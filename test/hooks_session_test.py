#!/usr/bin/env python3
"""The SessionStart hook must establish a session identity, or nothing else here separates runs.

Why this file exists. `run_id()` keys its marker by whatever `_session_key()` can establish,
and a key it cannot establish falls back to one `shared` entry per checkout. Under that key a
matching run id proves nothing, so `classify_lock` answers `ambiguous` and an expired lease can
never be reaped by the run that took it.

Measured on this machine 2026-08-25, in the checkout that had been running the tool all day:
`.agent-sync/sessions` did not exist and `.agent-sync/run-id` held exactly one key, `shared`.
The stamping block required `CLAUDE_SESSION_ID` in the hook's ENVIRONMENT; Claude Code delivers
the id to a hook on **stdin as JSON**, the way `guard.sh` has always read its own payload. So
the block had never run once, and the weak identity everything else hedged about was not a
fallback — it was the only path.

The tests below run the real hook as a process, because that is the only way this could have
been caught: every unit around it was correct.
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil
import importlib.util

# The module import below compiles bytecode into the shipped scripts/ directory;
# a test run must not leave build products in a tree people read as source (ASY-01).
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PLUGIN = os.path.join(REPO, "plugins", "agent-sync")
HOOK = os.path.join(PLUGIN, "hooks", "session-start.sh")
SCRIPT = os.path.join(PLUGIN, "skills", "agent-sync", "scripts", "agent_sync.py")

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
    except Exception as e:  # a crash is a failure, and it says which case crashed
        failures.append(name)
        print("  ERROR  %s\n         %s: %s" % (name, type(e).__name__, e))


def _repo():
    """A real git checkout with coordination configured — the hook no-ops without both."""
    d = tempfile.mkdtemp(prefix="agent-sync-hooktest-")
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        # The key is `backend`, and `leaseBackend` is not one: written the wrong way this
        # fixture stood on a project the tool reports as unprotected, which is not the
        # project whose identity these tests are about.
        json.dump({"backend": "fs", "leaseTtlSeconds": 2700, "gated": True,
                   "guardedFiles": []}, fh)
    open(os.path.join(d, "README.md"), "w").write("x\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True)
    return d


def _run_hook(root, payload, env_session=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = PLUGIN
    env["CLAUDE_PROJECT_DIR"] = root
    env.pop("CLAUDE_SESSION_ID", None)
    if env_session is not None:
        env["CLAUDE_SESSION_ID"] = env_session
    return subprocess.run(["bash", HOOK], cwd=root, env=env, input=payload,
                          capture_output=True, text=True, timeout=60)


def _module():
    spec = importlib.util.spec_from_file_location("agent_sync_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def the_hook_stamps_the_session_from_its_stdin_payload():
    """The defect, exactly: an id that arrives only on stdin must still be recorded."""
    root = _repo()
    try:
        proc = _run_hook(root, json.dumps({"session_id": "sess-from-stdin",
                                           "hook_event_name": "SessionStart"}))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        d = os.path.join(root, ".agent-sync", "sessions")
        assert os.path.isdir(d), (
            "the hook created no sessions/ directory, so no later command can learn which "
            "session it belongs to — stdout was: %r" % proc.stdout[:400])
        marks = os.listdir(d)
        assert marks, "sessions/ is empty: the stamp was not written"
        # keyed by the hook's PARENT, which is this test process — the one ancestor every
        # command of the session shares.
        assert str(os.getpid()) in marks, (
            "stamped %r, but the key must be the hook's parent pid (%d) or the walk that "
            "looks for it from a descendant cannot find it" % (marks, os.getpid()))
        got = open(os.path.join(d, str(os.getpid()))).read().strip()
        assert got == "sess-from-stdin", "stamped %r, expected the payload's session_id" % got
    finally:
        shutil.rmtree(root, ignore_errors=True)


def the_environment_variable_still_wins_when_it_is_there():
    """The old path is kept: an env id must not stop working because stdin arrived too."""
    root = _repo()
    try:
        proc = _run_hook(root, json.dumps({"session_id": "from-stdin"}),
                         env_session="from-env")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        got = open(os.path.join(root, ".agent-sync", "sessions", str(os.getpid()))).read().strip()
        assert got == "from-env", "stamped %r — the environment id must take precedence" % got
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_payload_with_no_session_id_stamps_nothing():
    """Silence is not an id. Stamping an empty string would key every session the same."""
    root = _repo()
    try:
        proc = _run_hook(root, json.dumps({"hook_event_name": "SessionStart"}))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        d = os.path.join(root, ".agent-sync", "sessions")
        assert not os.path.isdir(d) or not os.listdir(d), (
            "stamped %r from a payload carrying no session_id" % os.listdir(d))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_non_json_payload_does_not_break_the_hook():
    """A hook that throws breaks every turn of every session, including sessions that never
    asked for this plugin. It must degrade, not fail."""
    root = _repo()
    try:
        proc = _run_hook(root, "this is not json at all")
        assert proc.returncode == 0, (
            "exit %d — a SessionStart hook that fails takes the session with it: %s"
            % (proc.returncode, proc.stderr[:300]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_cleared_session_is_stamped_as_a_new_identity():
    """`/clear` ends the run but keeps the CLI process, so the stamp keyed by that
    process survives into the next session. Without `clear` in the SessionStart
    matcher the hook never re-runs there and the new session inherits the ended run's
    identity — "two sessions, one identity", the exact case SKILL.md names, and
    `release` then takes a lease the caller never had (ASY-08).

    `compact` stays excluded on purpose: a compaction continues the SAME session,
    and re-stamping it would only churn the marker it already has.
    """
    with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as fh:
        entries = json.load(fh)["hooks"]["SessionStart"]
    matchers = [e.get("matcher", "") for e in entries]
    parts = {p for m in matchers for p in m.split("|")}
    missing = {"startup", "resume", "clear"} - parts
    assert not missing, (
        "SessionStart matcher %r misses %r — a post-/clear session keeps the ended "
        "run's stamp, acquiring and releasing as a run that no longer exists"
        % (matchers, sorted(missing)))
    assert "compact" not in parts, (
        "SessionStart matcher %r includes `compact` — a compaction continues the same "
        "session; its exclusion is a decision, not an omission" % matchers)

    # And the hook itself must RE-stamp on that event: the fresh id must replace the
    # ended run's, not be refused because a stamp already exists.
    root = _repo()
    try:
        _run_hook(root, json.dumps({"session_id": "sess-before-clear",
                                    "source": "startup"}))
        proc = _run_hook(root, json.dumps({"session_id": "sess-after-clear",
                                           "source": "clear"}))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        got = open(os.path.join(root, ".agent-sync", "sessions",
                                str(os.getpid()))).read().strip()
        assert got == "sess-after-clear", (
            "after /clear the stamp still reads %r — the new session runs under the "
            "ended run's identity" % got)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def the_stamp_makes_this_run_identity_strong():
    """End to end: the stamp exists ⇒ a descendant resolves a session key ⇒ identity is strong
    ⇒ `classify_lock` can call this run's own expired lease reapable instead of ambiguous."""
    root = _repo()
    cwd = os.getcwd()
    try:
        _run_hook(root, json.dumps({"session_id": "sess-e2e"}))
        os.chdir(root)
        mod = _module()
        key, how = mod._session_key()
        assert key == "session:sess-e2e", (
            "the walk from a descendant resolved %r (%s) — the stamp is written but nothing "
            "reads it, which leaves the identity exactly as weak as before" % (key, how))
        rid = mod.run_id(mod.Path(root))
        marker = json.load(open(os.path.join(root, ".agent-sync", "run-id")))
        assert "session:sess-e2e" in marker["runs"], (
            "run-id map keyed %r — a `shared`-only map is the weak identity itself"
            % list(marker["runs"]))
        verdict = mod.classify_lock(
            "K", json.dumps({"run": rid, "repo": root, "host": mod.platform.node(),
                             "ts": "2020-01-01T00:00:00Z", "ttl": 1}),
            rid=rid, identity_is_strong=True, repo=root,
            host=mod.platform.node(), default_ttl=1, at=2e9)
        assert verdict["state"] == mod.REAPABLE, (
            "an expired lease this run took reads %r (%s) — the ambiguity this whole file "
            "exists to end" % (verdict["state"], verdict["why"]))
    finally:
        os.chdir(cwd)
        shutil.rmtree(root, ignore_errors=True)


