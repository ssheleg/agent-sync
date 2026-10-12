#!/usr/bin/env python3
"""Which files a shell command writes — the guard hook's Bash parse (agent-sync AS-09).

A separate file on purpose: the PreToolUse hook runs in EVERY session, and starting the
6,000-line coordinator to parse a command costs several times what this file does.

  python3 bash_writes.py < payload.json            one absolute path per line
  python3 bash_writes.py --guarded < payload.json  `<owner>\t<path>` per line, only for paths
                                                   inside a coordinated repository that match
                                                   its guardedFiles (seam S5/S8)
  python3 bash_writes.py --commit < payload.json   `<is_commit> <repo>`, then the absolute
                                                   paths the SAME command stages (AS-16)

What it reads, and nothing more (each shape is a case in `test/coordination_test.py`):

  redirections   `>`, `>>`, `>|`, `&>`, `&>>`, `>& FILE`, `<>` (read-write opens the file
                 for writing); `2>&1` is not a file
  programs       `tee [-a]`, `sed -i` (GNU and BSD spellings, `-e`/`--expression=`/`-f`
                 scripts), `cp`, `mv` (`-t DIR` too; `mv` writes its SOURCE too — moving a
                 guarded file away is a write to it)
  prefixes       `VAR=x cmd`, `env [-i] [-C DIR] A=1 cmd`, `sudo [-u U] cmd`, `nohup`,
                 `time`, `command`, `exec`, `timeout [opts] DUR`, `nice [-n N]`,
                 `stdbuf -oL`, `xargs [opts]` (its explicit arguments only)
  structure      `&&`, `||`, `;`, `|`, `|&`, `&`, newlines, `( … )` subshells, `<( … )` and
                 `>( … )` process substitutions (commands inside, the outer command resumes
                 after); quoted `;`/`|` stay inside their word; heredoc BODIES are data, not
                 commands, whatever the delimiter's quoting (`'EOF'`, `"EOF"`, `\\EOF`)
  directory      `cd [-L|-P] DIR` is followed, inside a subshell only until its `)`; a `cd`
                 in a pipeline or behind `&` runs in a subshell and changes nothing

What it does NOT read, and skips (UNPARSED — fail open; a shell parser is not a sandbox):
a target holding `$`, a backtick or a glob (`> $OUT`, `> *.md`); a relative target after
`cd -`, `cd` alone or `cd $X` (the directory is no longer known, so it stops following);
the arguments `xargs` reads from stdin; `env -S`; `perl -i`, `awk`, `dd`, `install`,
`rsync`, and any language runtime's own file write.
"""
from __future__ import annotations

import json
import os
import re
import sys

# A cheap test before the parse. `sed` alone, not `sed … -i`: the old `\bsed\b[^|;&]*\s-i`
# stopped at a quoted `;` and missed `sed 's/;/x/' -i f` (risk c). The tokeniser decides.
_WRITE_SHAPE = re.compile(r">|\btee\b|\bsed\b|\b(cp|mv)\b")
_NOT_A_FILE = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}

# Longest first: the tokeniser takes the longest operator that matches at a position.
_OPS = ("&>>", "<<<", "<<-", ">>", ">|", "&>", ">&", "<&", "<<", "<>", "<(", ">(", "&&", "||",
        "|&", ";;", ">", "<", "|", "&", ";", "(", ")")
