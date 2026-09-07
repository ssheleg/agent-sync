#!/usr/bin/env python3
"""FIX-SY-01.01 — Immutable reservation identity (sherlock audit, finding SY-01).

The finding: issued ids changed retroactively, and two reserves could return one
number. Allocation was *positional replay* over the merged log, so the value of an
already-issued id was a function of merge order — a shard arriving late, a base
appended afterwards, or clock skew re-seated numbers that were already printed on
someone's screen. Across machines it was worse: a shard another machine had not
pushed yet was invisible, so two machines replayed different logs and were both
"correct" about histories nobody shared.

The fix under test:

* a reserve line now CARRIES its value (a receipt) — replay never renumbers it,
  and a duplicate receipt loses instead of colliding;
* with `leaseBackend: "git"` the allocator is a compare-and-swap on a remote ref
  (`refs/agent-sync/ids/<REG>`) — two concurrent reserves cannot win one number;
* the retry on a lost race is bounded by RESERVE_RETRIES, never a spin.

Unit cases drive `resolve_reservations` directly; the concurrency cases drive the
shipped CLI as processes against a real bare remote, because the compare-and-swap
lives in `git push` semantics, not in this file's arithmetic.
"""
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile

# The import below must not leave bytecode in a tree people read as source (ASY-01).
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


def ev(op, run, value="", ts="2026-09-07T00:00:00Z", key="DEC"):
    d = {"op": op, "key": key, "run": run, "ts": ts}
    if value:
        d["value"] = value
    return d


# --------------------------------------------------------------- unit: replay


def t_receipt_is_immutable_under_late_shard():
    """A value-carrying reserve keeps its number when another shard arrives late
    with an earlier timestamp — the renumbering that was the finding."""
    log_a = [ev("base", "r-a", "0042"),
             ev("reserve", "r-a", "0042", ts="2026-09-07T00:00:01Z")]
    _b, _f, asg = A.resolve_reservations(log_a, "DEC")
    assert asg == [("r-a", 42)], f"seed replay broken: {asg}"

    late = [ev("base", "r-b", "0042", ts="2026-09-07T00:00:00Z"),
            ev("reserve", "r-b", "0043", ts="2026-09-07T00:00:00Z")]
    merged = sorted(late + log_a, key=lambda e: (e["ts"], e["run"]))
    _b, _f, asg = A.resolve_reservations(merged, "DEC")
    got = dict(asg)
    assert got.get("r-a") == 42, f"r-a was renumbered to {got.get('r-a')}"
    assert got.get("r-b") == 43, f"r-b lost its receipt: {got.get('r-b')}"


def t_receipts_stable_under_every_permutation():
    """Whatever order four receipt lines merge in, every issued id keeps its value."""
    lines = [ev("base", "r-a", "0010"),
             ev("reserve", "r-a", "0010"),
             ev("reserve", "r-b", "0011"),
             ev("reserve", "r-c", "0012")]
    want = {"r-a": 10, "r-b": 11, "r-c": 12}
    for perm in itertools.permutations(lines):
        _b, _f, asg = A.resolve_reservations(list(perm), "DEC")
        got = dict(asg)
        assert got == want, f"permutation moved a value: {got} from {[e['run'] for e in perm]}"


def t_late_base_does_not_renumber_receipts():
    """A base appended after a receipt moves the positional count, never the receipt."""
    log = [ev("base", "r-a", "0010", ts="2026-09-07T00:00:00Z"),
           ev("reserve", "r-a", "0010", ts="2026-09-07T00:00:01Z"),
           ev("base", "r-x", "0050", ts="2026-09-07T00:00:02Z"),
           ev("reserve", "r-b", ts="2026-09-07T00:00:03Z")]      # bare legacy line
    _b, _f, asg = A.resolve_reservations(log, "DEC")
    got = dict(asg)
    assert got["r-a"] == 10, f"receipt renumbered by a later base: {got['r-a']}"
    assert got["r-b"] == 50, f"legacy reserve ignored the re-base: {got['r-b']}"


def t_duplicate_receipt_single_winner():
    """Two receipts claiming one number: first in the merged order keeps it, the
    second gets nothing (its run retried and appended another line)."""
    log = [ev("base", "r-a", "0042"),
           ev("reserve", "r-a", "0042", ts="2026-09-07T00:00:01Z"),
           ev("reserve", "r-b", "0042", ts="2026-09-07T00:00:02Z")]
    _b, _f, asg = A.resolve_reservations(log, "DEC")
    assert asg == [("r-a", 42)], f"duplicate receipt was not resolved: {asg}"


