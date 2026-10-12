#!/usr/bin/env python3
"""Which files a shell command writes — the guard hook's Bash parse (agent-sync AS-09).

A separate file on purpose: the PreToolUse hook runs in EVERY session, and starting the
6,000-line coordinator to parse a command costs several times what this file does.
`python3 bash_writes.py < payload.json` prints one absolute path per line.
"""
from __future__ import annotations

import json
import os
import re
import sys

_WRITE_SHAPE = re.compile(r">|\btee\b|\bsed\b[^|;&]*\s-i|\bsed\b[^|;&]*--in-place|\b(cp|mv)\b")
_NOT_A_FILE = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}


def bash_write_targets(cmd: str, cwd: str = ".") -> list[str]:
    """The files a shell command writes, as far as a conservative parse can tell (AS-09).

    The PreToolUse guard saw Edit/Write and a parsed `git commit`, so a guarded file
    written with `cat >`, `sed -i` or `tee` reached disk unchecked. This reads the shapes an
    agent actually uses — `>`/`>>` redirections, `tee [-a]`, `sed -i`, `cp`/`mv` — per
    segment of a `&&`/`||`/`;`/`|` chain, following a leading `cd`. Anything it cannot parse
    is skipped (fail open), because a shell parser is not a sandbox: a Python `write_text`
    or an `awk` script writing a file stays outside, and the hook says so.
    Returns absolute paths, de-duplicated, in order.
    """
    import shlex
    out: list[str] = []
    here = cwd
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd or ""):
        try:
            lex = shlex.shlex(seg, posix=True, punctuation_chars="<>&")
            lex.whitespace_split = True
            toks = list(lex)
        except ValueError:
            continue
        if not toks:
            continue
        if toks[0] == "cd" and len(toks) > 1:
            here = toks[1] if os.path.isabs(toks[1]) else os.path.join(here, toks[1])
            continue
        found: list[str] = []
        k = 0
        words: list[str] = []
        while k < len(toks):
            t = toks[k]
            if t in (">", ">>", ">|") and k + 1 < len(toks):
                prev = toks[k - 1] if k else ""
                target = toks[k + 1]
                if not target.startswith("&") and not (prev.isdigit() and target in _NOT_A_FILE):
                    found.append(target)
                k += 2
                continue
            if t in ("<", "<<", "<<<", "&", ">&", "<&") and k + 1 < len(toks):
                k += 2 if t != "&" else 1
                continue
            words.append(t)
            k += 1
        # drop a trailing fd number that belonged to a redirection (`2> x`)
        words = [w for w in words if w]
        if words:
            prog = os.path.basename(words[0])
            args = [w for w in words[1:]]
            if prog == "tee":
                found += [a for a in args if not a.startswith("-")]
            elif prog == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place")
                                       for a in args):
                has_e = any(a in ("-e", "--expression") or a.startswith("-e") for a in args)
                files, skip_next, script_seen = [], False, has_e
                for a in args:
                    if skip_next:
                        skip_next = False
                        continue
                    if a in ("-e", "--expression", "-f", "--file"):
                        skip_next = True
                        continue
                    if a.startswith("-"):
                        continue
                    if not script_seen:
                        script_seen = True
                        continue
                    files.append(a)
                found += files
            elif prog in ("cp", "mv"):
                plain = [a for a in args if not a.startswith("-")]
                if len(plain) >= 2:
                    dest = plain[-1]
                    dabs = dest if os.path.isabs(dest) else os.path.join(here, dest)
                    if os.path.isdir(dabs):
                        found += [os.path.join(dest, os.path.basename(src.rstrip("/")))
                                  for src in plain[:-1]]
                    else:
                        found.append(dest)
        for f in found:
            if f in _NOT_A_FILE or not f:
                continue
            a = f if os.path.isabs(f) else os.path.join(here, f)
            a = os.path.normpath(os.path.abspath(a))
            if a not in out:
                out.append(a)
    return out


def main() -> int:
    try:
        d = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(d, dict):
        return 0
    cmd = (d.get("tool_input") or {}).get("command") or ""
    if not _WRITE_SHAPE.search(cmd):
        return 0
    for t in bash_write_targets(cmd, d.get("cwd") or os.getcwd()):
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
