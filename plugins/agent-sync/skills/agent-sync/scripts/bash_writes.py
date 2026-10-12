#!/usr/bin/env python3
"""Which files a shell command writes — the guard hook's Bash parse (agent-sync AS-09).

A separate file on purpose: the PreToolUse hook runs in EVERY session, and starting the
6,000-line coordinator to parse a command costs several times what this file does.
`python3 bash_writes.py < payload.json` prints one absolute path per line.

What it reads, and nothing more (each shape is a case in `test/coordination_test.py`):

  redirections   `>`, `>>`, `>|`, `&>`, `&>>`, `>& FILE` (`2>&1` is not a file)
  programs       `tee [-a]`, `sed -i` (GNU and BSD spellings), `cp`, `mv` (`-t DIR` too)
  prefixes       `VAR=x cmd`, `env [-i] A=1 cmd`, `sudo [-u U] cmd`, `nohup`, `time`,
                 `command`, `exec`
  structure      `&&`, `||`, `;`, `|`, `|&`, `&`, newlines, `( … )` subshells; quoted
                 `;`/`|` stay inside their word; heredoc BODIES are data, not commands
  directory      `cd DIR` is followed, inside a subshell only until its `)`

What it does NOT read, and skips (fail open — a shell parser is not a sandbox):
a target holding `$`, a backtick or a glob (`> $OUT`, `> *.md`); a relative target after
`cd -`, `cd` alone or `cd $X` (the directory is no longer known, so it stops following);
`perl -i`, `awk`, `dd`, `install`, `rsync`, and any language runtime's own file write.
"""
from __future__ import annotations

import json
import os
import re
import sys

_WRITE_SHAPE = re.compile(r">|\btee\b|\bsed\b[^|;&]*\s-i|\bsed\b[^|;&]*--in-place|\b(cp|mv)\b")
_NOT_A_FILE = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}

# Longest first: the tokeniser takes the longest operator that matches at a position.
_OPS = ("&>>", "<<<", "<<-", ">>", ">|", "&>", ">&", "<&", "<<", "<>", "&&", "||", "|&", ";;",
        ">", "<", "|", "&", ";", "(", ")")
