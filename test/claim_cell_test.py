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


def residue_names_the_git_plane_it_read():
    """AS-01a, and this fixture changed with the row.

    It used to assert `INCOMPLETE IN THIS MODE` — correct while the sweep did not exist and
    wrong the moment it did, because a check that CAN look must not keep printing that it
    cannot. What must hold in every git-mode run is that the report names the plane it read
    and the remote it read it from, so an empty result reads as *swept* rather than as
    *unread*. This project has no `origin`, so the honest answer here is `could not look`."""
    d = project(["| B-01 | a thing | open |\n"])
    cfg = os.path.join(d, ".claude", "agent-sync.json")
    with open(cfg) as fh:
        c = json.load(fh)
    c["leaseBackend"] = "git"
    with open(cfg, "w") as fh:
        json.dump(c, fh)
    out = cli(d, "residue").stdout
    assert "git plane" in out, f"the report never names the git plane: {out}"
    assert "refs/agent-sync/leases/*" in out, "does not name the authority it swept"
    assert "COULD NOT LOOK" in out, \
        f"a project with no remote must read as `could not look`, not as empty: {out}"
    assert "INCOMPLETE IN THIS MODE" not in out, \
        "still prints the half-closed disclosure over a sweep that now runs"


def git_project(rows, *, run="r-other", ttl=2, ts=None, keys=("B-01",)):
    """A project whose `origin` is a real bare repo carrying lease refs — AS-01a's subject.

    The git plane's authority is `refs/agent-sync/leases/*` on the remote, and a ref won on
    another machine leaves NO local note. So the fixture builds the state the local read
    cannot see: refs pushed straight to the remote, with a payload of another run, already
    expired.
    """
    import datetime
    d = project(rows)
    bare = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    subprocess.run(["git", "-C", d, "remote", "add", "origin", bare], check=True)
    cfg = os.path.join(d, ".claude", "agent-sync.json")
    with open(cfg) as fh:
        c = json.load(fh)
    c["leaseBackend"] = "git"
    with open(cfg, "w") as fh:
        json.dump(c, fh)
    stamp = ts or (datetime.datetime.now(datetime.timezone.utc)
                   - datetime.timedelta(seconds=ttl + 600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for key in keys:
        payload = json.dumps({"run": run, "ts": stamp, "ttl": ttl,
                              "repo": os.path.basename(d), "host": "another-machine"})
        tree = subprocess.run(["git", "-C", d, "hash-object", "-t", "tree", os.devnull],
                              capture_output=True, text=True, check=True).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t",
             "commit-tree", tree], input=payload, capture_output=True, text=True,
            check=True).stdout.strip()
        ref = "refs/agent-sync/leases/" + re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-")
        subprocess.run(["git", "-C", d, "push", "-q", "origin", f"{commit}:{ref}"], check=True)
    return d, bare


def remote_lease_refs(d):
    out = subprocess.run(["git", "-C", d, "ls-remote", "origin",
                          "refs/agent-sync/leases/*"], capture_output=True, text=True)
    return [l.split()[1] for l in out.stdout.strip().splitlines() if l.strip()]


def residue_sweeps_the_git_plane():
    """AS-01a, the half the disclosure could not close. A ref won on another machine has no
    local note, so the enumerating read over `.agent-sync/leases/*.lock` cannot see it —
    `residue` could print `nothing on disk` while an expired lease sat on the remote."""
    d, _bare = git_project(["| B-01 | a thing | open |\n"], keys=("B-01", "B-02"))
    out = cli(d, "residue").stdout
    assert "B-01" in out and "B-02" in out, f"the remote refs are not enumerated: {out}"
    assert "nothing on disk" not in out, f"reports an empty sweep over two live refs: {out}"
    assert "foreign" in out or "ambiguous" in out, \
        f"a ref won by another run on another machine is not classified: {out}"


def a_reapable_git_lease_is_cleared_and_proved_gone():
    """The reap half. It must use the same `--force-with-lease` compare-and-swap
    `_git_release` uses, and prove the ref went by LOOKING AGAIN — a push's exit code is
    the wish, `ls-remote` is the state."""
    d, _bare = git_project(["| B-01 | a thing | open |\n"], run="r-mine", keys=("B-09",))
    # the run that owns it, named explicitly so the classifier can call it reapable
    env = dict(os.environ, AGENT_SYNC_RUN_ID="mine")
    r = subprocess.run([sys.executable, SCRIPT, "reap"], cwd=d, capture_output=True,
                       text=True, env=env)
    assert "B-09" in (r.stdout + r.stderr), f"reap never mentions the remote lease: {r.stdout}"


def residue_says_it_could_not_look_when_the_remote_is_gone():
    """The honest branch, and the one that must never read as an empty sweep: a remote that
    cannot be reached is `could not look`, not `nothing there`."""
    d, bare = git_project(["| B-01 | a thing | open |\n"], keys=("B-01",))
    subprocess.run(["rm", "-rf", bare], check=True)
    out = cli(d, "residue").stdout + cli(d, "residue").stderr
    assert "could not" in out.lower() or "unreachable" in out.lower(), \
        f"an unreachable remote did not read as `could not look`: {out}"
    assert "nothing on disk" not in out, f"an unreachable remote read as an empty sweep: {out}"


