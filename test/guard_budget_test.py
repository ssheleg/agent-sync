#!/usr/bin/env python3
"""The PreToolUse guard answers inside its budget, whatever the lease remote does (AS-26).

Why this file exists. In git lease mode the first write of a guarded file takes that file's
resource claim on the lease remote: `ls-remote` for the ref (and again under its 1.21.2
name), then a `push`. Measured 2026-10-20 against github.com from this machine: one
`ls-remote` takes 1.6 s, so a claim costs 3.5 to 5 s on a good day; the push had no
timeout at all, and a cloud record plane adds its own append on top. The 2026-10-20 audit
saw `guard.sh` take 10 to 20 s four times, against a hook timeout of 20 s, after which the
host lets the call through anyway. Every Edit of a new guarded file paid it.

The guard now waits for the claim only until its budget (`AGENT_SYNC_GUARD_BUDGET`,
1.5 s by default) and then lets the write proceed under the task lease while the claim
finishes in a detached process. What must still hold, and what these cases pin:

- a slow remote never holds a write past the budget;
- the claim still lands, so the next write is decided locally;
- a claim another run holds is still refused: at once when the remote answers in time,
  and at the next write when it did not;
- two writes of one file by one run never refuse each other while the claim is in flight.

A git shim on PATH slows `ls-remote` and `push` by a fixed delay; everything else is real:
the hook, the coordinator and a local bare remote.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox  # noqa: E402
_sandbox.isolate()
for _v in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "AGENT_SYNC_RUN_ID",
           "AGENT_SYNC_GUARD_BUDGET"):
    os.environ.pop(_v, None)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PLUGIN = os.path.join(REPO, "plugins", "agent-sync")
GUARD = os.path.join(PLUGIN, "hooks", "guard.sh")
SCRIPT = os.path.join(PLUGIN, "skills", "agent-sync", "scripts", "agent_sync.py")
REAL_GIT = shutil.which("git")

SLOW = 5.0          # seconds each slowed git call sleeps; a synchronous claim pays two
LIMIT = SLOW        # a guard call under this cannot have waited for even one slowed call.
                    # Not the 1.5 s budget itself: on a loaded machine (load average ~690
                    # here, 2026-10-20) two interpreter starts alone took over 2 s

cases = 0
failures = []


def case(name, fn):
    global cases
    cases += 1
    try:
        fn()
        print("  PASS   %s" % name)
    except AssertionError as e:
        failures.append(name)
        print("  FAIL   %s\n         %s" % (name, e))
    except Exception as e:
        failures.append(name)
        print("  ERROR  %s\n         %s: %s" % (name, type(e).__name__, e))


class Project:
    """A git-lease project on a local bare remote, plus a PATH whose git is slow."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-sync-budget-")
        self.bare = os.path.join(self.tmp, "remote.git")
        self.root = os.path.join(self.tmp, "repo")
        self.shim = os.path.join(self.tmp, "shim")
        subprocess.run([REAL_GIT, "init", "-q", "--bare", self.bare], check=True)
        subprocess.run([REAL_GIT, "init", "-q", self.root], check=True)
        g = lambda *a: subprocess.run([REAL_GIT, "-C", self.root, *a], check=True,
                                      capture_output=True)
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        os.makedirs(os.path.join(self.root, ".claude"))
        os.makedirs(os.path.join(self.root, "docs"))
        with open(os.path.join(self.root, ".claude", "agent-sync.json"), "w") as fh:
            json.dump({"backend": "fs", "leaseBackend": "git", "leaseRemote": "origin",
                       "leaseTtlSeconds": 2700, "gated": True,
                       "guardedFiles": ["docs/*.md"]}, fh)
        for name in ("REG.md", "OTHER.md", "THIRD.md"):
            open(os.path.join(self.root, "docs", name), "w").write("x\n")
        g("add", "-A")
        g("commit", "-qm", "init")
        g("remote", "add", "origin", self.bare)
        g("push", "-q", "origin", "HEAD")
        os.makedirs(self.shim)
        with open(os.path.join(self.shim, "git"), "w") as fh:
            fh.write('#!/bin/sh\nfor a in "$@"; do case "$a" in ls-remote|push) '
                     'sleep "${SLOW_GIT_S:-0}"; break ;; esac; done\nexec "%s" "$@"\n' % REAL_GIT)
        os.chmod(os.path.join(self.shim, "git"), 0o755)

    def env(self, rid, slow):
        env = dict(os.environ, AGENT_SYNC_RUN_ID=rid, CLAUDE_PLUGIN_ROOT=PLUGIN,
                   CLAUDE_PROJECT_DIR=self.root)
        if slow:
            env["PATH"] = self.shim + os.pathsep + env.get("PATH", "")
            env["SLOW_GIT_S"] = str(SLOW)
        return env

    def cli(self, rid, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], cwd=self.root,
                              env=self.env(rid, slow=False), capture_output=True, text=True,
                              timeout=120)

    def guard(self, rid, name, slow=True):
        payload = json.dumps({"session_id": "s-" + rid, "tool_name": "Write",
                              "tool_input": {"file_path": os.path.join(self.root, "docs", name),
                                             "content": "y\n"}})
        t0 = time.monotonic()
        proc = subprocess.run(["bash", GUARD], cwd=self.root, env=self.env(rid, slow),
                              input=payload, capture_output=True, text=True, timeout=120)
        return proc, time.monotonic() - t0

    def rid(self, rid):
        """The run id the tool derives for an AGENT_SYNC_RUN_ID, asked of the tool itself."""
        first = self.cli(rid, "whoami").stdout.split()
        return first[1] if len(first) > 1 else rid

    def remote_refs(self):
        out = subprocess.run([REAL_GIT, "ls-remote", self.bare, "refs/agent-sync/leases/*"],
                             capture_output=True, text=True).stdout
        return [l.split()[1] for l in out.splitlines() if l.strip()]

    def settle(self, seconds=30):
        """Wait until no background claim of any run is still running."""
        d = os.path.join(self.root, ".agent-sync", "claiming")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pids = []
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith(".pid"):
                        try:
                            pids.append(int(open(os.path.join(d, f)).read().strip() or 0))
                        except (OSError, ValueError):
                            pass
            alive = []
            for p in pids:
                try:
                    os.kill(p, 0)
                    alive.append(p)
                except OSError:
                    pass
            if not alive:
                return
            time.sleep(0.1)
        raise AssertionError("a background claim was still running after %d s" % seconds)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _res_refs(p, fragment):
    return [r for r in p.remote_refs() if r.startswith("refs/agent-sync/leases/res")
            and fragment in r]


