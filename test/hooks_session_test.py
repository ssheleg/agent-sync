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


print("agent-sync — SessionStart identity")
for name, fn in [
    ("the hook stamps the session from its stdin payload",
     the_hook_stamps_the_session_from_its_stdin_payload),
    ("the environment variable still wins when it is there",
     the_environment_variable_still_wins_when_it_is_there),
    ("a payload with no session_id stamps nothing", a_payload_with_no_session_id_stamps_nothing),
    ("a non-JSON payload does not break the hook", a_non_json_payload_does_not_break_the_hook),
    ("the stamp makes this run's identity strong", the_stamp_makes_this_run_identity_strong),
]:
    case(name, fn)

if failures:
    print("\nFAIL: %d of %d — %s" % (len(failures), cases, ", ".join(failures)))
    sys.exit(1)
print("\nPASS: SessionStart identity — %d cases" % cases)