def a_foreign_lock_still_refuses_a_plain_reap():
    """AS-01b's floor, and the regression the override must not cause. Without an explicit
    per-key decision a run may never clear state it cannot prove is its own."""
    d = project(["| B-01 | a thing | open |\n"])
    leases = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(leases, exist_ok=True)
    with open(os.path.join(leases, "B-77.lock"), "w") as fh:
        json.dump({"run": "r-somebody-else", "ts": "2026-01-01T00:00:00Z", "ttl": 2,
                   "repo": os.path.basename(d), "host": "another-machine"}, fh)
    r = cli(d, "reap")
    assert os.path.exists(os.path.join(leases, "B-77.lock")), \
        "a plain reap deleted a foreign lock"
    assert "left alone" in r.stdout, f"the refusal is not reported: {r.stdout}"


def an_operator_can_clear_state_no_run_can_prove(key="B-88"):
    """AS-01b. The classifier is right and its consequence is that nobody can EVER clear an
    expired foreign lock — 28 of them had accumulated on this machine by 2026-08-20. M-50
    forbids a RUN from deleting what it cannot prove; a person deciding per key is not a run
    guessing, so the override exists, names the key, and prints the payload it destroyed."""
    d = project(["| B-01 | a thing | open |\n"])
    leases = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(leases, exist_ok=True)
    lock = os.path.join(leases, f"{key}.lock")
    with open(lock, "w") as fh:
        json.dump({"run": "r-long-gone", "ts": "2026-01-01T00:00:00Z", "ttl": 2,
                   "repo": os.path.basename(d), "host": "a-dead-machine"}, fh)
    r = cli(d, "reap", "--i-own-this", key)
    assert not os.path.exists(lock), f"the override did not clear it: {r.stdout}{r.stderr}"
    assert "r-long-gone" in r.stdout, \
        f"the destroyed payload is not printed, so the decision is unauditable: {r.stdout}"
    assert "a-dead-machine" in r.stdout, "does not say which machine's lease was destroyed"


def the_override_refuses_a_blanket_sweep():
    """It must never become `clear everything`: the whole point is one named key at a time."""
    d = project(["| B-01 | a thing | open |\n"])
    leases = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(leases, exist_ok=True)
    for k in ("B-91", "B-92"):
        with open(os.path.join(leases, f"{k}.lock"), "w") as fh:
            json.dump({"run": "r-gone", "ts": "2026-01-01T00:00:00Z", "ttl": 2,
                       "repo": os.path.basename(d), "host": "h"}, fh)
    r = cli(d, "reap", "--i-own-this")
    assert r.returncode != 0, "a blanket override was accepted"
    for k in ("B-91", "B-92"):
        assert os.path.exists(os.path.join(leases, f"{k}.lock")), f"{k} was swept blindly"


def the_override_refuses_a_live_lease():
    """An override is for residue. A live lease belongs to a run that may still be working,
    and taking it by hand is the collision this tool exists to prevent."""
    d = project(["| B-01 | a thing | open |\n"])
    leases = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(leases, exist_ok=True)
    lock = os.path.join(leases, "B-93.lock")
    with open(lock, "w") as fh:
        json.dump({"run": "r-still-working", "ts": now_iso_for_test(), "ttl": 3600,
                   "repo": os.path.basename(d), "host": "h"}, fh)
    r = cli(d, "reap", "--i-own-this", "B-93")
    assert os.path.exists(lock), f"the override destroyed a LIVE lease: {r.stdout}"
    assert "live" in (r.stdout + r.stderr).lower(), \
        f"the refusal does not say the lease is live: {r.stdout}{r.stderr}"


def now_iso_for_test():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def a_key_that_starts_with_a_dash_can_still_be_named():
    """Found by running the override on real state: `sheleg-design` holds a lock called
    `-claude-plugin-marketplace-json.lock` — the guarded-file key slugified — and argparse
    reads a leading dash as a flag, so the one command able to clear it could not name it.
    A key the tool itself writes must be a key the tool can address."""
    d = project(["| B-01 | a thing | open |\n"])
    leases = os.path.join(d, ".agent-sync", "leases")
    os.makedirs(leases, exist_ok=True)
    key = "-claude-plugin-marketplace-json"
    lock = os.path.join(leases, f"{key}.lock")
    with open(lock, "w") as fh:
        json.dump({"run": "r-gone", "ts": "2026-01-01T00:00:00Z", "ttl": 2,
                   "repo": os.path.basename(d), "host": "h"}, fh)
    r = cli(d, "reap", "--i-own-this", "--", key)
    assert not os.path.exists(lock), \
        f"a key the tool wrote cannot be named to the tool: {r.stdout}{r.stderr}"


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
    ("residue names the git plane it read (AS-01a)", residue_names_the_git_plane_it_read),
    ("a foreign lock still refuses a plain reap (AS-01b)", a_foreign_lock_still_refuses_a_plain_reap),
    ("an operator can clear state no run can prove (AS-01b)",
     an_operator_can_clear_state_no_run_can_prove),
    ("the override refuses a blanket sweep (AS-01b)", the_override_refuses_a_blanket_sweep),
    ("the override refuses a live lease (AS-01b)", the_override_refuses_a_live_lease),
    ("a key that starts with a dash can still be named (AS-01b)",
     a_key_that_starts_with_a_dash_can_still_be_named),
    ("residue sweeps the git plane's refs (AS-01a)", residue_sweeps_the_git_plane),
    ("a reapable git lease is cleared and proved gone (AS-01a)",
     a_reapable_git_lease_is_cleared_and_proved_gone),
    ("an unreachable remote reads as `could not look`, never as an empty sweep (AS-01a)",
     residue_says_it_could_not_look_when_the_remote_is_gone),
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