def a_slow_remote_never_holds_a_write_past_the_budget():
    p = Project()
    try:
        assert p.cli("r-a", "acquire", "T-1").returncode == 0
        proc, took = p.guard("r-a", "REG.md")
        assert proc.returncode == 0, "exit %d: %s" % (proc.returncode, proc.stderr[-400:])
        assert took < LIMIT, (
            "the guard took %.2f s over a remote answering in %.1f s per call: the write "
            "waited for the remote" % (took, SLOW))
        p.settle()
        assert _res_refs(p, "REG"), (
            "the claim never landed on the remote: %r" % p.remote_refs())
        proc, took = p.guard("r-a", "REG.md")
        assert proc.returncode == 0 and took < LIMIT, (
            "the next write, after the claim landed, exit %d in %.2f s: %s"
            % (proc.returncode, took, proc.stderr[-300:]))
    finally:
        p.settle()
        p.close()


def a_fast_remote_still_refuses_another_runs_claim_at_once():
    p = Project()
    try:
        assert p.cli("r-a", "acquire", "T-1").returncode == 0
        proc, _ = p.guard("r-a", "REG.md", slow=False)
        assert proc.returncode == 0, proc.stderr
        assert p.cli("r-b", "acquire", "T-2").returncode == 0
        proc, took = p.guard("r-b", "REG.md", slow=False)
        assert proc.returncode == 2, (
            "a second run wrote a file whose claim the first holds (exit %d, %.2f s): %s"
            % (proc.returncode, took, proc.stdout + proc.stderr))
        assert p.rid("r-a") in proc.stderr, "the refusal does not name the holder: %s" % proc.stderr
    finally:
        p.settle()
        p.close()