# --- SessionEnd: one budget both hosts honour ---------------------------------------------
#
# Codex 0.157 clamps a SessionEnd handler's timeout to 3 s and says so at every session start
# (`clamping SessionEnd hook timeout to 3s in …/hooks.json`); Claude Code sizes its own
# SessionEnd wait from the largest handler timeout. The hook declared 20 s and spent it as
# `whoami` plus one `release` process per key, each with its own 10 s limit — so under the
# real 3 s the kill landed mid-loop and the tail of the held keys stayed out until their TTL.

END_HOOK = os.path.join(PLUGIN, "hooks", "session-end.sh")
SESSION_END_BUDGET = 3


def _as(root, rid, *args):
    env = dict(os.environ, AGENT_SYNC_RUN_ID=rid, CLAUDE_PROJECT_DIR=root)
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=root, env=env,
                          capture_output=True, text=True, timeout=60)


def _run_end_hook(root, rid):
    env = dict(os.environ, AGENT_SYNC_RUN_ID=rid, CLAUDE_PLUGIN_ROOT=PLUGIN,
               CLAUDE_PROJECT_DIR=root)
    import time
    t0 = time.monotonic()
    proc = subprocess.run(["bash", END_HOOK], cwd=root, env=env,
                          input=json.dumps({"hook_event_name": "SessionEnd", "reason": "exit"}),
                          capture_output=True, text=True, timeout=60)
    return proc, time.monotonic() - t0


