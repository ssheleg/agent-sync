#!/usr/bin/env python3
"""FIX-SY-05.01 — no empty-lock window, no two winners (sherlock audit, SY-05).

The finding: a lock's NAME was created atomically (O_EXCL) but its CONTENT was
written later. A competitor reading the lock between the two saw empty JSON,
parsed it as {}, judged it not-live and stole it via _steal_expired — while
the first writer kept writing into a now-unlinked inode and also returned won.
Two winners.

The fix under test: the lock is PUBLISHED already filled, atomically
(temp + fsync + os.link no-replace), so a reader never sees an empty lock;
and an empty/partial lock that does appear is a creation-in-flight within a
grace, arbitrated by the file's age, never stolen for being empty JSON.

Standard library only; real project dirs.
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
           "renewIntervalSeconds": 60, "idRegisters": {}, "guardedFiles": [],
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


def lock_path(d, key):
    return os.path.join(d, ".agent-sync", "leases", f"{key}.lock")


def quiet(fn):
    o, e = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = io.StringIO()
    try:
        return fn()
    finally:
        sys.stdout, sys.stderr = o, e


def t_published_lock_is_never_empty():
    d = project()
    a = sync_as(d, "r-a")
    won, _ = quiet(lambda: a.acquire("KEY"))
    assert won
    with open(lock_path(d, "KEY")) as fh:
        body = json.load(fh)
    assert body.get("run") == "r-a" and body.get("gen") == 1, \
        f"the published lock is not fully formed: {body}"


def t_empty_lock_within_grace_is_not_stolen():
    d = project()
    a = sync_as(d, "r-a")
    b = sync_as(d, "r-b")
    # Simulate the window: an empty lock a creator just made (fresh mtime).
    path = lock_path(d, "KEY")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()                       # empty, mtime = now
    stole = quiet(lambda: b._steal_expired(A.Path(path),
                  json.dumps({"run": "r-b", "ts": A.now_iso(), "ttl": 600})))
    assert stole is False, \
        "an empty lock inside the creation grace was stolen — the finding itself"


def t_empty_lock_past_grace_is_reclaimed():
    d = project()
    b = sync_as(d, "r-b")
    path = lock_path(d, "KEY")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    old = os.stat(path).st_mtime - (A.Sync.CREATE_GRACE_SECONDS + 30)
    os.utime(path, (old, old))                    # an abandoned mid-create crash
    stole = quiet(lambda: b._steal_expired(A.Path(path),
                  json.dumps({"run": "r-b", "ts": A.now_iso(), "ttl": 600})))
    assert stole is True, "an abandoned empty lock was never reclaimed — a forever-lock"
    with open(path) as fh:
        assert json.load(fh).get("run") == "r-b"


def t_second_acquire_never_wins():
    d = project()
    a = sync_as(d, "r-a")
    b = sync_as(d, "r-b")
    won_a, _ = quiet(lambda: a.acquire("KEY"))
    won_b, holder = quiet(lambda: b.acquire("KEY"))
    assert won_a and not won_b, \
        f"two acquires both won: a={won_a} b={won_b}"
    assert holder == "r-a", f"the loser was told the wrong holder: {holder}"


def t_publish_refuses_to_replace():
    d = project()
    a = sync_as(d, "r-a")
    path = A.Path(lock_path(d, "KEY"))
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    assert a._publish_lock(path, {"run": "r-a", "gen": 1}) is True
    assert a._publish_lock(path, {"run": "r-b", "gen": 9}) is False, \
        "_publish_lock replaced an existing lock — it must be no-replace"
    with open(str(path)) as fh:
        assert json.load(fh)["run"] == "r-a", "the second publish overwrote the first"


def main():
    case("a published lock is never empty", t_published_lock_is_never_empty)
    case("an empty lock inside the creation grace is not stolen",
         t_empty_lock_within_grace_is_not_stolen)
    case("an empty lock past the grace is reclaimed (no forever-lock)",
         t_empty_lock_past_grace_is_reclaimed)
    case("a second acquire never wins", t_second_acquire_never_wins)
    case("_publish_lock is atomic no-replace", t_publish_refuses_to_replace)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