_SEPARATORS = {"&&", "||", ";", "|", "|&", "&", ";;", "\n"}
# A `cd` on either side of one of these runs in a subshell (a pipeline stage, a background
# job): the shell that runs the next command never moved.
_SUBSHELL_SEPS = {"|", "|&", "&"}
_WRITE_REDIRS = {">", ">>", ">|", "&>", "&>>", "<>"}
_READ_REDIRS = {"<", "<<<", "<&"}
_PROCSUB = {"<(", ">("}
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
                # Any quoting in the delimiter word — `'EOF'`, `"EOF"`, `\EOF`, `E"O"F` — is
                # removed to give the line that ends the body. `\EOF` kept its backslash, no
                # line ever matched it, and the rest of the command became heredoc body.
                m = re.match(r"""[^\s;|&<>()]+""", cmd[i:])
                if m:
                    delim = re.sub(r"""[\\'"]""", "", m.group(0))
                    pending_heredocs.append((delim, op == "<<-"))
                    out.append(_Tok(delim, False))
                    i += m.end()
            continue
        word.append(c)
        in_word = True
        i += 1
    end_word()
    return out


_TIMEOUT_VALUED = {"-s", "--signal", "-k", "--kill-after"}
_XARGS_VALUED = {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--arg-file", "--delimiter",
                 "--max-args", "--max-procs", "--max-lines", "--max-chars", "--eof",
                 "--process-slot-var"}
_STDBUF_VALUED = {"-i", "-o", "-e", "--input", "--output", "--error"}


def _strip_prefixes(words: list[str]) -> tuple[list[str], str | None]:
    """`A=1 env B=2 sudo -u x timeout 5 sed -i …` → (`sed -i …`, None).

    The second value is the directory an `env -C DIR` / `env --chdir=DIR` runs the command
    in, or None. The prefixes are the ones agents put in front of a write: each one's options
    are skipped by its own grammar, so `timeout 5 tee f` does not read `5` as the program.
    """
    k, chdir = 0, None
    while k < len(words):
        w = words[k]
        if _ASSIGN.match(w):
            k += 1
            continue
        base = os.path.basename(w)
        if base == "env":
            k += 1
            while k < len(words) and (words[k].startswith("-") or _ASSIGN.match(words[k])):
                o = words[k]
                if o in ("-C", "--chdir") and k + 1 < len(words):
                    chdir = words[k + 1]
                    k += 2
                elif o.startswith("--chdir="):
                    chdir = o.split("=", 1)[1]
                    k += 1
                elif o in ("-u", "--unset", "-S", "--split-string"):
                    k += 2
                else:
                    k += 1
            continue
        if base == "sudo":
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in ("-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D") else 1
            continue
        if base == "timeout":
            k += 1
            while k < len(words) and words[k].startswith("-") and words[k] != "-":
                k += 2 if words[k] in _TIMEOUT_VALUED else 1
            k += 1                                   # the duration
            continue
        if base == "nice":
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in ("-n", "--adjustment") else 1
            continue
        if base == "stdbuf":
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in _STDBUF_VALUED else 1
            continue
        if base == "xargs":
            # Its explicit arguments are parsed; the ones it reads from stdin are UNPARSED.
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 2 if words[k] in _XARGS_VALUED else 1
            continue
        if base in _PREFIXES:
            k += 1
            while k < len(words) and words[k].startswith("-"):
                k += 1
            continue
        break
    return words[k:], chdir


def _resolve(path: str, here: str | None) -> str | None:
    if os.path.isabs(path):
        return path
    if path == "~" or path.startswith("~/"):
        return os.path.expanduser(path)
    return None if here is None else os.path.join(here, path)


def _program_targets(words: list[str], here: str | None) -> list[str]:
    if not words:
        return []
    prog, args = os.path.basename(words[0]), words[1:]
    if prog == "tee":
        return [a for a in args if not a.startswith("-")]
    if prog == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place")
                             for a in args):
        # A script given by -e/--expression/-f/--file (either spelling) means every plain
        # argument is a file; otherwise the first plain argument is the script.
        has_e = any(a in ("-e", "--expression", "-f", "--file") or
                    a.startswith("--expression=") or a.startswith("--file=") or
                    (a.startswith("-e") and len(a) > 2) or
                    (a.startswith("-f") and len(a) > 2) for a in args)
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
        # `mv` removes its source: a guarded register moved away is a write to it (seam S7).
        moved = [s.rstrip("/") or s for s in plain] if prog == "mv" else []
        if target_dir is not None:
            return [os.path.join(target_dir, os.path.basename(s.rstrip("/"))) for s in plain] + moved
        if len(plain) < 2:
            return []
        dest, sources = plain[-1], plain[:-1]
        if prog == "mv":
            moved = moved[:-1]
        # An absolute destination is a directory or not whatever this parse knows of the
        # current directory; only a relative one needs `here`.
        dabs = _resolve(dest, here)
        if dabs is not None and os.path.isdir(dabs):
            return [os.path.join(dest, os.path.basename(s.rstrip("/"))) for s in sources] + moved
        return [dest] + moved
    return []