def the_session_end_timeout_fits_every_host():
    with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as fh:
        entries = json.load(fh)["hooks"]["SessionEnd"]
    for e in entries:
        for h in e["hooks"]:
            t = h.get("timeout")
            assert t is not None and t <= SESSION_END_BUDGET, (
                "SessionEnd timeout %r — Codex clamps it to %d s and warns at every session "
                "start; declare what the hosts will actually give" % (t, SESSION_END_BUDGET))


def the_session_end_hook_releases_every_held_lease_within_budget():
    root = _repo()
    try:
        for k in ("T-1", "T-2", "T-3"):
            r = _as(root, "r-ending", "acquire", k)
            assert r.returncode == 0, r.stdout + r.stderr
        assert "T-1, T-2, T-3" in _as(root, "r-ending", "whoami").stdout
        proc, took = _run_end_hook(root, "r-ending")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert took < SESSION_END_BUDGET, (
            "the hook took %.2f s against a %d s budget — the host kills it mid-release"
            % (took, SESSION_END_BUDGET))
        left = _as(root, "r-ending", "whoami").stdout
        assert "holds: nothing" in left, "after SessionEnd the run still %s" % left.strip()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def the_session_end_hook_leaves_another_runs_lease_alone():
    root = _repo()
    try:
        assert _as(root, "r-other", "acquire", "T-9").returncode == 0
        assert _as(root, "r-ending", "acquire", "T-1").returncode == 0
        proc, _ = _run_end_hook(root, "r-ending")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "T-9" in _as(root, "r-other", "whoami").stdout, (
            "SessionEnd of one run released another run's lease")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def release_held_is_a_no_op_for_a_run_holding_nothing():
    root = _repo()
    try:
        r = _as(root, "r-idle", "release", "--held")
        assert r.returncode == 0, "exit %d: %s" % (r.returncode, r.stdout + r.stderr)
        assert "nothing" in r.stdout, r.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def release_refuses_both_a_key_and_held():
    root = _repo()
    try:
        r = _as(root, "r-x", "release", "T-1", "--held")
        assert r.returncode != 0, "release accepted a key AND --held: %s" % r.stdout
        r = _as(root, "r-x", "release")
        assert r.returncode != 0, "release with neither a key nor --held exited 0"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- run_limited: the watchdog must not hold the caller's pipe --------------------------
#
# Stock macOS has neither `timeout` nor `gtimeout`, so `run_limited` falls back to a bash
# watchdog. That watchdog was `( sleep N; kill ) &` with the caller's stdout inherited, and
# `kill "$watchdog"` ended the subshell but not its `sleep` — the orphan kept the pipe open,
# so every `$(run_limited 10 …)` waited the full 10 s after the command had finished, and
# SessionStart's stdout stayed open 10 s on every macOS session. Measured 2026-09-27:
# session-end.sh took 10.45 s to release three leases that took well under one.

