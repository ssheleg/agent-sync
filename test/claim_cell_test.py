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
import platform
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


def plant_lock(d, key, run, age=0, ttl=2700, host=None):
    """One lock file, at a chosen age. `age=0` is live, anything past `ttl` is spent."""
    path = os.path.join(d, ".agent-sync", "leases", f"{key}.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))
    body = {"run": run, "ts": ts, "ttl": ttl, "repo": os.path.basename(d)}
    if host:
        body["host"] = host
    with open(path, "w") as fh:
        json.dump(body, fh)
    return path


def cli(d, *args, rid=None):
    env = dict(os.environ)
    if rid:
        env["AGENT_SYNC_RUN_ID"] = rid
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=d,
                          capture_output=True, text=True, env=env)


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


# --- ssheleg#5: a claim tag that outlived its lease -------------------------
#
# Filed 2026-08-17, reproduced verbatim at 1.14.0: `release B-77` against a board
# carrying `(claimed: r-…)` with no lock behind it printed `released B-77`, exited 0
# and left `git diff --stat` empty; `residue` said "nothing on disk"; `reconcile`
# never mentioned the claim. Two lines made it unreachable — `write_claim`'s
# `if saved is None: continue` and `claim_divergence`'s `if not held: return out`.


def an_orphaned_claim_tag_is_cleared():
    """THE #5 INCIDENT. A tag with no lock behind it at all — the state left by a run
    that died, or by `.agent-sync/` being wiped between the acquire and the release."""
    d = project(["| B-77 | a thing | open (claimed: r-ghost1234) |\n"])
    r = cli(d, "release", "B-77", rid="fresh")
    assert r.returncode == 0, f"release failed: {r.stdout}{r.stderr}"
    after = status_of(d, "B-77") or ""
    assert "claimed:" not in after, f"the orphaned tag survived the release: {after!r}"
    assert after == "open", f"the cell lost more than the marker: {after!r}"
    assert "r-ghost1234" in r.stdout, f"did not name whose tag it was: {r.stdout!r}"


def an_orphaned_tag_behind_an_expired_lease_is_cleared():
    """The same shape with the corpse still on disk: expiry ended the lease, and the
    tag is a claim about a lease the contract has already ended."""
    d = project(["| B-78 | a thing | open (claimed: r-dead) |\n"])
    plant_lock(d, "B-78", run="r-dead", age=864000, ttl=2700)
    r = cli(d, "release", "B-78", rid="fresh")
    assert r.returncode == 0, f"release failed: {r.stdout}{r.stderr}"
    assert "claimed:" not in (status_of(d, "B-78") or ""), status_of(d, "B-78")
    assert "r-dead" in r.stdout, f"did not name whose tag it was: {r.stdout!r}"


def a_tag_with_a_live_lease_is_not_cleared_by_another_run():
    """The direction that must NOT change. A tag whose lease is inside its TTL is
    somebody working, and clearing it is the collision the lease exists to prevent."""
    d = project(["| B-88 | a thing | open (claimed: r-alive) |\n"])
    plant_lock(d, "B-88", run="r-alive", age=0, ttl=2700)
    r = cli(d, "release", "B-88", rid="other")
    assert r.returncode != 0, f"released another run's live lease: {r.stdout}{r.stderr}"
    assert status_of(d, "B-88") == "open (claimed: r-alive)", (
        f"a live claim was edited by another run: {status_of(d, 'B-88')!r}")


def an_orphaned_tag_is_reported_by_the_commands_that_report_state():
    """Being clearable is half of it. The tag was STRUCTURALLY invisible: every reader
    of holdings applies the TTL, so nobody held it and nobody could be told."""
    d = project(["| B-79 | a thing | open (claimed: r-ghost1234) |\n"])
    for cmd in ("status", "residue", "reconcile"):
        out = (lambda r: r.stdout + r.stderr)(cli(d, cmd, rid="fresh"))
        assert "B-79" in out, f"`{cmd}` never names the orphaned tag: {out[-400:]}"
        assert "orphan" in out.lower(), f"`{cmd}` names it without saying what it is"


