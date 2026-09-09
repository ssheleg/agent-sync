#!/usr/bin/env python3
"""FIX-SY-04.02 — write-mode documentation (sherlock audit, second leaf of
SY-04, on FIX-SY-04.01's resource identity).

A doc leaf with a behavioural spine: the SKILL.md must now describe the two
write modes — the short transaction lock (per-file resource claim) and the
isolated worktree + merge — and must stop promising enforcement from a
single task owner. The agent-stack memory-architecture note that quotes
agent-sync's guarantee is corrected in the same change. The recipes are then
run as behaviour against the shipped module: a shared registry write takes a
resource claim, ordinary isolated code needs no global lease.

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
SKILL = os.path.join(HERE, os.pardir, os.pardir, "plugins", "agent-sync", "skills",
                     "agent-sync", "SKILL.md")
SCRIPT = os.path.join(HERE, os.pardir, os.pardir, "plugins", "agent-sync", "skills",
                      "agent-sync", "scripts", "agent_sync.py")
STACK_DOC = os.path.expanduser(
    "~/DATA/agent-stack/plugins/agent-stack/skills/agent-orchestrator/"
    "references/memory-architecture.md")

_spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

failures = []
not_run = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def t_skill_documents_two_modes():
    flat = " ".join(open(SKILL, encoding="utf-8").read().split())
    for needle in ("Two write modes",
                   "A task lease authorizes the TASK, never the file",
                   "short transaction lock",
                   "resource claim",
                   "isolated worktree + merge",
                   "does NOT promise is enforcement from a single task owner"):
        assert needle in flat, f"SKILL.md no longer states {needle!r}"
    assert "One lease covers every guarded file; hold one or write none." not in flat, \
        "the single-lease-covers-everything promise survived"


def t_stack_note_is_corrected_where_present():
    if not os.path.isfile(STACK_DOC):
        not_run.append("agent-stack not checked out here — the cross-repo note "
                       "is NOT_RUN on this machine (never a PASS)")
        return
    flat = " ".join(open(STACK_DOC, encoding="utf-8").read().split())
    assert "via a per-file **resource claim**" in flat and \
        "a task lease authorizes the task and not the file (SY-04)" in flat, \
        "the agent-stack note still overpromises agent-sync's guarantee"


# ------------- the recipes, run as behaviour


def project(guarded):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    cfg = {"backend": "fs", "gated": True, "leaseTtlSeconds": 600,
           "renewIntervalSeconds": 60, "idRegisters": {},
           "guardedFiles": guarded, "claimTags": {}, "gates": [],
           "mirror": {"enabled": False, "sources": []}}
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    os.makedirs(os.path.join(d, "registry"))
    open(os.path.join(d, "registry", "decisions.md"), "w").write("# reg\n")
    os.makedirs(os.path.join(d, "src"))
    open(os.path.join(d, "src", "util.py"), "w").write("x = 1\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


def sync_as(d, rid):
    os.chdir(d)
    s = A.Sync()
    s.rid = rid
    return s


def quiet(fn):
    buf, o, e = io.StringIO(), sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        return fn()
    finally:
        sys.stdout, sys.stderr = o, e


def t_shared_registry_recipe_takes_a_resource_claim():
    d = project(["registry/*.md"])
    s = sync_as(d, "r-shared")
    quiet(lambda: s.acquire("TASK"))
    quiet(lambda: s.guard(os.path.join(d, "registry", "decisions.md")))
    res = s.resource_key(os.path.join(d, "registry", "decisions.md"))
    assert res in s.held(), \
        "the shared-registry recipe did not take the file's resource claim"


def t_ordinary_isolated_code_needs_no_lease():
    d = project(["registry/*.md"])       # src/ is NOT guarded
    s = sync_as(d, "r-solo")
    allowed, reason = quiet(lambda: s.guard(os.path.join(d, "src", "util.py")))
    assert allowed and "not a guarded file" in reason, \
        f"ordinary isolated code demanded a lease: {reason}"


def main():
    case("SKILL.md documents both write modes, drops the single-owner promise",
         t_skill_documents_two_modes)
    case("the agent-stack note is corrected where present",
         t_stack_note_is_corrected_where_present)
    case("the shared-registry recipe takes a resource claim",
         t_shared_registry_recipe_takes_a_resource_claim)
    case("ordinary isolated code needs no global lease",
         t_ordinary_isolated_code_needs_no_lease)
    for n in not_run:
        print(f"  NOT_RUN  {n}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
