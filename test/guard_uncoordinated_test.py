#!/usr/bin/env python3
"""A repository with no coordination has nothing for the guard to say — and the guard said no.

Why this file exists. `cmd_guard` turns every `Fail` into exit 2, and `load_config` raises
`Fail` when `.claude/agent-sync.json` is absent. So in an uncoordinated repository the guard
denied EVERY staged path, including ordinary source files, with a message about a missing
lease. `guard.sh` then printed "this run holds no lease on it. Acquire one, or unstage it."
and no lease could ever satisfy it, because the refusal never reached the lease logic.

Measured on this machine 2026-09-10 in `fabric`, whose `workspace` submodule is a separate
repository: `agent_sync.py guard lib/shell.mjs` exited 2 there, and so did every other path.
The submodule's own `AGENTS.md` states it has "no independent agent-sync identity" and that
work proceeds "under the parent's coordination rules" — so the configuration the guard
demanded is the one that repository is documented never to have. Nothing could be committed
in it through the tool at all.

The boundary this fixes is narrow, and the second and third cases are the point: an ABSENT
config means the repository opted out and the guard allows; a config that exists and cannot
be read means coordination is on and unreadable, which must still deny. Failing open on a
corrupt config would turn one bug into the opposite bug.
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "plugins", "agent-sync",
                      "skills", "agent-sync", "scripts", "agent_sync.py")

cases = 0
failures = []


def case(name, fn):
    global cases
    cases += 1
    try:
        fn()
        print("  ok  %s" % name)
    except AssertionError as exc:
        failures.append(name)
        print("FAIL  %s: %s" % (name, exc))


def repo(config=None, raw_config=None):
    root = tempfile.mkdtemp(prefix="agent-sync-guard-")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    if config is not None or raw_config is not None:
        os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
        with open(os.path.join(root, ".claude", "agent-sync.json"), "w") as fh:
            fh.write(raw_config if raw_config is not None else json.dumps(config))
    return root


def guard(root, path):
    return subprocess.run([sys.executable, SCRIPT, "guard", path],
                          cwd=root, capture_output=True, text=True)


def an_uncoordinated_repository_allows_every_path():
    root = repo()
    try:
        for path in ["lib/shell.mjs", "docs/ux/scenarios.md", "README.md"]:
            result = guard(root, path)
            assert result.returncode == 0, \
                "%s was denied in a repository with no coordination: %s" % (
                    path, (result.stderr or result.stdout).strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def the_answer_says_why_it_allowed():
    root = repo()
    try:
        result = guard(root, "lib/shell.mjs")
        assert "coordination" in (result.stdout + result.stderr).lower(), \
            "an allowed path gave no reason: %r" % result.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def an_unreadable_config_still_denies():
    # Coordination exists and cannot be read. That is the case where failing open would
    # be worse than the bug this file fixes.
    root = repo(raw_config="{ this is not json")
    try:
        result = guard(root, "docs/ROADMAP.md")
        assert result.returncode == 2, \
            "a corrupt config failed open with exit %d" % result.returncode
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_coordinated_repository_still_guards_its_files():
    root = repo({"backend": "fs", "leaseTtlSeconds": 2700, "renewIntervalSeconds": 300,
                 "gated": True, "idRegisters": {}, "guardedFiles": ["docs/ROADMAP.md"],
                 "claimTags": {}, "gates": []})
    try:
        denied = guard(root, "docs/ROADMAP.md")
        assert denied.returncode == 2, \
            "a guarded file was allowed with no lease (exit %d)" % denied.returncode
        allowed = guard(root, "src/main.ts")
        assert allowed.returncode == 0, \
            "an unguarded file was denied in a coordinated repository: %s" % (
                allowed.stderr.strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)


print("agent-sync — the guard in an uncoordinated repository")
for name, fn in [
    ("a repository with no config allows every path", an_uncoordinated_repository_allows_every_path),
    ("the allowance says coordination is off", the_answer_says_why_it_allowed),
    ("a config that exists and cannot be read still denies", an_unreadable_config_still_denies),
    ("a configured repository still guards its own files", a_coordinated_repository_still_guards_its_files),
]:
    case(name, fn)

if failures:
    print("\nFAIL: %d of %d — %s" % (len(failures), cases, ", ".join(failures)))
    sys.exit(1)
print("\nPASS: uncoordinated guard — %d cases" % cases)