def residue_says_what_it_cannot_see_in_git_mode():
    """AS-01a. The read walks the lock directory; in git mode the authority is a set of
    refs on the remote. A check that cannot look must not read as one that looked."""
    d = project(["| B-01 | a thing | open |\n"])
    cfg = os.path.join(d, ".claude", "agent-sync.json")
    with open(cfg) as fh:
        c = json.load(fh)
    c["leaseBackend"] = "git"
    with open(cfg, "w") as fh:
        json.dump(c, fh)
    out = cli(d, "residue").stdout
    assert "INCOMPLETE IN THIS MODE" in out, f"residue reads as complete in git mode: {out}"
    assert "refs/agent-sync/leases/*" in out, "does not say how to enumerate them by hand"


def a_local_lock_records_the_machine_that_wrote_it():
    """AS-03. `host` used to be the git mode's alone, and `classify_lock` consumes it —
    so in local mode the classifier had one fewer way to refuse. 25 of the 25 locks this
    family had on disk carried none (2026-08-20)."""
    d = project(["| B-13 | a thing | open |\n"])
    assert cli(d, "acquire", "B-13").returncode == 0
    with open(os.path.join(d, ".agent-sync", "leases", "B-13.lock")) as fh:
        lock = json.load(fh)
    assert lock.get("host"), f"a local lock carries no host: {lock}"
    assert lock["host"] == platform.node(), f"host is not this machine: {lock}"


def two_machines_are_separated_in_local_mode():
    """The classifier fixture the payload change exists for: same run id, same repo,
    same expiry — a different machine, and it stops being this run's to clear."""
    mod_dir = os.path.dirname(SCRIPT)
    sys.path.insert(0, mod_dir)
    try:
        import agent_sync as mod                    # noqa: PLC0415 - loaded from the shipped path
    finally:
        sys.path.pop(0)
    payload = {"run": "r-mine", "ts": "1970-01-01T00:10:00Z", "ttl": 60}
    here = mod.classify_lock("K", json.dumps({**payload, "host": "boxA"}), rid="r-mine",
                             identity_is_strong=True, repo="here", host="boxA",
                             default_ttl=2700, at=1_000_000.0)
    there = mod.classify_lock("K", json.dumps({**payload, "host": "boxB"}), rid="r-mine",
                              identity_is_strong=True, repo="here", host="boxA",
                              default_ttl=2700, at=1_000_000.0)
    assert here["state"] == "reapable", f"this machine's own spent lock: {here}"
    assert there["state"] == "foreign", f"another machine's lock is not foreign: {there}"
    assert "boxB" in there["why"], f"does not name the machine: {there['why']}"


CASES = [
    ("a cited id is still taggable (B-42)", a_cited_id_is_still_taggable),
    ("releasing keeps a close written while held (B-35)", releasing_keeps_a_close_written_while_held),
    ("an untouched cell is restored exactly", an_untouched_cell_is_restored_exactly),
    ("a register with the shipped `pattern` key works (B-34)", a_register_with_the_shipped_key_works),
    ("a register with no pattern reports rather than crashes (B-34)", a_register_with_no_pattern_reports_rather_than_crashes),
    ("a genuinely duplicated id still refuses", a_genuinely_ambiguous_id_still_refuses),
    ("an expired foreign lease can be reaped", an_expired_foreign_lease_can_be_reaped),
    ("a live foreign lease is still refused", a_live_foreign_lease_is_still_refused),
    ("an undatable lease is not eternal", a_lease_with_an_unreadable_timestamp_is_not_eternal),
    ("an orphaned claim tag is cleared (#5)", an_orphaned_claim_tag_is_cleared),
    ("an orphaned tag behind an expired lease is cleared (#5)",
     an_orphaned_tag_behind_an_expired_lease_is_cleared),
    ("a tag with a live lease is not cleared by another run (#5)",
     a_tag_with_a_live_lease_is_not_cleared_by_another_run),
    ("an orphaned tag is reported by status/residue/reconcile (#5)",
     an_orphaned_tag_is_reported_by_the_commands_that_report_state),
    ("residue states what it cannot see in git mode (AS-01a)",
     residue_says_what_it_cannot_see_in_git_mode),
    ("a local lock records its host (AS-03)", a_local_lock_records_the_machine_that_wrote_it),
    ("two machines are separated in local mode (AS-03)", two_machines_are_separated_in_local_mode),
]
for n, f in CASES:
    case(n, f)

# Counted, never restated: a literal here goes stale the first time a case is added,
# and then the suite reports a total that is not the total it ran.
if failures:
    print(f"\nFAIL: {len(failures)} of {len(CASES)}")
    sys.exit(1)
print(f"\nPASS: claim cell, id registers, lease reaping and orphaned claim tags "
      f"— {len(CASES)} cases")