def bash_write_targets(cmd: str, cwd: str = ".") -> list[str]:
    """The files a shell command writes, as far as this parse can tell (AS-09).

    Returns absolute paths, de-duplicated, in order. A command that does not tokenise
    (an unclosed quote) yields nothing — fail open, as the module docstring says.
    """
    return _walk(cmd, cwd)[0]


def _walk(cmd: str, cwd: str) -> tuple[list[str], list[tuple[list[str], str | None]]]:
    """(write targets, [(command words, directory it runs in)]) — one walk for both callers."""
    try:
        toks = _tokens(cmd or "")
    except ValueError:
        return [], []
    found: list[tuple[str, str | None]] = []   # (target, directory it is relative to)
    commands: list[tuple[list[str], str | None]] = []
    here: str | None = cwd
    # ("sub", here, None) for `( … )`; ("proc", here, outer words) for `<( … )` / `>( … )`.
    stack: list[tuple[str, str | None, list[str] | None]] = []
    words: list[str] = []
    prev_sep: str | None = None

    def flush(sep_after: str | None) -> None:
        nonlocal here, words
        w, chdir = _strip_prefixes(words)
        words = []
        if not w:
            return
        if w[0] == "cd":
            if prev_sep in _SUBSHELL_SEPS or sep_after in _SUBSHELL_SEPS:
                return           # a pipeline stage or a background job: this shell never moved
            rest = [a for a in w[1:] if a == "-" or not a.startswith("-")]
            dest = rest[0] if rest else None
            if dest is None or dest == "-" or _UNRESOLVED.search(dest) or dest.startswith("~"):
                here = None      # where the shell is now is not known — stop following
            elif os.path.isabs(dest):
                here = dest
            elif here is not None:
                here = os.path.join(here, dest)
            return
        base = here
        if chdir is not None:
            base = None if _UNRESOLVED.search(chdir) else _resolve(chdir, here)
        commands.append((w, base))
        found.extend((t, base) for t in _program_targets(w, base))

    k = 0
    while k < len(toks):
        t = toks[k]
        if t.op and t.text in _SEPARATORS:
            flush(t.text)
            prev_sep = t.text
            k += 1
            continue
        if t.op and t.text in _PROCSUB:
            # The outer command is not over: keep its words, read the inner commands, and
            # resume the outer one at the matching `)`.
            stack.append(("proc", here, words))
            words, prev_sep = [], None
            k += 1
            continue
        if t.op and t.text == "(":
            flush(None)
            stack.append(("sub", here, None))
            prev_sep = None
            k += 1
            continue
        if t.op and t.text == ")":
            flush(None)
            if stack:
                kind, saved_here, saved_words = stack.pop()
                here = saved_here
                if kind == "proc":
                    words = saved_words or []
            prev_sep = None
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
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            k += 2 if nxt is not None and not nxt.op else 1
            continue
        if not t.op:
            words.append(t.text)
        k += 1
    flush(None)

    out: list[str] = []
    for f, base in found:
        if not f or f in _NOT_A_FILE or _UNRESOLVED.search(f):
            continue
        a = _resolve(f, base)
        if a is None:
            continue          # relative to a directory this parse no longer knows
        a = os.path.normpath(os.path.abspath(a))
        if a not in out:
            out.append(a)
    return out, commands


