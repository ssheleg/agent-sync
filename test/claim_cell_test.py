#!/usr/bin/env python3
"""Fixtures for the claim cell and the id registers — driven through the real CLI.

Three defects, all found on 2026-08-14 in one afternoon of using this tool on a board
that cross-references itself:

* **B-42** — `claimTags mode: cell` collected every row *containing* the id, so any row
  citing another defeated the tag for the cited id. Cross-referencing is what a board is
  for: 14 rows of the umbrella's board cite others, and 19 of its 41 ids were untaggable.
  The tag refused, and the lease was granted anyway — coordination that reports success
  while writing nothing.
* **B-35** — release restored the claimed cell *verbatim*, and in this family the claim
  cell IS the status cell. So `close then release` silently reopened a row that had just
  been closed with evidence. Caught by `finish` reporting the board uncommitted, not by
  anybody reading it.
* **B-34** — the id-register pattern was read under a key no shipped config writes, and
  an absent key became `re.search("", text)`, which matches at position 0 — so the found
  branch ran and died with `IndexError` instead of saying the register has no pattern.

Each runs the shipped script as a process against a real project directory, because all
three are about what the command does to a file on disk.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, os.pardir, "plugins", "agent-sync", "skills", "agent-sync",
                      "scripts", "agent_sync.py")

failures = []

BOARD_HEAD = "| id | What | Status |\n|---|---|---|\n"


def project(rows, registers=None):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "board.md"), "w") as fh:
        fh.write(BOARD_HEAD + "".join(rows))
    cfg = {
        "backend": "fs",
        "gated": True,
        "idRegisters": registers or {},
        "guardedFiles": ["docs/board.md"],
        "claimTags": {"docs/board.md": {"mode": "cell", "cell": -1,
                                        "held": "{prev} (claimed: {holder})"}},
        "gates": [], "mirror": {"enabled": False, "sources": []},
    }
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


def cli(d, *args):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=d,
                          capture_output=True, text=True)


def board(d):
    with open(os.path.join(d, "docs", "board.md"), encoding="utf-8") as fh:
        return fh.read()


def status_of(d, key):
    for line in board(d).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == key:
            return cells[-1]
    return None


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def a_cited_id_is_still_taggable():
    """THE B-42 INCIDENT. B-01 owns one row and is cited by two others."""
    d = project([
        "| B-01 | the thing itself | open |\n",
        "| B-02 | blocked until B-01 lands | open |\n",
        "| B-03 | supersedes B-01 and B-02 | open |\n",
    ])
    r = cli(d, "acquire", "B-01")
    assert r.returncode == 0, f"acquire failed: {r.stderr}"
    assert "claimed:" in (status_of(d, "B-01") or ""), (
        f"the owning row was not tagged — {status_of(d, 'B-01')!r}; "
        f"stdout: {r.stdout.strip()[:200]}")
    assert "claimed:" not in (status_of(d, "B-02") or ""), "a citing row was tagged instead"
    assert "claimed:" not in (status_of(d, "B-03") or ""), "a citing row was tagged too"


def releasing_keeps_a_close_written_while_held():
    """THE B-35 INCIDENT. The claim cell and the status cell are the same cell."""
    d = project(["| B-10 | a thing | open |\n"])
    assert cli(d, "acquire", "B-10").returncode == 0
    held = status_of(d, "B-10")
    assert "claimed:" in held, held
    # the run closes the row with its evidence, exactly as stage 10 requires
    text = board(d).replace(held, "**closed 2026-08-14** — proven by X" + held[held.index(" (claimed:"):])
    with open(os.path.join(d, "docs", "board.md"), "w") as fh:
        fh.write(text)
    r = cli(d, "release", "B-10")
    assert r.returncode == 0, r.stderr
    after = status_of(d, "B-10") or ""
    assert "closed 2026-08-14" in after, f"releasing reverted the close: {after!r}"
    assert "claimed:" not in after, f"the claim marker survived the release: {after!r}"


def an_untouched_cell_is_restored_exactly():
    """The ordinary path must not change: nothing edited, so the cell comes back verbatim."""
    d = project(["| B-11 | a thing | open |\n"])
    assert cli(d, "acquire", "B-11").returncode == 0
    assert cli(d, "release", "B-11").returncode == 0
    assert status_of(d, "B-11") == "open", f"got {status_of(d, 'B-11')!r}"


def a_register_with_the_shipped_key_works():
    d = project(["| B-20 | a thing | open |\n"],
                registers={"B": {"file": "docs/board.md", "pattern": r"B-(\d+)"}})
    r = cli(d, "check")
    assert "no id pattern" not in r.stdout + r.stderr, "the shipped key was not accepted"
    assert "Traceback" not in r.stderr, f"check crashed: {r.stderr[-300:]}"


def a_register_with_no_pattern_reports_rather_than_crashes():
    """Standing instruction #1. An absent pattern became `re.search("", text)`, which
    matches at position 0, so the found branch ran and raised `IndexError`."""
    d = project(["| B-21 | a thing | open |\n"],
                registers={"B": {"file": "docs/board.md"}})
    r = cli(d, "check")
    assert "Traceback" not in r.stderr, f"check crashed instead of reporting: {r.stderr[-300:]}"
    assert "IndexError" not in r.stderr, "the empty-pattern crash is still reachable"
    assert re.search(r"no id pattern", r.stdout + r.stderr), (
        f"the missing pattern was not reported: {(r.stdout + r.stderr)[-300:]}")


def a_genuinely_ambiguous_id_still_refuses():
    """Ownership narrows a citation, not a real duplicate. Two rows both OWNING `B-30`
    is a broken board, and guessing which is the claim is how a tag lands on the wrong
    row — the failure the refusal exists for."""
    d = project([
        "| B-30 | one | open |\n",
        "| B-30 | two | open |\n",
    ])
    r = cli(d, "acquire", "B-30")
    out = r.stdout + r.stderr
    assert "refusing to guess" in out or "2 table rows" in out, (
        f"a duplicated id was tagged without complaint: {out[-300:]}")


def an_expired_foreign_lease_can_be_reaped():
    """THE #4 INCIDENT. A lease from a run that died is expired for `acquire` and
    was eternal for `release`, so no command could clear it — measured in the field
    at 604x its TTL, released only by deleting the file by hand."""
    d = project(["| B-01 | the thing itself | open |\n"])
    lock = os.path.join(d, ".agent-sync", "leases", "B-01.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    # A dead run's lease, written 10 days ago with a 2700s TTL.
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 864000))
    with open(lock, "w") as fh:
        json.dump({"run": "r-deadbeef", "ts": old, "ttl": 2700}, fh)
    r = cli(d, "release", "B-01")
    assert r.returncode == 0, f"still refused: {r.stdout}{r.stderr}"
    assert "reaped" in r.stdout, f"reaped silently: {r.stdout!r}"
    assert "r-deadbeef" in r.stdout, f"did not name whose lease: {r.stdout!r}"
    assert not os.path.exists(lock), "the lock survived the reap"


def a_live_foreign_lease_is_still_refused():
    """The direction that must NOT change: a lease inside its TTL belongs to
    somebody, and reaping it is the collision a lease exists to prevent."""
    d = project(["| B-01 | the thing itself | open |\n"])
    lock = os.path.join(d, ".agent-sync", "leases", "B-01.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(lock, "w") as fh:
        json.dump({"run": "r-alive", "ts": fresh, "ttl": 2700}, fh)
    r = cli(d, "release", "B-01")
    assert r.returncode != 0, f"reaped a live lease: {r.stdout}{r.stderr}"
    assert os.path.exists(lock), "a live lease was removed"


def a_lease_with_an_unreadable_timestamp_is_not_eternal():
    """An unparseable `ts` is not a licence to hold forever — the shape that would
    otherwise turn a corrupt lock into the same unclearable state."""
    d = project(["| B-01 | the thing itself | open |\n"])
    lock = os.path.join(d, ".agent-sync", "leases", "B-01.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    with open(lock, "w") as fh:
        json.dump({"run": "r-corrupt", "ts": "not-a-date", "ttl": 2700}, fh)
    r = cli(d, "release", "B-01")
    assert r.returncode == 0, f"an undatable lock stayed eternal: {r.stdout}{r.stderr}"
    assert not os.path.exists(lock), "the lock survived"


for n, f in [
    ("a cited id is still taggable (B-42)", a_cited_id_is_still_taggable),
    ("releasing keeps a close written while held (B-35)", releasing_keeps_a_close_written_while_held),
    ("an untouched cell is restored exactly", an_untouched_cell_is_restored_exactly),
    ("a register with the shipped `pattern` key works (B-34)", a_register_with_the_shipped_key_works),
    ("a register with no pattern reports rather than crashes (B-34)", a_register_with_no_pattern_reports_rather_than_crashes),
    ("a genuinely duplicated id still refuses", a_genuinely_ambiguous_id_still_refuses),
    ("an expired foreign lease can be reaped", an_expired_foreign_lease_can_be_reaped),
    ("a live foreign lease is still refused", a_live_foreign_lease_is_still_refused),
    ("an undatable lease is not eternal", a_lease_with_an_unreadable_timestamp_is_not_eternal),
]:
    case(n, f)

if failures:
    print(f"\nFAIL: {len(failures)} of 9")
    sys.exit(1)
print("\nPASS: claim cell, id registers and lease reaping — 9 cases")
