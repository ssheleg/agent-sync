#!/usr/bin/env python3
"""FIX-SY-03.02 — fence-aware renewal (sherlock audit, parent FIX-SY-03).

The finding: the local renew rewrote the lock's timestamp on nothing more
than a run-id match — but a replacement session SHARES the run id and the
checkout, so after its steal (generation bump) the old session's heartbeat
kept resurrecting the lease, and both sessions proceeded as exclusive
owners. And a renew of an already-EXPIRED lease quietly brought it back
from the dead under a stealer's feet.

The fix under test, with a virtual clock injected into the module:

* a valid current renew still passes and moves the timestamp;
* after a same-run-id steal, the OLD session's renew loses to the
  generation fence and names both generations;
* a renew of an expired lease refuses ("acquire it again") and does not
  resurrect it — and acquire() on one's own expired lease is a STEAL with
  a generation bump, not a refresh;
* a different-run steal still refuses the old owner outright.

Standard library only; real project directories, fake time.
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


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0
        self._t, self._n = A.time.time, A.now_iso

    def __enter__(self):
        A.time.time = lambda: self.now
        A.now_iso = lambda: A.datetime.fromtimestamp(
            self.now, A.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self

    def __exit__(self, *exc):
        A.time.time, A.now_iso = self._t, self._n


def project(ttl=600):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    cfg = {"backend": "fs", "gated": True, "leaseTtlSeconds": ttl,
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


def lock_of(d, key):
    with open(os.path.join(d, ".agent-sync", "leases", f"{key}.lock")) as fh:
        return json.load(fh)


def quiet(fn):
    err, real = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        out = fn()
    finally:
        sys.stderr = real
    return out, err.getvalue()


def t_valid_renew_passes():
    d = project()
    with Clock() as clock:
        s = sync_as(d, "r-alive")
        assert s.acquire("KEY")[0]
        clock.now += 120
        assert s.renew("KEY") is True
        assert A.parse_iso(lock_of(d, "KEY")["ts"]) == int(clock.now) or \
            abs(A.parse_iso(lock_of(d, "KEY")["ts"]) - clock.now) < 2, \
            "a valid renew moved no timestamp"


def t_zombie_session_loses_to_the_generation_fence():
    d = project()
    with Clock() as clock:
        old = sync_as(d, "r-shared")
        assert old.acquire("KEY")[0]
        gen1 = lock_of(d, "KEY")["gen"]
        clock.now += 700                      # past the 600s TTL
        new = sync_as(d, "r-shared")          # replacement session, SAME run id
        won, _ = new.acquire("KEY")
        assert won, "the replacement could not take its own expired lease"
        assert lock_of(d, "KEY")["gen"] == gen1 + 1, \
            "re-taking an expired own lease did not bump the generation"
        ts_after_steal = lock_of(d, "KEY")["ts"]
        clock.now += 30
        out, msg = quiet(lambda: old.renew("KEY"))
        assert out is False, \
            "the old session renewed the stolen lease — the finding itself"
        assert "generation" in msg and str(gen1) in msg and str(gen1 + 1) in msg, \
            f"the refusal does not name both generations: {msg!r}"
        assert lock_of(d, "KEY")["ts"] == ts_after_steal, \
            "the old session's renew moved the new owner's timestamp"
        clock.now += 30
        assert new.renew("KEY") is True, "the CURRENT owner's renew was refused"


def t_expired_lease_is_not_resurrected():
    d = project()
    with Clock() as clock:
        s = sync_as(d, "r-sleeper")
        assert s.acquire("KEY")[0]
        stale_ts = lock_of(d, "KEY")["ts"]
        clock.now += 700
        out, msg = quiet(lambda: s._refresh_lease("KEY"))
        assert out is False and "acquire it again" in msg, \
            f"an expired lease was renewed: {msg!r}"
        assert lock_of(d, "KEY")["ts"] == stale_ts, \
            "the expired lease's timestamp moved — it was resurrected"


def t_foreign_steal_still_refuses_the_old_owner():
    d = project()
    with Clock() as clock:
        a = sync_as(d, "r-owner-a")
        assert a.acquire("KEY")[0]
        clock.now += 700
        b = sync_as(d, "r-owner-b")
        assert b.acquire("KEY")[0], "the stealer could not take an expired lease"
        out, _ = quiet(lambda: a.renew("KEY"))
        assert out is False, "the old owner renewed another run's lease"


def main():
    case("a valid current renew passes and moves the timestamp", t_valid_renew_passes)
    case("a zombie session (same run id) loses to the generation fence",
         t_zombie_session_loses_to_the_generation_fence)
    case("an expired lease is refused, not resurrected", t_expired_lease_is_not_resurrected)
    case("a foreign steal still refuses the old owner", t_foreign_steal_still_refuses_the_old_owner)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