# ------------------------------------------------------------------ owner and guardedFiles

def matches_glob(rel: str, pattern: str) -> bool:
    """agent_sync.py's `matches_glob`, copied — this file must not import the coordinator.
    `test/coordination_test.py` asserts the two agree on every pattern shape."""
    parts: list[str] = []
    for seg in pattern.strip("/").split("/"):
        if seg == "**":
            parts.append("(?:[^/]+/)*")
            continue
        out = ""
        for ch in seg:
            out += "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
        parts.append(out + "/")
    body = "".join(parts)
    return re.match("^" + (body[:-1] if body.endswith("/") else body) + "$", rel) is not None


def owner_of(path: str) -> str | None:
    """The git toplevel holding `path`, found by walking up to a `.git` entry — no process
    spawned. The nearest EXISTING ancestor starts the walk: a write may create its directory."""
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    d = os.path.realpath(d)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def guarded_targets(targets: list[str]) -> list[tuple[str, str]]:
    """(owner, target) for each target the coordinator must be asked about (seam S5/S8).

    The guard's question belongs to the repository that OWNS the write, so the session's
    own configuration no longer gates the parse: a session rooted outside every coordinated
    repository that `cd`s into one is checked too. Only a target that is certainly not
    guarded is dropped — no `.claude/agent-sync.json` in its repository, or no pattern
    matching it. A config this file cannot read keeps the target: the coordinator then
    fails closed on it, as it always has.
    """
    out: list[tuple[str, str]] = []
    configs: dict[str, list[str] | None] = {}
    for t in targets:
        top = owner_of(t)
        if top is None:
            continue
        if top not in configs:
            cfg_path = os.path.join(top, ".claude", "agent-sync.json")
            if not os.path.isfile(cfg_path):
                configs[top] = []
            else:
                try:
                    with open(cfg_path, encoding="utf-8") as fh:
                        cfg = json.load(fh)
                    pats = cfg.get("guardedFiles") if isinstance(cfg, dict) else None
                    configs[top] = [p for p in pats if isinstance(p, str)] \
                        if isinstance(pats, list) else (None if not isinstance(cfg, dict) else [])
                except (OSError, ValueError):
                    configs[top] = None              # unreadable: let the coordinator decide
        pats = configs[top]
        if pats is None:
            out.append((top, t))
            continue
        rel = os.path.relpath(os.path.realpath(t), top)
        if any(matches_glob(rel, p) for p in pats):
            out.append((top, t))
    return out


# ------------------------------------------------------------------ AS-16: what a commit stages

_COMMIT_WORDS = re.compile(r"\bgit\b[^\n]*\bcommit\b")
_HEREDOC_OP = re.compile(r"""<<(-?)[ \t]*(?:'([^'\n]*)'|"([^"\n]*)"|\\?([^\s;&|()<>'"]+))""")
_SUB = "$SUB"                        # what a removed `$(…)` / backtick span leaves behind
_LOOSE_WORD = re.compile(r"""\"([^\"]*)\"?|'([^']*)'?|(\S+)""")
_SEGMENT_KEYWORDS = {"{", "!", "then", "do", "else"}
_COMMIT_VALUED_SHORT = set("mFCct")
_COMMIT_VALUED_LONG = {"--message", "--file", "--author", "--date", "--template",
                       "--reuse-message", "--reedit-message", "--fixup", "--squash",
                       "--cleanup", "--trailer", "--pathspec-from-file"}
# `--gpg-sign` is absent on purpose: its key id attaches only as `--gpg-sign=<keyid>`, so the
# word after a bare `--gpg-sign` is a pathspec or another option, never its value.


