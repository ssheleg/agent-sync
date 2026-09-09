#!/usr/bin/env python3
"""FIX-SY-03.01 — one local ownership critical section (sherlock audit, SY-03).

The finding: local acquire/renew/release/steal each ran their own
read-modify-write against the lock file. The steal path had its own O_EXCL
section, but a RENEW could rewrite the timestamp between the stealer's expiry
re-read and its create — two writers, one file, and a partial read away from
two owners.

The fix under test: every local writer enters ONE OS-backed section (the
.steal guard, now shared); a renew arriving while a steal is inside DEFERS
instead of racing; ownership changes bump a generation a renewal preserves;
a truncated lock never yields a second owner; and a host where the primitive
fails gets an explicit `unsupported` Fail, never an unlocked fallback.

Standard library only; real project directories.
"""
import importlib.util
import json
import os
import time
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
    cfg = {"backend": "fs", "gated": True, "leaseTtlSeconds": 2700,
           "renewIntervalSeconds": 300, "idRegisters": {}, "guardedFiles": [],
           "claimTags": {}, "gates": [], "mirror": {"enabled": False, "sources": []}}
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


def sync_as(d, rid):
    os.chdir(d)
    s = A.Sync()
    s.rid = rid
    return s


def lock_body(d, key):
    with open(os.path.join(d, ".agent-sync", "leases", f"{key}.lock")) as fh:
        return json.load(fh)


def t_renew_defers_while_a_steal_is_inside():
    d = project()
    a = sync_as(d, "r-owner")
    assert a.acquire("K1")[0]
    ts_before = lock_body(d, "K1")["ts"]
    # another run enters the section (a steal in progress)
    guard = os.path.join(d, ".agent-sync", "leases", "K1.lock.steal")
    with open(guard, "w"):
        pass
    try:
        out = a._refresh_lease("K1")
        assert out is False, "the renew barged into an occupied section"
        assert lock_body(d, "K1")["ts"] == ts_before, \
            "the deferred renew still rewrote the timestamp"
    finally:
        os.unlink(guard)
    assert a._refresh_lease("K1") is True, "the renew did not recover after the section freed"


def t_generation_moves_on_ownership_never_on_renewal():
    d = project()
    a = sync_as(d, "r-gen-a")
    assert a.acquire("K2")[0]
    assert lock_body(d, "K2")["gen"] == 1, "a fresh acquire did not seed generation 1"
    assert a._refresh_lease("K2")
    assert lock_body(d, "K2")["gen"] == 1, "a renewal moved the generation"
    # expire it, then B steals: the generation must move
    body = lock_body(d, "K2")
    body["ts"] = "2020-01-01T00:00:00Z"
    with open(os.path.join(d, ".agent-sync", "leases", "K2.lock"), "w") as fh:
        json.dump(body, fh)
    b = sync_as(d, "r-gen-b")
    assert b.acquire("K2")[0], "the expired lock was not stolen"
    assert lock_body(d, "K2")["gen"] == 2, \
        f"the steal did not bump the generation: {lock_body(d, 'K2')}"


def t_partial_read_never_yields_two_owners():
    d = project()
    lockdir = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(lockdir, exist_ok=True)
    torn = os.path.join(lockdir, "K3.lock")
    with open(torn, "w") as fh:
        fh.write('{"run": "r-half')          # a torn write
    # SY-05: a torn lock YOUNGER than the creation grace is a creation-in-flight
    # and is protected, not reaped. To test the reap of a genuinely abandoned
    # torn lock, age it past the grace — otherwise this asserts SY-05's own
    # protect-the-creator behaviour would be violated.
    old_mtime = time.time() - (A.Sync.CREATE_GRACE_SECONDS + 5)
    os.utime(torn, (old_mtime, old_mtime))
    a = sync_as(d, "r-reader")
    assert a._refresh_lease("K3") is False, "a torn lock was renewed as owned"
    assert a.acquire("K3")[0], "a torn lock could not be reaped and taken"
    assert lock_body(d, "K3")["run"] == "r-reader"


def t_unsupported_section_fails_loud():
    d = project()
    a = sync_as(d, "r-unsup")
    assert a.acquire("K4")[0]
    real_open = os.open

    def broken_open(path, *args, **kw):
        if str(path).endswith(".steal"):
            raise PermissionError("no exclusive create on this mount")
        return real_open(path, *args, **kw)

    A.os.open = broken_open
    try:
        try:
            a._refresh_lease("K4")
            raise AssertionError("an unsupported section fell back to unlocked")
        except A.Fail as e:
            assert "unsupported" in str(e) and "luck" in str(e), \
                f"the failure does not name the remedy: {e}"
    finally:
        A.os.open = real_open


def main():
    case("a renew defers while a steal is inside the section",
         t_renew_defers_while_a_steal_is_inside)
    case("the generation moves on ownership, never on renewal",
         t_generation_moves_on_ownership_never_on_renewal)
    case("a partial read never yields two owners", t_partial_read_never_yields_two_owners)
    case("an unsupported section fails loud, never unlocked",
         t_unsupported_section_fails_loud)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
