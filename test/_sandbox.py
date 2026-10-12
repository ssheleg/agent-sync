"""One temp HOME per test process, and no session identity it did not set itself.

Imported FIRST by every test file that runs the coordinator. A test run wrote 14 files into
the operator's real `~/.agent-sync/session-repos` (2026-10-12): `cli()` passed the real HOME
and the live CLAUDE_CODE_SESSION_ID through, so every `acquire` registered itself under the
operator's session. After `isolate()`, every subprocess built from `os.environ` inherits a
temporary HOME, removed at exit, and no session id unless the case sets one.

`STARTED_HOME` is the HOME the process was given, for the one guard case that proves it is
left untouched; `REAL_HOME` survives the swap for reading (never writing) a sibling checkout.
"""
import atexit
import os
import shutil
import tempfile

SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "AGENT_SYNC_RUN_ID")

STARTED_HOME = os.environ.get("HOME", "")
REAL_HOME = os.environ.get("AGENT_SYNC_TEST_REAL_HOME") or STARTED_HOME
HOME = ""


def isolate() -> str:
    global HOME
    if HOME:
        return HOME
    HOME = tempfile.mkdtemp(prefix="agent-sync-test-home-")
    os.environ["AGENT_SYNC_TEST_REAL_HOME"] = REAL_HOME
    os.environ["HOME"] = HOME
    for v in SESSION_VARS:
        os.environ.pop(v, None)
    # Every fixture made with tempfile lands inside the temp HOME and goes with it: the suites
    # left their `mkdtemp()` repositories behind in $TMPDIR (unit B2).
    tempfile.tempdir = HOME
    atexit.register(shutil.rmtree, HOME, True)
    return HOME


def agent_sync_files(home: str) -> list:
    """Every file under `<home>/.agent-sync`, for a before/after comparison."""
    root = os.path.join(home, ".agent-sync")
    out = []
    for d, _subs, files in os.walk(root):
        out += [os.path.join(d, f) for f in files]
    return sorted(out)
