#!/usr/bin/env python3
"""`run_gate.classify`: a real-home file naming a gate home or this checkout is the suites'
leak and fails the gate; a file another live session wrote meanwhile is reported, not failed
(2026-11-25: the v1.23.7 release gate was refused twice on other sessions' session-repos)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _sandbox  # noqa: E402
_sandbox.isolate()
import run_gate  # noqa: E402

MARKERS = run_gate.GATE_MARKERS + (run_gate.REPO,)
root = tempfile.mkdtemp()
fails = 0


def write(name, value):
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    return path


def check(label, cond):
    global fails
    print(("ok: " if cond else "FAIL: ") + label)
    fails += 0 if cond else 1


gate = write("a.json", ["/var/folders/x/T/agent-sync-gate-home-abc/repo"])
test = write("b.json", ["/tmp/agent-sync-test-home-q1/fixture"])
repo = write("c.json", [run_gate.REPO])
other = write("d.json", ["/Users/someone/DATA/_worktrees/fabric-0330-land"])
missing = os.path.join(root, "gone.json")

leaked, foreign = run_gate.classify([gate, test, repo, other, missing], MARKERS)
check("a file naming a gate home is a leak", gate in leaked)
check("a file naming a test home is a leak", test in leaked)
check("a file naming this checkout is a leak", repo in leaked)
check("another session's file is foreign, not a leak", other in foreign and other not in leaked)
check("an unreadable file counts as a leak", missing in leaked)
check("nothing new reads as nothing", run_gate.classify([], MARKERS) == ([], []))
print(f"\n{'PASS' if not fails else 'FAIL'}: gate residue — {6 - fails} of 6")
sys.exit(1 if fails else 0)