def _git_sub(w: list[str]) -> tuple[str | None, str | None, list[str]]:
    """(-C directory, subcommand, its arguments) of a `git …` command, else (None, None, [])."""
    if not w or os.path.basename(w[0]) != "git":
        return None, None, []
    k, r = 1, None
    while k < len(w):
        t = w[k]
        if t == "-C" and k + 1 < len(w):
            r = w[k + 1] if r is None or os.path.isabs(w[k + 1]) else os.path.join(r, w[k + 1])
            k += 2
            continue
        if t in ("-c", "--namespace", "--git-dir", "--work-tree") and k + 1 < len(w):
            k += 2
            continue
        if t.startswith("-"):
            k += 1
            continue
        return r, t, w[k + 1:]
    return r, None, []


def commit_plan(cmd: str, cwd: str) -> tuple[bool, str | None, list[tuple[str, str, list[str]]]]:
    """(is a commit, the directory it runs in, [(dir, kind, pathspecs)] it also stages).

    AS-16: the hook reads the index BEFORE the command runs, so `git add X && git commit`
    in one call reached the commit with X not yet staged and passed. The staging the same
    command does is therefore read from the command: `git add <specs>`, `git add -A/-u`,
    `git commit -a`, `git commit <paths>`. kind is "add" (modified + untracked under the
    specs), "tracked" (modified tracked files under the specs) — an empty spec list means
    the whole tree. Only staging that comes BEFORE the commit in the command counts.
    """
    try:
        _tokens(cmd or "")
    except ValueError:
        # The tokenizer closes a "…" string at the first inner quote, so a commit message
        # heredoc inside "$(…)" with an odd number of quotes does not parse (AS-18). Failing
        # open there skipped the commit check entirely. What the same command stages is not
        # known; the hook still reads the index of the repository the commit runs in.
        repo = _untokenised_commit_dir(cmd or "", cwd)
        return (True, repo, []) if repo is not None else (False, None, [])
    _targets, commands = _walk(cmd, cwd)
    stages: list[tuple[str, str, list[str]]] = []
    for w, base in commands:
        r, sub, args = _git_sub(w)
        if sub is None:
            continue
        # None when the directory is unknown (after `cd -`): the commit is still a commit,
        # and the hook falls back to the session's project for it; staging there is skipped.
        d = r if r is not None and os.path.isabs(r) else \
            (None if base is None else (base if r is None else os.path.join(base, r)))
        if sub in ("add", "stage"):
            if d is None:
                continue
            specs, kind, dry, k = [], "add", False, 0
            while k < len(args):
                a = args[k]
                if a == "--":
                    specs += args[k + 1:]
                    break
                if a in ("-A", "--all", "--no-ignore-removal"):
                    kind, specs = "add-all", specs
                elif a in ("-u", "--update"):
                    kind = "tracked"
                elif a in ("-n", "--dry-run"):
                    dry = True
                elif a.startswith("--pathspec-from-file"):
                    kind = "add-all"
                    if a == "--pathspec-from-file":
                        k += 1
                elif not a.startswith("-"):
                    specs.append(a)
                k += 1
            if dry:
                continue
            if kind == "add-all":
                stages.append((d, "add", [] if not specs else specs))
            elif kind == "tracked":
                stages.append((d, "tracked", specs))
            elif specs:
                stages.append((d, "add", specs))
            continue
        if sub == "commit":
            specs, all_tracked, k = [], False, 0
            while k < len(args):
                a = args[k]
                if a == "--":
                    specs += args[k + 1:]
                    break
                if a in ("-a", "--all"):
                    all_tracked = True
                elif a.startswith("--"):
                    if "=" not in a and a in _COMMIT_VALUED_LONG:
                        k += 1
                elif a.startswith("-") and len(a) > 1:
                    for j, ch in enumerate(a[1:]):
                        if ch == "a":
                            all_tracked = True
                        if ch in _COMMIT_VALUED_SHORT:
                            if j == len(a) - 2:
                                k += 1           # the value is the next word
                            break                # else it is glued: `-mmsg`
                else:
                    specs.append(a)
                k += 1
            if d is not None and all_tracked:
                stages.append((d, "tracked", []))
            if d is not None and specs:
                stages.append((d, "tracked", specs))
            return True, d, stages
    return False, None, []