_SEPARATORS = {"&&", "||", ";", "|", "|&", "&", ";;", "\n"}
_WRITE_REDIRS = {">", ">>", ">|", "&>", "&>>"}
_READ_REDIRS = {"<", "<<<", "<&", "<>"}
_PREFIXES = {"nohup", "time", "command", "exec", "builtin"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_UNRESOLVED = re.compile(r"[$`*?\[]")


class _Tok:
    __slots__ = ("text", "op", "glued")

    def __init__(self, text: str, op: bool, glued: bool = False) -> None:
        self.text = text      # the word with its quotes removed, or the operator
        self.op = op          # an UNQUOTED operator — `";"` in quotes is a word
        self.glued = glued    # a word with no space before the operator that follows it

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ("OP(%r)" if self.op else "W(%r)") % self.text


def _tokens(cmd: str) -> list[_Tok]:
    """Split a command line into words and unquoted operators, the way a shell reads it.

    Tokenising comes FIRST and segmenting second: splitting the raw string on `;` or `|`
    broke quoted text apart, and splitting on newlines before reading a heredoc made its
    body into commands — `cat > notes.md <<'EOF'` followed by `> docs/ROADMAP.md` refused
    a harmless write to a guarded name that was only text. A heredoc body is consumed here,
    at the newline that ends its opening line, and never becomes a token.
    """
    out: list[_Tok] = []
    pending_heredocs: list[tuple[str, bool]] = []
    i, n = 0, len(cmd)
    word: list[str] = []
    in_word = False

    def end_word(glued: bool = False) -> None:
        nonlocal word, in_word
        if in_word:
            out.append(_Tok("".join(word), False, glued))
        word, in_word = [], False

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n":               # line continuation
                i += 2
                continue
            word.append(cmd[i + 1])
            in_word = True
            i += 2
            continue
        if c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise ValueError("unclosed single quote")
            word.append(cmd[i + 1:j])
            in_word = True
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n and cmd[j + 1] in '"\\$`\n':
                    buf.append(cmd[j + 1])
                    j += 2
                    continue
                buf.append(cmd[j])
                j += 1
            if j >= n:
                raise ValueError("unclosed double quote")
            word.append("".join(buf))
            in_word = True
            i = j + 1
            continue
        if c == "#" and not in_word:
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        if c == "\n":
            end_word()
            out.append(_Tok("\n", True))
            i += 1
            # The bodies of every heredoc opened on the line just ended.
            for delim, strip_tabs in pending_heredocs:
                while i < n:
                    j = cmd.find("\n", i)
                    line = cmd[i:] if j < 0 else cmd[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
            pending_heredocs = []
            continue
        if c in " \t\r":
            end_word()
            i += 1
            continue
        op = next((o for o in _OPS if cmd.startswith(o, i)), None)
        if op is not None:
            end_word(glued=True)
            out.append(_Tok(op, True))
            i += len(op)
            if op in ("<<", "<<-"):
                # The delimiter is the next word, its quotes removed.
                while i < n and cmd[i] in " \t":
                    i += 1
                m = re.match(r"""(['"]?)([^\s'";|&<>()]+)\1""", cmd[i:])
                if m:
                    pending_heredocs.append((m.group(2), op == "<<-"))
                    out.append(_Tok(m.group(2), False))
                    i += m.end()
            continue
        word.append(c)
        in_word = True
        i += 1
    end_word()
    return out


def _strip_prefixes(words: list[str]) -> list[str]:
    """`A=1 env B=2 sudo -u x sed -i …` → `sed -i …`."""
    k = 0
    while k < len(words):
        w = words[k]
        if _ASSIGN.match(w):
            k += 1
            continue
        base = os.path.basename(w)
        if base == "env":
            k += 1
            while k < len(words) and (words[k].startswith("-") or _ASSIGN.match(words[k])):
                k += 2 if words[k] in ("-u", "--unset", "-C", "--chdir", "-S") else 1
            continue
        if base == "sudo":
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in ("-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t") else 1
            continue
        if base in _PREFIXES:
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 1
            continue
        break
    return words[k:]


def _program_targets(words: list[str], here: str | None) -> list[str]:
    if not words:
        return []
    prog, args = os.path.basename(words[0]), words[1:]
    if prog == "tee":
        return [a for a in args if not a.startswith("-")]
    if prog == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place")
                             for a in args):
        has_e = any(a in ("-e", "--expression", "-f", "--file") or
                    (a.startswith("-e") and len(a) > 2) for a in args)
        files, skip_next, script_seen = [], False, has_e
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a in ("-e", "--expression", "-f", "--file"):
                skip_next = True
                continue
            if a.startswith("-") or a == "":
                continue
            if not script_seen:
                script_seen = True
                continue
            files.append(a)
        return files
    if prog in ("cp", "mv"):
        target_dir, plain, k = None, [], 0
        while k < len(args):
            a = args[k]
            if a in ("-t", "--target-directory") and k + 1 < len(args):
                target_dir = args[k + 1]
                k += 2
                continue
            if a.startswith("--target-directory="):
                target_dir = a.split("=", 1)[1]
            elif not a.startswith("-"):
                plain.append(a)
            k += 1
        if target_dir is not None:
            return [os.path.join(target_dir, os.path.basename(s.rstrip("/"))) for s in plain]
        if len(plain) < 2:
            return []
        dest = plain[-1]
        if here is not None:
            dabs = dest if os.path.isabs(dest) else os.path.join(here, dest)
            if os.path.isdir(dabs):
                return [os.path.join(dest, os.path.basename(s.rstrip("/"))) for s in plain[:-1]]
        return [dest]
    return []


def bash_write_targets(cmd: str, cwd: str = ".") -> list[str]:
    """The files a shell command writes, as far as this parse can tell (AS-09).

    Returns absolute paths, de-duplicated, in order. A command that does not tokenise
    (an unclosed quote) yields nothing — fail open, as the module docstring says.
    """
    try:
        toks = _tokens(cmd or "")
    except ValueError:
        return []
    found: list[tuple[str, str | None]] = []   # (target, directory it is relative to)
    here: str | None = cwd
    stack: list[str | None] = []
    words: list[str] = []

    def flush() -> None:
        nonlocal here, words
        w = _strip_prefixes(words)
        words = []
        if not w:
            return
        if w[0] == "cd":
            dest = w[1] if len(w) > 1 else None
            if dest is None or dest == "-" or _UNRESOLVED.search(dest) or dest.startswith("~"):
                here = None      # where the shell is now is not known — stop following
            elif os.path.isabs(dest):
                here = dest
            elif here is not None:
                here = os.path.join(here, dest)
            return
        found.extend((t, here) for t in _program_targets(w, here))

    k = 0
    while k < len(toks):
        t = toks[k]
        if t.op and t.text in _SEPARATORS:
            flush()
            k += 1
            continue
        if t.op and t.text == "(":
            flush()
            stack.append(here)
            k += 1
            continue
        if t.op and t.text == ")":
            flush()
            here = stack.pop() if stack else here
            k += 1
            continue
        if t.op and (t.text in _WRITE_REDIRS or t.text == ">&"):
            # A file-descriptor number glued to the operator (`2>`) is not an argument.
            if words and k > 0 and not toks[k - 1].op and toks[k - 1].glued and \
                    toks[k - 1].text.isdigit():
                words.pop()
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            if nxt is not None and not nxt.op:
                target = nxt.text
                dup = t.text == ">&" and (target.isdigit() or target == "-")
                if not dup:
                    found.append((target, here))
                k += 2
            else:
                k += 1
            continue
        if t.op and t.text in _READ_REDIRS | {"<<", "<<-"}:
            if words and k > 0 and not toks[k - 1].op and toks[k - 1].glued and \
                    toks[k - 1].text.isdigit():
                words.pop()
            k += 2
            continue
        if not t.op:
            words.append(t.text)
        k += 1
    flush()

    out: list[str] = []
    for f, base in found:
        if not f or f in _NOT_A_FILE or _UNRESOLVED.search(f):
            continue
        if f == "~" or f.startswith("~/"):
            f = os.path.expanduser(f)
        if os.path.isabs(f):
            a = f
        elif base is None:
            continue          # relative to a directory this parse no longer knows
        else:
            a = os.path.join(base, f)
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
