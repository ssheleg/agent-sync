#!/usr/bin/env python3
"""FIX-SY-02.01 — the renew throttle is per run and per key (sherlock audit).

The finding: one shared `last-renew` file per checkout throttled EVERY run's
renewals together. Run A touching it every hundred seconds meant run B's
heartbeat read "renewed recently" for forty-five minutes, refreshed nothing,
and B's lease expired under work in progress. And `acquire` stamped the same
shared marker, so unrelated activity replaced the renewal time of leases it
never touched.

The fix under test, with a virtual clock injected into the module:

* A active every 100s for 45 simulated minutes; B holds a lease and heartbeats
  every 300s — B's lease timestamp keeps moving and never crosses its TTL;
* an explicit `renew <key>` really refreshes even seconds after the last one,
  or answers with the precise per-key reason;
* one run's marker never suppresses another run's heartbeat, and an acquire
  stamps only its own key's marker.

Standard library only; real project directories, fake time.
"""
import importlib.util
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
    """One fake clock wired into time.time AND now_iso, so the marker bytes and
    the throttle compare against the same instant."""

    def __init__(self):
        self.now = 1_700_000_000.0
        self._real_time = A.time.time
        self._real_now_iso = A.now_iso

    def __enter__(self):
        A.time.time = lambda: self.now
        A.now_iso = lambda: A.datetime.fromtimestamp(
            self.now, A.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self

    def __exit__(self, *exc):
        A.time.time = self._real_time
        A.now_iso = self._real_now_iso


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


def lock_ts(d, key):
    with open(os.path.join(d, ".agent-sync", "leases", f"{key}.lock")) as fh:
        return json.load(fh)["ts"]


def t_a_cannot_starve_b():
    d = project()
    with Clock() as clock:
        a = sync_as(d, "r-agent-a")
        b = sync_as(d, "r-agent-b")
        assert a.acquire("KEY-A")[0] and b.acquire("KEY-B")[0]
        refreshes = 0
        start = clock.now
        while clock.now - start < 45 * 60:
            clock.now += 100
            a.renew()                                # A's activity, every 100s
            if (clock.now - start) % 300 < 100:      # B's hook cadence, ~every 300s
                if b.renew():
                    refreshes += 1
            ts = A.parse_iso(lock_ts(d, "KEY-B"))
            assert clock.now - ts < 2700, \
                f"B's lease expired at t+{int(clock.now - start)}s while B was working"
        assert refreshes >= 6, \
            f"B refreshed only {refreshes} times in 45 minutes — still being throttled"


def t_explicit_renew_refreshes_or_names_the_reason():
    d = project()
    with Clock() as clock:
        s = sync_as(d, "r-explicit")
        assert s.acquire("KEY-X")[0]
        clock.now += 5                           # seconds after acquire, well inside the interval
        assert s.renew("KEY-X") is True, \
            "an explicit renew hid behind the heartbeat throttle"
        ts_after = A.parse_iso(lock_ts(d, "KEY-X"))
        assert abs(ts_after - clock.now) < 2, "the explicit renew moved no timestamp"

    d2 = project()
    with Clock():
        s2 = sync_as(d2, "r-nobody")
        import io
        err = io.StringIO()
        real = sys.stderr
        sys.stderr = err
        try:
            out = s2.renew("KEY-GHOST")
        finally:
            sys.stderr = real
        assert out is False
        msg = err.getvalue()
        assert "KEY-GHOST" in msg and "does not hold" in msg, \
            f"the per-key reason is missing: {msg!r}"


def t_acquire_stamps_only_its_own_key():
    d = project()
    with Clock() as clock:
        s = sync_as(d, "r-two-keys")
        assert s.acquire("KEY-1")[0]
        clock.now += 400                         # KEY-1's marker is now stale
        assert s.acquire("KEY-2")[0]             # unrelated acquire
        age1 = s._renew_age("KEY-1")
        age2 = s._renew_age("KEY-2")
        assert age1 is not None and age1 >= 400, \
            f"an unrelated acquire replaced KEY-1's renewal time (age {age1})"
        assert age2 is not None and age2 < 5
        assert s.renew() is True, "the heartbeat skipped a key whose marker was stale"


def main():
    case("A active every 100s cannot starve B's 45-minute lease", t_a_cannot_starve_b)
    case("explicit renew refreshes for real or names the per-key reason",
         t_explicit_renew_refreshes_or_names_the_reason)
    case("an acquire stamps only its own key's marker", t_acquire_stamps_only_its_own_key)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