def _scan(s: str, k: int, sub: bool) -> tuple[str, int, bool]:
    """(command text, index after, closed) of `s` from `k`, quote-aware.

    Each balanced `$(…)` and backtick span is replaced by `$SUB`, each heredoc body (from the
    line after `<<DELIM` / `<<'DELIM'` / `<<-DELIM` through the line that is exactly DELIM)
    and each `#` comment is dropped. With `sub`, the scan is inside a `$(`: a fresh quoting
    context that ends at its matching `)`. An opener whose span never closes ends the text
    there (closed False), so a quote or heredoc that cannot be matched is never read as
    commands.
    """
    out: list[str] = []
    q: str | None = None
    depth, pending, n = 0, [], len(s)
    while k < n:
        ch = s[k]
        if q == "'":
            out.append(ch)
            q = None if ch == "'" else q
            k += 1
            continue
        if ch == "\\" and k + 1 < n:
            out.append(s[k:k + 2])
            k += 2
            continue
        if s.startswith("$(", k):
            _inner, end, ok = _scan(s, k + 2, True)
            if not ok:
                return "".join(out), k, False
            out.append(_SUB)
            k = end
            continue
        if ch == "`":
            j = k + 1
            while j < n and s[j] != "`":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                return "".join(out), k, False
            out.append(_SUB)
            k = j + 1
            continue
        if q == '"':
            out.append(ch)
            q = None if ch == '"' else q
            k += 1
            continue
        if ch in "'\"":
            q = ch
        elif ch == "#" and (k == 0 or s[k - 1] in " \t\n;&|()"):
            j = s.find("\n", k)
            k = n if j < 0 else j
            continue
        elif s.startswith("<<<", k):
            out.append("<<<")
            k += 3
            continue
        elif s.startswith("<<", k):
            m = _HEREDOC_OP.match(s, k)
            if m:
                delim = next(g for g in (m.group(2), m.group(3), m.group(4)) if g is not None)
                pending.append((delim, m.group(1) == "-"))
                out.append(m.group(0))
                k = m.end()
                continue
        elif ch == "\n" and pending:
            out.append("\n")
            k += 1
            for delim, tabs in pending:
                while True:
                    if k >= n:
                        return "".join(out), k, False      # a body that never ends
                    j = s.find("\n", k)
                    line = s[k:n if j < 0 else j]
                    k = n if j < 0 else j + 1
                    if (line.lstrip("\t") if tabs else line) == delim:
                        break
            pending = []
            continue
        elif sub and ch == "(":
            depth += 1
        elif sub and ch == ")":
            if depth == 0:
                return "".join(out), k + 1, True
            depth -= 1
        out.append(ch)
        k += 1
    return "".join(out), k, not sub and not pending


def _command_text(cmd: str) -> str:
    """The part of `cmd` that is commands: continuations joined, substitutions, heredoc
    bodies and comments removed (see `_scan`)."""
    return _scan(cmd.replace("\\\n", ""), 0, False)[0]


def _head_segments(head: str) -> list[str]:
    """`head` split at `;`, `&&`, `||`, `|`, `&`, `(`, `)` and newlines outside quotes. A
    quote left open (the text ended at an unclosed opener) simply runs to its end."""
    segs, cur, q, k = [], [], None, 0
    while k < len(head):
        ch = head[k]
        if q is None and ch == "\\" and k + 1 < len(head):
            cur.append(head[k:k + 2])
            k += 2
            continue
        if q is not None:
            if ch == q:
                q = None
            elif ch == "\\" and q == '"' and k + 1 < len(head):
                cur.append(head[k:k + 2])
                k += 2
                continue
        elif ch in "'\"":
            q = ch
        elif ch in ";&|()\n":
            segs.append("".join(cur))
            cur = []
            k += 1
            continue
        cur.append(ch)
        k += 1
    segs.append("".join(cur))
    return segs


