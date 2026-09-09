#!/usr/bin/env python3
"""FIX-SY-04.01 — resource identity (sherlock audit, parent FIX-SY-04).

The finding: guard(path) allowed a write whenever the run held ANY task
lease — honestly documented as "one lease covers every guarded file", but
two runs holding two different task ids both passed and interleaved writes
to one shared registry.

The fix under test, on real project directories: the resource key is
canonical repo identity + canonical path (never the task id); a guarded
write auto-claims the FILE's own key under the task lease, so a single
agent feels nothing; two tasks on one file conflict — the second guard
refuses naming the file's claim; two tasks on two different files never
serialize; and releasing the last task key releases the run's resource
claims with it.

Standard library only.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, os.pardir, os.pardir, "plugins", "agent-sync", "skills",
                      "agent-sync", "scripts", "agent_sync.py")

_spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

failures = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def project():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    cfg = {"backend": "fs", "gated": True, "leaseTtlSeconds": 600,
           "renewIntervalSeconds": 60, "idRegisters": {},
           "guardedFiles": ["registry/*.md"], "claimTags": {}, "gates": [],
           "mirror": {"enabled": False, "sources": []}}
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    os.makedirs(os.path.join(d, "registry"))
    open(os.path.join(d, "registry", "decisions.md"), "w").write("# reg\n")
    open(os.path.join(d, "registry", "roadmap.md"), "w").write("# reg\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


def sync_as(d, rid):
    os.chdir(d)
    s = A.Sync()
    s.rid = rid
    return s


def quiet(fn):
    out_err = io.StringIO()
    real_o, real_e = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = out_err
    try:
        return fn(), out_err.getvalue()
    finally:
        sys.stdout, sys.stderr = real_o, real_e


def t_single_agent_feels_nothing():
    d = project()
    a = sync_as(d, "r-solo")
    (won, _), _o = quiet(lambda: a.acquire("TASK-A"))
    assert won
    (allowed, reason), _o = quiet(lambda: a.guard(os.path.join(d, "registry", "decisions.md")))
    assert allowed, f"a task-leased single agent was refused: {reason}"
    assert "resource claim taken" in reason
    (again, reason2), _o = quiet(lambda: a.guard(os.path.join(d, "registry", "decisions.md")))
    assert again and "resource claim held" in reason2, \
        "the second write of one run re-acquired instead of holding"


def t_two_tasks_one_file_conflict():
    d = project()
    a = sync_as(d, "r-writer-a")
    b = sync_as(d, "r-writer-b")
    quiet(lambda: a.acquire("TASK-A"))
    quiet(lambda: b.acquire("TASK-B"))
    (ok_a, _), _o = quiet(lambda: a.guard(os.path.join(d, "registry", "decisions.md")))
    assert ok_a
    (ok_b, reason_b), _o = quiet(lambda: b.guard(os.path.join(d, "registry", "decisions.md")))
    assert not ok_b, \
        "two different tasks both passed the guard on ONE file — the finding itself"
    assert "resource claim" in reason_b and "decisions" in reason_b, \
        f"the refusal does not name the file's claim: {reason_b}"


def t_different_files_never_serialize():
    d = project()
    a = sync_as(d, "r-writer-a")
    b = sync_as(d, "r-writer-b")
    quiet(lambda: a.acquire("TASK-A"))
    quiet(lambda: b.acquire("TASK-B"))
    (ok_a, _), _o = quiet(lambda: a.guard(os.path.join(d, "registry", "decisions.md")))
    (ok_b, reason_b), _o = quiet(lambda: b.guard(os.path.join(d, "registry", "roadmap.md")))
    assert ok_a and ok_b, \
        f"independent files were serialized without cause: {reason_b}"


def t_both_writes_land_in_turn():
    d = project()
    reg = os.path.join(d, "registry", "decisions.md")
    a = sync_as(d, "r-turn-a")
    b = sync_as(d, "r-turn-b")
    quiet(lambda: a.acquire("TASK-A"))
    quiet(lambda: b.acquire("TASK-B"))
    (ok, _), _o = quiet(lambda: a.guard(reg))
    assert ok
    open(reg, "a").write("- A's row\n")
    quiet(lambda: a.release("TASK-A"))
    (ok_b, reason), _o = quiet(lambda: b.guard(reg))
    assert ok_b, f"the claim did not free with the task release: {reason}"
    open(reg, "a").write("- B's row\n")
    text = open(reg).read()
    assert "- A's row" in text and "- B's row" in text, "an edit was lost"


def t_resource_key_is_repo_and_path():
    d = project()
    s = sync_as(d, "r-key")
    k1 = s.resource_key(os.path.join(d, "registry", "decisions.md"))
    k2 = s.resource_key(os.path.join(d, "registry", "roadmap.md"))
    assert k1.startswith("res--") and k1 != k2
    assert "decisions-md" in k1 and "TASK" not in k1, \
        f"the resource key leans on something other than the path: {k1}"


def main():
    case("a single task-leased agent feels nothing", t_single_agent_feels_nothing)
    case("two tasks on one file conflict, the refusal names the claim",
         t_two_tasks_one_file_conflict)
    case("two tasks on two different files never serialize",
         t_different_files_never_serialize)
    case("both writes land when taken in turn; release frees the claim",
         t_both_writes_land_in_turn)
    case("the resource key is repo identity + canonical path",
         t_resource_key_is_repo_and_path)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