def t_released_receipt_value_can_be_reissued():
    """release_id returns a receipt's number to circulation: a later receipt for the
    same number is honoured, not treated as a duplicate."""
    log = [ev("base", "r-a", "0005"),
           ev("reserve", "r-a", "0005", ts="2026-09-07T00:00:01Z"),
           ev("release_id", "r-a", "0005", ts="2026-09-07T00:00:02Z"),
           ev("reserve", "r-b", "0005", ts="2026-09-07T00:00:03Z")]
    _b, _f, asg = A.resolve_reservations(log, "DEC")
    assert asg == [("r-a", 5), ("r-b", 5)], f"reissue after release broken: {asg}"


def t_receipt_advances_positional_count():
    """A bare legacy reserve after a receipt lands PAST it, never on top of it."""
    log = [ev("base", "r-a", "0010"),
           ev("reserve", "r-a", "0012", ts="2026-09-07T00:00:01Z"),
           ev("reserve", "r-b", ts="2026-09-07T00:00:02Z")]
    _b, _f, asg = A.resolve_reservations(log, "DEC")
    got = dict(asg)
    assert got["r-a"] == 12
    assert got["r-b"] == 13, f"legacy reserve collided with or skipped past a receipt: {got['r-b']}"


def t_legacy_positional_unchanged():
    """Bare lines from existing logs still resolve exactly as before the change."""
    log = [ev("base", "r-a", "0216"),
           ev("reserve", "r-a"),
           ev("reserve", "r-b", ts="2026-09-07T00:00:01Z"),
           ev("release_id", "r-a", "0216", ts="2026-09-07T00:00:02Z"),
           ev("reserve", "r-c", ts="2026-09-07T00:00:03Z")]
    _b, free, asg = A.resolve_reservations(log, "DEC")
    assert asg == [("r-a", 216), ("r-b", 217), ("r-c", 216)], f"legacy replay moved: {asg}"
    assert free == [], f"free list wrong: {free}"


# ------------------------------------------------- end to end: the CLI + git CAS


def mkproject():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "decisions.md"), "w") as fh:
        fh.write("# Decisions\n\nnext free id: DEC-0042\n")
    cfg = {
        "backend": "fs",
        "leaseBackend": "git",
        "leaseRemote": "origin",
        "gated": True,
        "idRegisters": {"DEC": {"file": "docs/decisions.md",
                                "pattern": r"next free id: DEC-(\d+)"}},
        "guardedFiles": [], "claimTags": {}, "gates": [],
        "mirror": {"enabled": False, "sources": []},
    }
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    bare = tempfile.mkdtemp(suffix=".git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    subprocess.run(["git", "-C", d, "remote", "add", "origin", bare], check=True)
    return d


def cli(d, *args, rid=None):
    env = dict(os.environ)
    if rid:
        env["AGENT_SYNC_RUN_ID"] = rid
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=d,
                          capture_output=True, text=True, env=env)


def issued(d, reg="DEC"):
    """Replay every shard, merged — through the tool's own reader, so the test sees
    exactly the view every other reader gets."""
    os.chdir(d)
    events, _bad = A.Sync().events("reservations")
    _b, _f, asg = A.resolve_reservations(events, reg)
    return asg


def t_concurrent_reserve_distinct_ids():
    """Two reserves racing for one register get two different numbers, and both
    are receipts at or past the register floor."""
    d = mkproject()
    procs = [subprocess.Popen(
        [sys.executable, SCRIPT, "reserve", "DEC"], cwd=d,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, AGENT_SYNC_RUN_ID=rid))
        for rid in ("r-race-aaa", "r-race-bbb")]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"reserve failed: {err.strip()[:300]}"
        outs.append(out.strip())
    values = sorted(int(o.split("-")[-1]) for o in outs)
    assert values[0] != values[1], f"two reserves returned one number: {outs}"
    assert values[0] >= 42, f"allocation ignored the register floor: {outs}"
    got = dict(issued(d))
    assert set(got.values()) == set(values), f"log receipts disagree with printed ids: {got} vs {values}"