def _untokenised_commit_dir(cmd: str, cwd: str) -> str | None:
    """The directory a `git … commit` COMMAND in an untokenisable `cmd` runs in, else None.

    AS-18 scope (1.22.1): the fallback used to match `git … commit` ANYWHERE, so a PR body or
    a heredoc that merely mentioned "every git commit" was read as a commit in cwd. Command
    text is what `_command_text` leaves: balanced `$(…)` / backtick spans, heredoc bodies and
    comments removed, continuations joined — it ends early only at an opener that never
    closes. (Cutting at the FIRST opener missed `cd "$(…)" && git commit …`.) A commit counts
    only at a segment start (after env assignments / prefixes and `git -C <dir>` / `-c k=v`);
    a preceding `cd <dir>` segment and `-C <dir>` name the repository whose index the hook
    must read, not the payload's cwd.
    """
    head = _command_text(cmd)
    if not _COMMIT_WORDS.search(head):
        return None
    here: str | None = cwd
    for seg in _head_segments(head):
        words = [next(g for g in mt.groups() if g is not None)
                 for mt in _LOOSE_WORD.finditer(seg)]
        while words and words[0] in _SEGMENT_KEYWORDS:
            words = words[1:]
        if not words or words[0].startswith("#"):
            continue
        words, chdir = _strip_prefixes(words)
        if not words:
            continue
        if words[0] == "cd":
            target = words[1] if len(words) > 1 else "~"
            here = None if target == "-" or _UNRESOLVED.search(target) \
                else _resolve(target, here)
            continue
        r, sub, _args = _git_sub(words)
        if sub != "commit":
            continue
        base = here if chdir is None else _resolve(chdir, here)
        d = base if r is None else _resolve(r, base)
        # A directory this cannot name (`cd -`, a `$VAR`, a path that does not exist) falls
        # back to the payload's cwd: checking the wrong index is the lesser failure than
        # checking none, and guard.sh applies the same fallback to a repo that is not a dir.
        if d is None or not os.path.isdir(d):
            return cwd
        return os.path.normpath(d)
    return None


def staged_by_command(stages: list[tuple[str, str, list[str]]]) -> list[str]:
    """Absolute paths the command's own staging would add — asked of git, which knows the
    pathspec grammar (`.`, a directory, a quoted glob) better than any copy of it here."""
    import subprocess
    out: list[str] = []
    for d, kind, specs in stages:
        try:
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if not top:
                continue
            flags = ["-m", "-d"] + (["-o", "--exclude-standard"] if kind == "add" else [])
            where = d if specs else top
            r = subprocess.run(["git", "-C", where, "ls-files", "--full-name", "-z"] + flags +
                               (["--"] + specs if specs else []),
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        for name in r.stdout.split("\0"):
            if name:
                p = os.path.join(top, name)
                if p not in out:
                    out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        d = json.load(sys.stdin)
    except (ValueError, OSError):
        d = None
    if not isinstance(d, dict):
        if "--commit" in argv:
            print("0 .")
        return 0
    cmd = (d.get("tool_input") or {}).get("command") or ""
    cwd = d.get("cwd") or os.getcwd()
    if "--commit" in argv:
        is_commit, repo, stages = commit_plan(cmd, cwd)
        print(f"{1 if is_commit else 0} {repo or '.'}")
        if is_commit:
            for p in staged_by_command(stages):
                print(p)
        return 0
    if not _WRITE_SHAPE.search(cmd):
        return 0
    targets = bash_write_targets(cmd, cwd)
    if "--guarded" in argv:
        for top, t in guarded_targets(targets):
            print(f"{top}\t{t}")
        return 0
    for t in targets:
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
