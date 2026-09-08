#!/usr/bin/env python3
"""FIX-SY-01.02 — allocator receipts and offline mapping (sherlock audit, SY-01,
second leaf; depends on FIX-SY-01.01's immutable receipts and git CAS).

The contract under test:

* a receipt names its authority — backend, revision, reservation key — in the
  log line the allocation writes;
* a RETRY of one reservation key never issues a second number: not when the
  receipt landed (the merged-log short-circuit), and not when the run died
  between winning the CAS and writing the receipt (the counter chain remembers
  which key each number was served to);
* an offline id is a namespaced composite (`REG-o-<run>-<seq>`) that cannot
  collide with the numeric global sequence, and its later mapping onto a real
  number is append-only — the same fact twice is fine, a different number is
  refused, a mapping for an id never issued is refused.

Driven against the shipped CLI and module on a real bare remote.
Standard library only.
"""
import importlib.util
import json
import os
import re
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
        "backend": "fs", "leaseBackend": "git", "leaseRemote": "origin", "gated": True,
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


def shard_text(d):
    out = []
    base = os.path.join(d, ".agent-sync")
    for name in sorted(os.listdir(base)):
        if name.endswith(".md"):
            with open(os.path.join(base, name)) as fh:
                out.append(fh.read())
    return "\n".join(out)


def t_receipt_names_its_authority():
    d = mkproject()
    r = cli(d, "reserve", "DEC", "--key", "rk-alpha", rid="r-recv")
    assert r.returncode == 0, r.stderr[:300]
    text = shard_text(d)
    line = next(l for l in text.splitlines() if "op=reserve" in l and "rk-alpha" in l)
    assert "`backend=git`" in line, f"the receipt does not name its backend: {line}"
    assert re.search(r"`rev=[0-9a-f]{12}`", line), f"the receipt carries no revision: {line}"


def t_retry_of_one_key_returns_one_number():
    d = mkproject()
    first = cli(d, "reserve", "DEC", "--key", "rk-one", rid="r-retry")
    second = cli(d, "reserve", "DEC", "--key", "rk-one", rid="r-retry")
    assert first.stdout.strip() == second.stdout.strip() == "DEC-0042", \
        f"one key, two answers: {first.stdout.strip()!r} vs {second.stdout.strip()!r}"
    other = cli(d, "reserve", "DEC", "--key", "rk-two", rid="r-retry")
    assert other.stdout.strip() == "DEC-0043", \
        f"a fresh key did not advance: {other.stdout.strip()!r}"


def t_crash_between_cas_and_receipt_is_recovered():
    """The crash window: the CAS won, the process died before the log line. The
    retry with the same key reads its allocation out of the counter chain."""
    d = mkproject()
    os.chdir(d)
    os.environ["AGENT_SYNC_RUN_ID"] = "r-crash"
    try:
        sync = A.Sync()
        value, rev = sync._git_reserve_id("DEC", 42, rkey="rk-crash")   # no receipt written
        assert (value, len(rev)) == (42, 40), f"seed allocation surprised: {value}, {rev!r}"
        retry = sync.reserve("DEC", rkey="rk-crash")                    # the full path
        assert retry == 42, f"the retry after the crash took a second number: {retry}"
        fresh = sync.reserve("DEC", rkey="rk-later")
        assert fresh == 43, f"the next reservation did not continue past the recovered one: {fresh}"
    finally:
        os.environ.pop("AGENT_SYNC_RUN_ID", None)


def t_offline_id_cannot_collide_with_the_sequence():
    d = mkproject()
    a = cli(d, "reserve", "DEC", "--offline", rid="r-off")
    b = cli(d, "reserve", "DEC", "--offline", rid="r-off")
    ids = [a.stdout.strip(), b.stdout.strip()]
    assert ids[0] != ids[1], f"two offline reserves returned one id: {ids}"
    for oid in ids:
        assert re.fullmatch(r"DEC-o-[a-z0-9]+-\d{3}", oid), \
            f"offline id {oid!r} is not namespaced"
        assert not re.fullmatch(r"DEC-\d+", oid), \
            f"offline id {oid!r} is shaped like the global sequence"


def t_offline_mapping_is_append_only():
    d = mkproject()
    oid = cli(d, "reserve", "DEC", "--offline", rid="r-map").stdout.strip()
    number = cli(d, "reserve", "DEC", rid="r-map").stdout.strip().split("-")[-1]
    ok = cli(d, "map-offline", "DEC", oid, number, rid="r-map")
    assert ok.returncode == 0, f"mapping refused: {ok.stderr[:200]}"
    again = cli(d, "map-offline", "DEC", oid, number, rid="r-map")
    assert again.returncode == 0, "the same fact twice was refused"
    rebind = cli(d, "map-offline", "DEC", oid, "9999", rid="r-map")
    assert rebind.returncode != 0 and "append-only" in (rebind.stdout + rebind.stderr), \
        "a different number for a mapped id was accepted"
    ghost = cli(d, "map-offline", "DEC", "DEC-o-nobody-001", "0050", rid="r-map")
    assert ghost.returncode != 0 and "never issued" in (ghost.stdout + ghost.stderr), \
        "a mapping for an id never issued was accepted"


def main():
    case("the receipt names backend, revision and key", t_receipt_names_its_authority)
    case("a retry of one reservation key returns one number", t_retry_of_one_key_returns_one_number)
    case("a crash between CAS and receipt is recovered by key", t_crash_between_cas_and_receipt_is_recovered)
    case("an offline id is namespaced away from the sequence", t_offline_id_cannot_collide_with_the_sequence)
    case("the offline mapping is append-only, both ways", t_offline_mapping_is_append_only)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