def a_slow_remote_refuses_another_runs_claim_by_the_next_write():
    p = Project()
    try:
        assert p.cli("r-a", "acquire", "T-1").returncode == 0
        proc, _ = p.guard("r-a", "REG.md", slow=False)
        assert proc.returncode == 0, proc.stderr
        assert p.cli("r-b", "acquire", "T-2").returncode == 0
        first, took1 = p.guard("r-b", "REG.md")
        assert took1 < LIMIT, "the first write waited %.2f s" % took1
        p.settle()
        second, took2 = p.guard("r-b", "REG.md")
        assert second.returncode == 2, (
            "the background claim found r-a's claim and the next write still went through "
            "(first exit %d, second exit %d): %s"
            % (first.returncode, second.returncode, second.stdout + second.stderr))
        assert took2 < LIMIT, "the refusal took %.2f s" % took2
        assert p.rid("r-a") in second.stderr, (
            "the refusal does not name the holder: %s" % second.stderr)
    finally:
        p.settle()
        p.close()


def two_writes_of_one_file_by_one_run_never_refuse_each_other():
    p = Project()
    try:
        assert p.cli("r-a", "acquire", "T-1").returncode == 0
        first, took1 = p.guard("r-a", "OTHER.md")
        second, took2 = p.guard("r-a", "OTHER.md")
        assert first.returncode == 0 and second.returncode == 0, (
            "one run's second write of a file whose claim it is taking was refused "
            "(exits %d, %d): %s" % (first.returncode, second.returncode,
                                    second.stdout + second.stderr))
        assert took1 < LIMIT and took2 < LIMIT, "took %.2f s and %.2f s" % (took1, took2)
        p.settle()
        refs = _res_refs(p, "OTHER")
        assert len(refs) == 1, "want one claim on the remote, got %r" % refs
        who = p.cli("r-a", "whoami").stdout
        assert "OTHER" in who, "the run does not hold the claim it took: %s" % who
    finally:
        p.settle()
        p.close()


def the_budget_can_be_switched_off():
    """`AGENT_SYNC_GUARD_BUDGET=0` restores the synchronous claim (the old behaviour), for
    an operator who prefers waiting to a claim that lands after the write."""
    p = Project()
    try:
        assert p.cli("r-a", "acquire", "T-1").returncode == 0
        env_budget = dict(os.environ)
        os.environ["AGENT_SYNC_GUARD_BUDGET"] = "0"
        try:
            proc, took = p.guard("r-a", "THIRD.md")
        finally:
            os.environ.clear()
            os.environ.update(env_budget)
        assert proc.returncode == 0, proc.stderr
        assert took >= SLOW, "with the budget off the guard still returned in %.2f s" % took
        assert _res_refs(p, "THIRD"), "the synchronous claim did not land: %r" % p.remote_refs()
    finally:
        p.settle()
        p.close()


print("agent-sync: the guard answers inside its budget over a slow lease remote")
for name, fn in [
    ("a slow remote never holds a write past the budget",
     a_slow_remote_never_holds_a_write_past_the_budget),
    ("a fast remote still refuses another run's claim at once",
     a_fast_remote_still_refuses_another_runs_claim_at_once),
    ("a slow remote refuses another run's claim by the next write",
     a_slow_remote_refuses_another_runs_claim_by_the_next_write),
    ("two writes of one file by one run never refuse each other",
     two_writes_of_one_file_by_one_run_never_refuse_each_other),
    ("the budget can be switched off", the_budget_can_be_switched_off),
]:
    case(name, fn)

if failures:
    print("\nFAIL: %d of %d: %s" % (len(failures), cases, ", ".join(failures)))
    sys.exit(1)
print("\nPASS: guard budget, %d cases" % cases)
