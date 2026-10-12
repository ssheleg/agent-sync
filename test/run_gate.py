#!/usr/bin/env python3
"""The local gate — every test suite CI runs (the validator self-test, the smoke test and
`claude plugin validate` stay CI-only), each in a sandbox, then a check that the operator's
own `~/.agent-sync` is exactly as it was.

`npm test` runs this. It used to run five suites and not `test/audit_regressions/*.py`, which
CI runs — so CI went red (FIX-SY-04.02) on a change whose local gate was green. Every suite
here gets a temporary HOME and loses CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID /
AGENT_SYNC_RUN_ID: a session id inherited from the agent running the gate made every test
`acquire` register in the real home (2026-10-12, 14 files).
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True       # no test/__pycache__ in a tree people read as source (ASY-01)
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _sandbox  # noqa: E402

SUITES = [["python3", "test/validate.py"],
          ["python3", "test/claim_cell_test.py"],
          ["python3", "test/hooks_session_test.py"],
          ["python3", "test/guard_uncoordinated_test.py"],
          ["python3", "test/coordination_test.py"]]
SUITES += [["python3", os.path.relpath(p, REPO)]
           for p in sorted(glob.glob(os.path.join(REPO, "test", "audit_regressions", "*.py")))]
SUITES += [["node", "test/installer_test.js"]]


def main() -> int:
    real = _sandbox.STARTED_HOME
    before = _sandbox.agent_sync_files(real) if real else []
    failed = []
    for argv in SUITES:
        home = tempfile.mkdtemp(prefix="agent-sync-gate-home-")
        # Bytecode goes to the temp HOME, never beside the shipped scripts (ASY-01) — and
        # not disabled either, so the hooks run as they do on a real machine.
        env = dict(os.environ, HOME=home, AGENT_SYNC_TEST_REAL_HOME=real,
                   PYTHONPYCACHEPREFIX=os.path.join(home, ".cache", "pycache"))
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for v in _sandbox.SESSION_VARS:
            env.pop(v, None)
        print(f"== {' '.join(argv)}", flush=True)
        r = subprocess.run(argv, cwd=REPO, env=env)
        shutil.rmtree(home, ignore_errors=True)
        if r.returncode != 0:
            failed.append(" ".join(argv))
    after = _sandbox.agent_sync_files(real) if real else []
    if after != before:
        added = sorted(set(after) - set(before))
        print(f"\nFAIL: the gate changed {real}/.agent-sync — {len(added)} new file(s): {added[:5]}")
        failed.append("real-HOME residue")
    if failed:
        print(f"\nFAIL: {len(failed)} of {len(SUITES)} suite(s): " + "; ".join(failed))
        return 1
    print(f"\nPASS: {len(SUITES)} suite(s), each in its own HOME; "
          f"{real or '$HOME'}/.agent-sync untouched ({len(after)} file(s) before and after)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
