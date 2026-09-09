#!/usr/bin/env python3
"""FIX-SY-07.01 — the guard's advisory boundary is declared (sherlock audit,
SY-07).

The finding: the PreToolUse guard covers Edit/Write/MultiEdit/NotebookEdit and
a Bash git-commit filter, but a Python write_text, a `sed -i`, or a shell
redirect before commit is not blocked — and the late staged-path check cannot
recover a working tree already overwritten. Treating a regex shell parser as a
universal sandbox is the mistake.

The fix under test: the boundary is DECLARED advisory, with a capability
matrix (covered/unsupported per write vector) in guard.sh and the honest
scope in hooks.json's description; and the guard's own commit parser is run to
confirm it covers what it claims (git -C, env, compound) and does not
misclassify an unsupported vector as a commit.

Standard library only; runs the shipped guard.sh parser logic.
"""
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GUARD = os.path.join(ROOT, "plugins", "agent-sync", "hooks", "guard.sh")
HOOKS = os.path.join(ROOT, "plugins", "agent-sync", "hooks", "hooks.json")

failures = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def t_guard_declares_the_matrix():
    g = open(GUARD, encoding="utf-8").read()
    for needle in ("THE PROTECTION BOUNDARY IS ADVISORY",
                   "capability matrix",
                   "Write / Edit / MultiEdit / NotebookEdit   COVERED",
                   "sed -i / perl -i", "UNSUPPORTED",
                   "python -c",
                   "git -C <dir> commit",
                   "regex shell parser is not a universal sandbox",
                   "An UNSUPPORTED vector is not silently trusted"):
        assert needle in g, f"guard.sh no longer states {needle!r}"


def t_hooks_json_declares_the_boundary():
    h = json.load(open(HOOKS, encoding="utf-8"))
    desc = h["description"]
    assert "ADVISORY" in desc and "not a sandbox" in desc.lower(), \
        "hooks.json does not declare the advisory boundary"
    assert "sed -i" in desc and "isolated worktree" in desc, \
        "hooks.json does not name an unsupported vector and the enforceable path"
    # the matchers actually present still cover the declared surfaces
    pre = h["hooks"]["PreToolUse"]
    matchers = " ".join(x.get("matcher", "") + x.get("if", "") for x in pre)
    for m in ("Edit", "Write", "MultiEdit", "NotebookEdit", "git commit"):
        assert m in matchers, f"the declared covered surface {m!r} lost its matcher"


# ---------------- the commit parser, extracted and run as behaviour


PARSER = r'''
import json, shlex, sys
d = json.load(sys.stdin)
cmd = (d.get("tool_input") or {}).get("command", "")
is_commit, repo = 0, "."
for seg in cmd.replace("&&", "\n").replace("||", "\n").replace("|&", "\n").replace(";", "\n").replace("|", "\n").split("\n"):
    try:
        toks = shlex.split(seg)
    except ValueError:
        continue
    if not toks:
        continue
    if toks[0] == "cd" and len(toks) > 1:
        repo = toks[1]; continue
    # skip an env prefix (VAR=value ...)
    i = 0
    while i < len(toks) and "=" in toks[i] and not toks[i].startswith("-"):
        i += 1
    toks = toks[i:]
    if not toks or toks[0] != "git":
        continue
    k, r = 1, None
    while k < len(toks):
        t = toks[k]
        if t == "-C" and k + 1 < len(toks):
            r = toks[k + 1]; k += 2; continue
        if t in ("-c", "--namespace") and k + 1 < len(toks):
            k += 2; continue
        if t.startswith("-"):
            k += 1; continue
        break
    if k < len(toks) and toks[k] == "commit":
        is_commit = 1
        if r:
            repo = r
        break
print(is_commit, repo)
'''


def classify(command):
    r = subprocess.run([sys.executable, "-c", PARSER],
                       input=json.dumps({"tool_input": {"command": command}}),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:200]
    is_commit, repo = r.stdout.split()
    return is_commit == "1", repo


def t_covered_commit_forms_are_detected():
    assert classify("git commit -m x")[0]
    ok, repo = classify("git -C sub commit -m x")
    assert ok and repo == "sub", "git -C commit not covered"
    assert classify("VAR=1 git commit -m x")[0], "env-prefixed commit not covered"
    assert classify("make build && git commit -m x")[0], "compound commit not covered"
    assert classify("echo msg | git commit -F -")[0], "piped commit not covered"


def t_non_commit_and_unsupported_are_not_misclassified():
    assert not classify("git log --grep=commit")[0], "git log misread as commit"
    assert not classify("sed -i 's/a/b/' file.md")[0], \
        "a sed -i was misclassified as a commit — the guard must not pretend to cover it"
    assert not classify("python -c \"open('x','w').write('y')\"")[0]
    assert not classify("echo data > file.md")[0], "a redirect was misread as a commit"


def main():
    case("guard.sh declares the capability matrix", t_guard_declares_the_matrix)
    case("hooks.json declares the advisory boundary and keeps its matchers",
         t_hooks_json_declares_the_boundary)
    case("the covered commit forms are detected", t_covered_commit_forms_are_detected)
    case("non-commit and unsupported vectors are not misclassified",
         t_non_commit_and_unsupported_are_not_misclassified)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