LIB = os.path.join(PLUGIN, "hooks", "_lib.sh")


def _fallback_path():
    """A PATH carrying what the helper needs and neither timeout binary — the stock-macOS
    shape, reproduced on a Linux runner where coreutils would otherwise hide the fallback."""
    d = tempfile.mkdtemp(prefix="agent-sync-nopath-")
    for tool in ("bash", "sleep", "echo", "true", "kill", "cat"):
        src = shutil.which(tool)
        if src:
            os.symlink(src, os.path.join(d, tool))
    return d


def _lib_run(snippet):
    """Run `snippet` with the helper sourced, stdout and stderr both pipes — the shape a hook
    host gives. The defect is a race (whether the kill lands before the watchdog has forked its
    `sleep`), so callers repeat the shape rather than trusting one draw."""
    import time
    d = _fallback_path()
    try:
        t0 = time.monotonic()
        proc = subprocess.run([shutil.which("bash"), "-c", ". \"%s\"\n%s" % (LIB, snippet)],
                              env={"PATH": d, "HOME": os.environ.get("HOME", "/")},
                              capture_output=True, text=True, timeout=120)
        return proc, time.monotonic() - t0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def the_fallback_watchdog_releases_a_captured_pipe_at_once():
    proc, took = _lib_run('command -v timeout gtimeout && exit 9\n'
                          'for i in {1..20}; do\n'
                          '  x=$(run_limited 3 echo hi); echo "sub:$x"\n'
                          '  run_limited 3 echo hi | cat >/dev/null; echo "pipe:$?"\n'
                          'done')
    assert proc.returncode == 0, "exit %d — the PATH still offers a timeout binary, or the "\
        "helper failed: %s" % (proc.returncode, proc.stdout + proc.stderr)
    assert proc.stdout.count("sub:hi") == 20, proc.stdout
    assert took < 3, ("forty captured run_limited calls of `echo` took %.2f s — the watchdog's "
                      "sleep is holding the caller's pipe open until its limit" % took)


def the_fallback_watchdog_still_kills_an_overrun():
    proc, took = _lib_run('run_limited 1 sleep 20; echo "rc:$?"')
    assert took < 5, "an overrunning command outlived its 1 s limit: %.2f s" % took
    assert "rc:0" not in proc.stdout, "a killed command reported success: %r" % proc.stdout


print("agent-sync — session hooks: SessionStart identity, SessionEnd budget")
for name, fn in [
    ("the hook stamps the session from its stdin payload",
     the_hook_stamps_the_session_from_its_stdin_payload),
    ("the environment variable still wins when it is there",
     the_environment_variable_still_wins_when_it_is_there),
    ("a payload with no session_id stamps nothing", a_payload_with_no_session_id_stamps_nothing),
    ("a non-JSON payload does not break the hook", a_non_json_payload_does_not_break_the_hook),
    ("a cleared session is stamped as a new identity",
     a_cleared_session_is_stamped_as_a_new_identity),
    ("the stamp makes this run's identity strong", the_stamp_makes_this_run_identity_strong),
    ("the SessionEnd timeout fits every host", the_session_end_timeout_fits_every_host),
    ("the SessionEnd hook releases every held lease within budget",
     the_session_end_hook_releases_every_held_lease_within_budget),
    ("the SessionEnd hook leaves another run's lease alone",
     the_session_end_hook_leaves_another_runs_lease_alone),
    ("release --held is a no-op for a run holding nothing",
     release_held_is_a_no_op_for_a_run_holding_nothing),
    ("release refuses both a key and --held", release_refuses_both_a_key_and_held),
    ("the fallback watchdog releases a captured pipe at once",
     the_fallback_watchdog_releases_a_captured_pipe_at_once),
    ("the fallback watchdog still kills an overrun", the_fallback_watchdog_still_kills_an_overrun),
]:
    case(name, fn)

if failures:
    print("\nFAIL: %d of %d — %s" % (len(failures), cases, ", ".join(failures)))
    sys.exit(1)
print("\nPASS: session hooks — %d cases" % cases)