def t_issued_id_never_renumbered_by_later_activity():
    """An id already printed keeps its value through later reserves and a register
    that moved forward by hand — replayed from the merged log, not remembered."""
    d = mkproject()
    r1 = cli(d, "reserve", "DEC", rid="r-first")
    assert r1.returncode == 0, r1.stderr[:300]
    first = int(r1.stdout.strip().split("-")[-1])
    holders = [r for r, v in issued(d) if v == first]
    assert len(holders) == 1, f"the printed id has no single receipt: {holders}"
    owner = holders[0]

    # The register grows by a hand edit — the classic re-base trigger.
    with open(os.path.join(d, "docs", "decisions.md"), "w") as fh:
        fh.write("# Decisions\n\nnext free id: DEC-0090\n")
    r2 = cli(d, "reserve", "DEC", rid="r-second")
    assert r2.returncode == 0, r2.stderr[:300]
    second = int(r2.stdout.strip().split("-")[-1])
    assert second >= 90, f"floor was not honoured after the hand edit: {second}"

    after = dict(issued(d))
    assert after.get(owner) == first, \
        f"issued id was renumbered: was {first}, replay now says {after.get(owner)}"
    assert second in after.values(), f"second receipt missing from replay: {after}"
    assert first != second


def t_cas_loss_is_retried_and_bounded():
    """A push that loses the compare-and-swap re-reads and takes the next number;
    a remote that NEVER stops moving is reported after RESERVE_RETRIES, not spun on."""
    d = mkproject()
    os.chdir(d)
    sync = A.Sync()
    sync.rid = "r-cas-test"

    real_run = subprocess.run
    state = {"pushes": 0, "inject": True}

    def racing_run(cmd, **kw):
        if isinstance(cmd, list) and cmd[:2] == ["git", "push"] and "refs/agent-sync/ids/" in " ".join(cmd):
            state["pushes"] += 1
            if state["inject"] and state["pushes"] == 1:
                # Another machine wins between our read and our push.
                interloper = real_run(
                    ["git", "-c", "user.name=x", "-c", "user.email=x@x",
                     "commit-tree", real_run(["git", "hash-object", "-t", "tree", os.devnull],
                                             capture_output=True, text=True).stdout.strip()],
                    input=json.dumps({"reg": "DEC", "next": 43, "run": "r-interloper"}),
                    capture_output=True, text=True).stdout.strip()
                real_run(["git", "push", "-q", "origin",
                          f"{interloper}:refs/agent-sync/ids/DEC"],
                         capture_output=True, text=True)
        return real_run(cmd, **kw)

    A.subprocess.run = racing_run
    try:
        value = sync._git_reserve_id("DEC", 42)
    finally:
        A.subprocess.run = real_run
    assert state["pushes"] >= 2, "the CAS loss was never exercised"
    assert value == 43, f"after losing the race the allocator took {value}, expected the moved tip's 43"

    # Bounded retry: a remote that always rejects is reported, never spun on.
    def always_lose(cmd, **kw):
        if isinstance(cmd, list) and cmd[:2] == ["git", "push"] and "refs/agent-sync/ids/" in " ".join(cmd):
            state["pushes"] += 1
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rejected (test)")
        return real_run(cmd, **kw)

    state["pushes"] = 0
    A.subprocess.run = always_lose
    try:
        sync._git_reserve_id("DEC", 42)
        raise AssertionError("an always-losing CAS did not raise")
    except A.Fail as e:
        assert str(A.RESERVE_RETRIES) in str(e), f"failure does not name the bound: {e}"
    finally:
        A.subprocess.run = real_run
    assert state["pushes"] == A.RESERVE_RETRIES, \
        f"retry is not bounded at RESERVE_RETRIES: {state['pushes']} pushes"


def main():
    case("receipt is immutable under a late shard", t_receipt_is_immutable_under_late_shard)
    case("receipts stable under every merge permutation", t_receipts_stable_under_every_permutation)
    case("a late base never renumbers a receipt", t_late_base_does_not_renumber_receipts)
    case("duplicate receipt: exactly one winner", t_duplicate_receipt_single_winner)
    case("released value can be reissued by receipt", t_released_receipt_value_can_be_reissued)
    case("a receipt advances the positional count", t_receipt_advances_positional_count)
    case("legacy bare lines resolve as before", t_legacy_positional_unchanged)
    case("concurrent reserve: two distinct ids (real git CAS)", t_concurrent_reserve_distinct_ids)
    case("an issued id is never renumbered", t_issued_id_never_renumbered_by_later_activity)
    case("CAS loss retries, bounded at RESERVE_RETRIES", t_cas_loss_is_retried_and_bounded)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
