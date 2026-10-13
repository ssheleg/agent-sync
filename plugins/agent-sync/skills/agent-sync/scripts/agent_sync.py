#!/usr/bin/env python3
"""agent-sync — coordination for concurrent agents over a pluggable knowledge cloud.

Stdlib only. Python 3.9+.

Two planes: git is the record plane, the cloud is the coordination plane.
Leases and id reservations are decided by replaying one append-only log, because
no supported backend offers compare-and-swap. Document order is authoritative;
timestamps only expire leases.

Credentials are read from the environment and never appear in argv, a log line,
a journal entry or a rendered board. HTTP goes through urllib inside this
process — there is no subprocess and nothing for another process to read.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import stat
import subprocess
import hashlib
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.22.1"

CONFIG_PATH = Path(".claude/agent-sync.json")
ENV_FILE = Path(".env.agent-sync")
STATE_DIR = Path(".agent-sync")
# How recent an expired lease has to be to earn a line of its own. Anything older
# collapses into one summary that still names the keys. Twenty-four hours is chosen
# because it is the span in which an expired lease can still mean "a run is in
# trouble"; beyond it, it means "somebody's machine was closed".
STALE_DETAIL_SECONDS = 24 * 60 * 60
GENERATED_MARKER = "<!-- agent-sync:generated"
MERGE_LOG_MARKER = "<!-- agent-sync:merge-log -->"
DEFAULT_MERGE_LOG = "docs/MERGES.md"
DEFAULT_MERGE_RETENTION = 7

# What a won lease is actually worth, in one place. Six surfaces used to phrase this
# independently and two of them named the knowledge base as the authority — a role it
# has not held since 1.0.0, when exclusion moved to a primitive the store cannot lose.
# One guarantee described two ways reads as two guarantees, and an operator acts on the
# weaker one.
LEASE_GUARANTEE = {
    "git": ("exclusive across machines",
            "the remote's non-fast-forward rejection is a real compare-and-swap"),
    "local": ("exclusive on this machine, advisory across machines",
              'set `leaseBackend: "git"` if agents run on more than one'),
}


def lease_guarantee(mode: str) -> tuple[str, str]:
    """The headline and the detail for a lease mode. Unknown modes claim nothing."""
    return LEASE_GUARANTEE.get(
        mode, ("NOT a lease — unknown mode, treat this project as unprotected",
               f"'{mode}' is not a known leaseBackend; only {' or '.join(LEASE_GUARANTEE)}"))


def id_allocator(cfg: dict[str, Any], adapter: "Adapter") -> tuple[str | None, str]:
    """Who hands out register ids in this configuration — `(authority, description)`.

    The ONE answer `reserve`, `release_id` and `check` share. `check` used to ask only
    the record plane, while `reserve` also allocated through git refs, so a project with a
    local record plane and `leaseBackend: "git"` was refused for declaring a register that
    `reserve` served race-free — and took its numbers by hand instead.

    - `"git"` — `leaseBackend` is git: a compare-and-swap on `refs/agent-sync/ids/<REG>`
      at the lease remote. Independent of the record plane, exactly like the lease.
    - `"log"` — the record plane orders writes (`atomicAppend` + `totalOrderRead`): an
      optimistic claim over the merged log, confirmed on read-back.
    - `None` — neither. Two clones' appends are ordered by nothing, so any number handed
      out would be a guess another run can make too.

    Order matters and matches `reserve`: in git mode the ref is the allocator even when
    the record plane could order writes, because only the ref is shared across machines.
    Whether the git remote actually exists is a separate question (`git_remote_exists`) —
    a configuration names an allocator; a repository may still lack the remote it names.
    """
    if (cfg.get("leaseBackend") or "local") == "git":
        remote = cfg.get("leaseRemote") or "origin"
        return "git", f"compare-and-swap on refs/agent-sync/ids/* at '{remote}'"
    if adapter.is_lease_authority:
        return "log", f"the {adapter.name} plane's ordered log"
    return None, (f"backend '{adapter.name}' cannot order writes (atomicAppend is false) "
                  "and leaseBackend is not git")


def git_remote_exists(remote: str, root: Path | None = None) -> bool:
    """Whether `remote` is configured in this repository. Local only — no network."""
    return bool(git("remote", "get-url", remote, cwd=root))


LOGS = {
    "claims": "30 Claims",
    "reservations": "40 Reservations",
    "signals": "50 Signals",
    # The as-built record: what agents actually implemented, as they implemented it.
    # Git documentation says how it SHOULD be — written before the code and often
    # without it. This says how it IS, derived from what was really written. They are
    # two source-of-truths answering two different questions, and the gap between them
    # is the finding, not a defect in either.
    "asbuilt": "70 As-built",
}

# Write with "- ", read any bullet. A knowledge base normalises markdown on the way
# in — Outline rewrites "- " to "* " — so a parser anchored to the character we wrote
# rejects every line the server gave back, and the caller sees "lost" instead of
# "unreadable". Be strict in what you emit, liberal in what you accept.
LINE_RE = re.compile(r"^[-*+] `(?P<ts>[^`]+)`(?P<pairs>(?: `[a-z_]+=[^`]*`)+)$")
CANDIDATE_RE = re.compile(r"^[-*+] `")
PAIR_RE = re.compile(r"`([a-z_]+)=([^`]*)`")
MAX_UNPARSEABLE = 0.02

DEFAULT_SETTLE = 3.0
DEFAULT_TTL = 2700
DEFAULT_RENEW = 300
# How many times an id allocation may lose its race before the tool reports the
# contention instead of spinning. Each loss means another allocator moved the state
# between our read and our write — a bounded number of honest retries, never a loop.
RESERVE_RETRIES = 6
# How long a steal section may be held before it is treated as abandoned. It covers two
# filesystem calls, so anything longer than this is a crashed process, not slow work.
STEAL_GRACE = 30
# How many signal identities `status` remembers as already shown. Bounded, with a floor
# timestamp beside it so the entries that fall out are not announced a second time.
SEEN_CAP = 500

# Every key `.claude/agent-sync.json` may carry. One list, and `agent-sync.schema.json`
# must agree with it exactly — the validator asserts that, because the two were already
# a second copy of each other once and disagreed: `check` called `mergeLog` (written by
# `init` itself) and `integrationBranch` (in the schema, in the example, read below)
# unknown keys that "will be ignored". Both statements were false, and the second was an
# instruction: an agent making `check` green deletes working configuration.
CONFIG_KEYS = frozenset({
    "$schema", "backend", "leaseTtlSeconds", "renewIntervalSeconds", "gated",
    "idRegisters", "guardedFiles", "claimTags", "gates", "mirror", "setupFile",
    "leaseBackend", "leaseRemote", "settleSeconds", "integrationBranch", "mergeLog",
    "scheduledIntervalSeconds",
})


# --------------------------------------------------------------------------- utils

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_or_none(ts: str) -> float | None:
    """The instant this string names, or None when it names none.

    `parse_iso` folds an unreadable timestamp into 0.0, and every expiry test then reads
    that as "expired in 1970". For exclusion that is the right answer — a lock whose clock
    cannot be read must not go on holding a key. For residue it is the wrong one: *spent*
    and *unreadable* are different verdicts, and only the first may be cleared.
    """
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def parse_iso(ts: str) -> float:
    at = parse_iso_or_none(ts)
    return 0.0 if at is None else at


# Read-only questions whose answer cannot change during one `guard` process. `guard` runs on
# every write a hook sees, and asked them six times per call — ~95 ms of git spawns in a
# ~150 ms command (seam S5, measured 2026-10-12). None = off: every other command, and every
# in-process caller, asks git each time as before.
_GIT_MEMO: dict[tuple[str, tuple[str, ...]], str] | None = None
_GIT_MEMOABLE = {("rev-parse", "--show-toplevel"), ("config", "--get", "remote.origin.url"),
                 ("rev-parse", "--show-superproject-working-tree")}


def git(*args: str, cwd: Path | None = None) -> str:
    memo_key = None
    if _GIT_MEMO is not None and args in _GIT_MEMOABLE:
        memo_key = (str(cwd) if cwd else os.getcwd(), args)
        if memo_key in _GIT_MEMO:
            return _GIT_MEMO[memo_key]
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=15)
        answer = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""
    if memo_key is not None:
        _GIT_MEMO[memo_key] = answer
    return answer


def project_root() -> Path:
    top = git("rev-parse", "--show-toplevel")
    return Path(top) if top else Path.cwd()


# --------------------------------------------------------------------------- lease names

# A lease key is stored under two names — `refs/agent-sync/leases/<name>` on the remote and
# `.agent-sync/leases/<name>.lock` here — and both are THIS name. Until 1.21.3 they were two
# different lossy slugs (ssheleg/agent-sync#25): the ref kept `.`, the lock stem turned it
# into `-`, and `held()` reported the stem. `acquire T-1.2` came back as `T-1-2`, `release
# --held` released `T-1-2`, printed "released", and the ref stayed on the remote.
#
# The name is the key with every byte outside `[A-Za-z0-9._-]` percent-escaped (UTF-8), and
# a `.` escaped wherever git forbids it in a ref component: first, last, before another `.`,
# and the one that opens a trailing `.lock`. `%` is itself escaped, so the escape is
# unambiguous and `lease_key(lease_name(k)) == k` for every key — a bijection, not a slug.
# A key made only of safe characters is its own name, which is why every ref 1.21.2 pushed
# for such a key is already under the right name.
LEASE_REF_PREFIX = "refs/agent-sync/leases/"
_LEASE_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
# Filename budget: a lock is `<name>.lock`, its guard `<name>.lock.steal`, its temp inode
# `<name>.lock.<pid>.new` — all inside the 255-byte limit of every filesystem this runs on.
LEASE_NAME_MAX = 200
# Characters a key may not carry, because a surface OTHER than the ref and the lock cannot
# carry them back: whitespace splits the `whoami` list and the CLI, a backtick ends a log
# pair (`key=...`), `|` ends a board cell, and a control character ends the log line.
_KEY_FORBIDDEN = re.compile(r"[\s`|\x00-\x1f\x7f]")


def lease_name(key: str) -> str:
    """The one name a key is stored under, as a ref component and as a lock stem."""
    out: list[str] = []
    for i, ch in enumerate(key):
        dot_forbidden = ch == "." and (i == 0 or i == len(key) - 1 or key[i + 1] == ".")
        if ch in _LEASE_SAFE and not dot_forbidden:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    name = "".join(out)
    if name.endswith(".lock"):                 # git refuses a component ending in `.lock`
        name = name[:-len(".lock")] + "%2Elock"
    return name


def lease_key(name: str) -> str:
    """The inverse of `lease_name`. A name with no `%` is its own key — which is also how
    every name 1.21.2 wrote reads back, since its slugs never produced a `%`."""
    if "%" not in name:
        return name
    try:
        return urllib.parse.unquote(name, errors="strict")
    except UnicodeDecodeError:
        return name


def lease_key_problem(key: str) -> str | None:
    """Why `key` cannot be a lease key, or None. Asked by `acquire` before anything is written."""
    if not key:
        return "a lease key cannot be empty"
    bad = _KEY_FORBIDDEN.search(key)
    if bad:
        return (f"lease key {key!r} contains {bad.group()!r} — whitespace, control characters, "
                "a backtick or `|` cannot travel through the log, the board and `whoami` "
                "unchanged, so the key would come back as a different string")
    if len(lease_name(key)) > LEASE_NAME_MAX:
        return (f"lease key {key[:40]!r}… is {len(lease_name(key))} bytes once stored, over the "
                f"{LEASE_NAME_MAX}-byte limit of a lock filename — use a shorter task id")
    return None


def legacy_ref_name(key: str) -> str:
    """The ref component agent-sync <= 1.21.2 pushed for `key`. Read for migration only."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-")


def legacy_lock_stem(key: str) -> str:
    """The lock stem agent-sync <= 1.21.2 wrote for `key`. Read for migration only."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", key)


def head_sha() -> str:
    return git("rev-parse", "--short", "HEAD") or "unknown"


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD") or ""


def worktree_holding(branch: str) -> str | None:
    """The OTHER worktree that has `branch` checked out, or None.

    git refuses to check a branch out twice, and moving a branch's ref under the worktree
    that has it checked out leaves that worktree showing the reverse of every change as
    its own uncommitted edits. Both happen to `merge` run from a linked worktree while the
    main checkout sits on the integration branch — the ordinary layout of several agents
    sharing one repository.
    """
    here = git("rev-parse", "--show-toplevel")
    here_path = Path(here).resolve() if here else None
    path = None
    for line in (git("worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and path is not None:
            if here_path is None or Path(path).resolve() != here_path:
                return path
    return None


def default_branch() -> str:
    """The branch everything integrates into. Asked of the repository, never assumed."""
    ref = git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if ref:
        return ref.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if git("rev-parse", "--verify", "--quiet", cand):
            return cand
    return "main"


def merge_conflicts(target: str, branch: str) -> list[str]:
    """Files that would conflict, decided WITHOUT touching the working tree.

    A merge that starts and then aborts still leaves the operator in a repository they
    did not expect. `git merge-tree` answers the same question in memory. Modern git
    (2.38+) takes --write-tree and reports conflicted paths; older git prints a diff
    where a conflict shows up as marker lines, so both forms are read here.
    """
    r = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", target, branch],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return []
    if r.returncode == 1:
        # Layout: tree oid, the conflicted paths, a blank line, then git's own prose
        # ("Auto-merging …", "CONFLICT (content) …"). Only the paths are the answer;
        # printing the prose as if it were a filename makes the report untrustworthy.
        paths: list[str] = []
        for line in r.stdout.splitlines()[1:]:
            if not line.strip():
                break
            paths.append(line)
        return paths

    base = git("merge-base", target, branch)
    if not base:
        return ["(cannot determine a merge base — unrelated histories)"]
    old = subprocess.run(["git", "merge-tree", base, target, branch],
                         capture_output=True, text=True)
    return ["(conflicting hunks — this git is too old to name the files)"] \
        if "<<<<<<<" in old.stdout else []


def repo_name() -> str:
    url = git("config", "--get", "remote.origin.url")
    if url:
        return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return project_root().name


class Fail(Exception):
    """A failure the caller must see. Never swallowed into a success."""


class NotCoordinated(Fail):
    """This repository has no coordination configured — an ANSWER, not a failure.

    Every command that needs the config still refuses, because it cannot do its work.
    The guard is the exception: with no coordination there is nothing for it to protect,
    so it allows rather than denying every path. A config that EXISTS and cannot be read
    is an ordinary Fail and still denies. (Recovered 2026-10-11 from the unmerged branch
    fix/guard-allows-uncoordinated-repository: guard.sh exits early, but a direct
    `agent_sync.py guard <path>` still exited 2 in a repository with no config.)
    """


_GLOB_CACHE: dict[str, re.Pattern[str]] = {}



def id_pattern(spec: dict) -> str | None:
    """The regex that finds a register's next free id, under either accepted key.

    The script read `nextFreeIdPattern`; every config this family ships writes `pattern`.
    Both are accepted now, because a key mismatch is a documentation problem and should
    not be a crash — but ABSENCE returns None rather than `""`, which is the whole defect:
    `re.search("", text)` matches the empty string at position 0, so `check` took the found
    branch and died with `IndexError: no such group` instead of saying the register has no
    pattern. A component that never received its input approved and then fell over.
    """
    for k in ("nextFreeIdPattern", "pattern"):
        v = spec.get(k)
        if v:
            return v
    return None


def matches_glob(rel: str, pattern: str) -> bool:
    """Repo-root-anchored glob — ONE implementation, for the guard and for `check`.

    They used to have two, and the two disagreed in both directions about the same
    pattern. `Path.match` anchors at the RIGHT, so `docs/DECISIONS.md` also matched
    `vendor/docs/DECISIONS.md` — a file `check` (which enumerates with `glob`) never saw
    and never validated, guarded by a rule nobody wrote. And `Path.match` does not walk
    `**` before Python 3.13, so `docs/**/*.md` guarded less than `check` reported it did.

    A pattern that means two things means nothing, so the translation lives here: `**` is
    zero or more directories, `*` and `?` never cross a separator, everything else is
    literal, and the whole path must match from the repository root.
    """
    rx = _GLOB_CACHE.get(pattern)
    if rx is None:
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
        rx = re.compile("^" + (body[:-1] if body.endswith("/") else body) + "$")
        _GLOB_CACHE[pattern] = rx
    return bool(rx.match(rel.replace(os.sep, "/")))


def _tracked_files(root: Path) -> list[str]:
    """Every candidate path in the repository, walked ONCE.

    `glob_files` used to walk the whole tree per pattern, and `check` calls it for every
    guarded pattern and every claim-tag pattern — five patterns over a 20 000-file
    repository is five full walks. Measured at ~3 s for a single `status`, which since
    1.7.0 runs `check`, and `status` is a `SessionStart` hook. Git's own index is used
    where it exists, because it already excludes everything `.gitignore` does; the walk
    is the fallback for files not yet tracked.
    """
    listed = git("ls-files", "--cached", "--others", "--exclude-standard", cwd=root)
    if listed:
        return listed.split("\n")
    skip = {".git", "node_modules", STATE_DIR.name}
    out: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if skip & set(rel.parts):
            continue
        if path.is_file():
            out.append(str(rel))
    return out


def glob_files(root: Path, pattern: str) -> list[Path]:
    """Every existing file the pattern covers, by the same rule the guard applies."""
    return [root / rel for rel in _tracked_files(root) if matches_glob(rel, pattern)]


def glob_files_many(root: Path, patterns: list[str]) -> dict[str, list[Path]]:
    """The same answer for several patterns, from one walk."""
    files = _tracked_files(root)
    return {p: [root / rel for rel in files if matches_glob(rel, p)] for p in patterns}


# --------------------------------------------------------------------------- config

def find_env_file(root: Path) -> Path | None:
    """Locate .env.agent-sync for this checkout, including from inside a submodule.

    Submodules are separate git repositories, so `project_root()` in one of them is the
    submodule — and the credentials sit in the SUPERPROJECT. Looking only in the local
    root put every submodule agent into degraded `fs` mode, isolated in local files and
    unable to see anyone: three agents entered from one umbrella, coordinating with
    nobody, and each one saying `ungated` while believing it was configured.

    So one credential file serves the whole tree — but only a tree git can vouch for.
    The search is: `AGENT_SYNC_ENV` if set, then the local root, then each superproject
    in turn. It used to continue into **plain parent directories** until something
    matched, which meant a stray `.env.agent-sync` in a home or work directory silently
    configured every project beneath it and pointed them all at one collection — a
    coordination plane shared by projects with nothing to do with each other, discovered
    by nobody, because a found file looks exactly like a configured one.
    """
    explicit = os.environ.get("AGENT_SYNC_ENV")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None

    local = root / ENV_FILE
    if local.exists():
        return local

    # A checkout whose `.git` is a DIRECTORY is no submodule (git absorbs a submodule's git
    # dir into the superproject's `.git/modules` and leaves a `.git` FILE), so it has no
    # superproject to ask about — one git spawn saved on every guard call (seam S5).
    if (root / ".git").is_dir():
        return None
    seen: set[str] = set()
    current = root
    for _ in range(8):                      # a submodule chain, not the whole filesystem
        superproject = git("rev-parse", "--show-superproject-working-tree", cwd=current)
        if not superproject or superproject in seen:
            break
        seen.add(superproject)
        candidate = Path(superproject) / ENV_FILE
        if candidate.exists():
            return candidate
        current = Path(superproject)
    return None


VAULT_REEXEC = "AGENT_SYNC_VAULT_REEXEC"
VAULT_SECRET = "AGENT_SYNC_NOTION_TOKEN"
VAULT_SLOT_CACHE_S = 3600
# A NEGATIVE answer — no slot, or `run` refused — is reused for a minute: long enough that a
# burst of record-plane commands asks once, short enough that a slot the operator just
# created (or a grant just given) is seen almost at once.
VAULT_NEGATIVE_CACHE_S = 60
# The wall-clock start of the hook process, carried across the vault re-exec, so a budget
# measured from "this process started" is not reset by `execv` (risk a, 2026-10-12).
HOOK_T0_ENV = "AGENT_SYNC_HOOK_T0"
_WALL_T0 = time.time()
# Where the record-plane token came from in this process: "env", "vault", "file" or None.
# Assigned once, while None: `status` loads the env file and then builds a Sync() that loads
# it again — by then the file's token IS in the environment, and the second pass used to
# relabel a plaintext token as "from the environment" (unit B1, 2026-10-12).
TOKEN_SOURCE: str | None = None
# The roots whose vault question this process has already settled — asked once, before any
# child starts, never again (risk b): the session path loads the env file twice.
_VAULT_DECIDED: set[str] = set()
# Why the vault was not used although it was asked, for `status` to say so. None = not asked.
VAULT_DEGRADED: str | None = None
# Set by main(): whether THIS subcommand touches the record plane, and the argv it was given.
# Off by default, so importing this module or calling a helper never re-executes anything.
RECORD_PLANE_COMMAND = False
INVOKED_ARGV: list[str] | None = None
# Subcommands that never need the record-plane token. The PreToolUse guard runs on every
# Edit/Write/commit: re-running it under the vault cost a spawn per call, and when the vault
# REFUSED (grants are checked by `run`, not by `where`) the guard exited 2 on every write in
# the repository — a lockout from a token the guard never reads.
#
# `renew` and `release --held` (decided in main()) are on this list too, since 1.22.0's fix
# round 2. The lease they act on is a local lock or a git ref — neither needs the token. What
# they would publish with it is one visibility line in the Notion claims log, and readers of
# that log already apply the TTL. The price of asking was too high for that: renew.sh runs
# after EVERY tool call (two `use` audit rows per call once a slot existed), and SessionEnd's
# whole budget is 2 s, which a vault probe and a re-exec consumed before any lease was
# released. With the token in the plaintext file (degraded) they still publish.
NO_RECORD_PLANE = frozenset({"guard", "write-targets", "whoami", "renew"})


def vault_reexec_argv(backend: str, environ: dict[str, str], argv: list[str], python: str,
                      script: str, use_secret: str | None, project: str,
                      slot_exists: bool) -> list[str] | None:
    """The command to re-run under the Observatory vault, or None (R20). Pure.

    The record-plane token was read from a plaintext `.env.agent-sync` — fifteen of them
    under one operator's `~/DATA` — while the machine's rule is vault-only. The value cannot
    be fetched into this process without passing through something printable, so the tool
    re-runs itself under `use_secret.py run --vault-only`, which puts the value in the
    child's ENVIRONMENT and scrubs it from what the child prints. The value never reaches
    argv. Only when every condition holds: the backend needs the token, nothing set it
    explicitly, this is not already the re-run, the engine is installed, and the vault has
    the slot. Otherwise the file is the fallback, reported as degraded.
    """
    if backend != "notion" or environ.get(VAULT_SECRET) or environ.get(VAULT_REEXEC):
        return None
    if not use_secret or not slot_exists:
        return None
    return [python, use_secret, "run", "--vault-only", project, VAULT_SECRET, "--",
            python, script] + list(argv)


def vault_project(root: Path) -> str:
    """The Observatory project a repository's vault slot is filed under — its folder name.

    `root.name` was wrong twice: a linked worktree (`_wt/agent-sync-w3`) and a renamed
    checkout each have a folder name the vault never heard of, so the slot "did not exist"
    in exactly the checkouts agents work in. The MAIN worktree's folder is the project's;
    a submodule (whose git dir is not a `.git` folder) falls back to its remote's name.
    """
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
    if common:
        p = Path(common)
        if p.name == ".git":
            return p.parent.name
    url = git("remote", "get-url", "origin", cwd=root)
    if url:
        name = re.split(r"[/:]", url.rstrip("/"))[-1]
        name = name[:-4] if name.endswith(".git") else name
        if name:
            return name
    return root.name


def _vault_cache(root: Path) -> tuple[Path, dict[str, Any]]:
    cache = root / STATE_DIR / "vault-slot.json"
    try:
        data = json.loads(cache.read_text())
        return cache, data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return cache, {}


def _vault_cache_write(cache: Path, data: dict[str, Any]) -> None:
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    except OSError:
        pass


def _observatory_use_secret(root: Path) -> str | None:
    """Where `use_secret.py` is — cached an hour beside the slot answer, because asking
    `project-observatory full-path` is a process spawn and record-plane commands run from
    hooks. A cached path that no longer names a file is asked again."""
    cache, data = _vault_cache(root)
    path = data.get("use_secret")
    if isinstance(path, str) and Path(path).is_file() and \
            time.time() - float(data.get("use_secret_at") or 0) < VAULT_SLOT_CACHE_S:
        return path
    try:
        out = subprocess.run(["project-observatory", "full-path"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    found = Path(out.stdout.strip()) / "tools" / "use_secret.py"
    if out.returncode != 0 or not found.is_file():
        return None
    data.update({"use_secret": str(found), "use_secret_at": time.time()})
    _vault_cache_write(cache, data)
    return str(found)


def _vault_slot_exists(root: Path, use_secret: str, project: str, file_has_token: bool) -> bool:
    """`use_secret.py where --vault-only` answers 0 when the slot resolves.

    Only a POSITIVE answer is cached for the hour. A cached "no slot" hid the slot an
    operator had just created for up to an hour, while the plaintext file went on serving
    the token; a negative is now reused only while the file still carries a token — the
    moment that line is deleted, the vault is asked again.
    """
    cache, data = _vault_cache(root)
    slot = (data.get("slots") or {}).get(project) or {}
    age = time.time() - float(slot.get("at") or 0)
    fresh = age < VAULT_SLOT_CACHE_S
    if fresh and slot.get("exists"):
        return True
    if slot.get("exists") is False and (age < VAULT_NEGATIVE_CACHE_S or
                                        (fresh and file_has_token and slot.get("file_token"))):
        return False
    try:
        r = subprocess.run([sys.executable, use_secret, "where", "--vault-only", project,
                            VAULT_SECRET], capture_output=True, text=True, timeout=10)
        exists = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    data.setdefault("slots", {})[project] = {"exists": exists, "at": time.time(),
                                             "file_token": file_has_token}
    _vault_cache_write(cache, data)
    return exists


def _vault_run_refusal(root: Path, use_secret: str, project: str) -> str | None:
    """Ask `use_secret.py run` itself, with a child that does nothing — None when it would run.

    `where` resolves the slot; `run` also checks the grant, so a slot can exist and `run`
    still refuse. The tool used to exec blind on `where`'s answer, and a refusal then ended
    every command — the guard included — with use_secret's exit 2. Never exec on a guess.

    The verdict is cached beside the slot answer: a pass for the hour (as the slot is), a
    refusal for a minute. Every probe is a `use` row in the vault's audit log, and asking it
    before every record-plane command doubled the rows for nothing (risk g, 2026-10-12).
    """
    cache, data = _vault_cache(root)
    slot = (data.get("slots") or {}).get(project) or {}
    now = time.time()
    if now - float(slot.get("run_ok_at") or 0) < VAULT_SLOT_CACHE_S:
        return None
    if slot.get("run_refused") and now - float(slot.get("run_refused_at") or 0) < VAULT_NEGATIVE_CACHE_S:
        return str(slot["run_refused"])
    try:
        r = subprocess.run([sys.executable, use_secret, "run", "--vault-only", project,
                            VAULT_SECRET, "--", sys.executable, "-c", ""],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"use_secret.py could not run ({type(exc).__name__})"
    if r.returncode == 0:
        verdict = None
        slot.update({"run_ok_at": now, "run_refused": None})
    else:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        verdict = lines[-1][:200] if lines else f"use_secret.py run exited {r.returncode}"
        slot.update({"run_ok_at": 0, "run_refused": verdict, "run_refused_at": now})
    data.setdefault("slots", {})[project] = slot
    _vault_cache_write(cache, data)
    return verdict


def _configured_backend(root: Path) -> str:
    try:
        cfg = json.loads((root / CONFIG_PATH).read_text())
        return str(cfg.get("backend") or "") if isinstance(cfg, dict) else ""
    except (OSError, ValueError):
        return ""


def _maybe_reexec_under_vault(root: Path, file_backend: str | None, file_has_token: bool) -> None:
    global TOKEN_SOURCE, VAULT_DEGRADED
    if os.environ.get(VAULT_SECRET):
        if TOKEN_SOURCE is None:
            TOKEN_SOURCE = "vault" if os.environ.get(VAULT_REEXEC) else "env"
        return
    if not RECORD_PLANE_COMMAND or os.environ.get(VAULT_REEXEC):
        return
    # Decided once per root per process. The session path loads the env file before its
    # children start and again in Sync(): a second probe was a second audit row, and an exec
    # decided the second time would have replaced a process whose children were already out.
    try:
        decided_key = str(root.resolve())
    except OSError:
        decided_key = str(root)
    if decided_key in _VAULT_DECIDED:
        return
    _VAULT_DECIDED.add(decided_key)
    backend = os.environ.get("AGENT_SYNC_BACKEND") or file_backend or _configured_backend(root)
    if backend != "notion":
        return
    use_secret = _observatory_use_secret(root)
    if not use_secret:
        return
    project = vault_project(root)
    argv_in = INVOKED_ARGV if INVOKED_ARGV is not None else sys.argv[1:]
    argv = vault_reexec_argv(backend, dict(os.environ), argv_in, sys.executable,
                             os.path.abspath(__file__), use_secret, project,
                             _vault_slot_exists(root, use_secret, project, file_has_token))
    if not argv:
        return
    refusal = _vault_run_refusal(root, use_secret, project)
    if refusal is not None:
        VAULT_DEGRADED = f"the vault slot {project}/{VAULT_SECRET} exists but `run` refused: {refusal}"
        return
    os.environ[VAULT_REEXEC] = "1"
    os.environ.setdefault(HOOK_T0_ENV, repr(_WALL_T0))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(argv[0], argv)


def load_env_file(root: Path) -> None:
    """Read .env.agent-sync into the environment when it is not already there.

    Load-bearing, and it was missing. A Claude Code hook is spawned with a bare
    environment: it never sees the `set -a && . ./.env.agent-sync` the operator ran in
    their own shell. So every hook silently fell back to the `fs` backend while the
    agent's own commands used the cloud — the guard denied edits whose lease WAS held,
    and recorded the run `ungated` while the agent had been told `gated`. Two views of
    one project, and the enforcement half held the wrong one.

    The file's location is deterministic, so the tool loads it rather than depending on
    how it was invoked. An already-set variable always wins: explicit beats implicit,
    and an operator overriding a value for one command must not be undone here.
    """
    global TOKEN_SOURCE
    path = find_env_file(root)
    text = ""
    if path is not None:
        try:
            text = path.read_text()
        except OSError:
            text = ""
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs.append((key.strip(), value.strip().strip('"').strip("'")))
    # R20: the vault is asked BEFORE the plaintext file can supply the token — by the
    # commands that touch the record plane, and only those (NO_RECORD_PLANE). An explicitly
    # set variable still wins over both — explicit beats implicit, as below.
    file_vals = dict(pairs)
    _maybe_reexec_under_vault(root, file_vals.get("AGENT_SYNC_BACKEND"),
                              bool(file_vals.get(VAULT_SECRET)))
    for key, value in pairs:
        if key and value and key not in os.environ:
            os.environ[key] = value
            if key == VAULT_SECRET and TOKEN_SOURCE is None:
                TOKEN_SOURCE = "file"


def load_config(root: Path) -> dict[str, Any]:
    path = root / CONFIG_PATH
    if not path.exists():
        raise NotCoordinated(
            "no .claude/agent-sync.json in this project.\n"
            "Run `init` first — it asks which backend to use and writes the config.\n"
            "  agent_sync.py init --backend outline --url <instance-url>\n"
            "  agent_sync.py init --backend fs")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise Fail(f".claude/agent-sync.json is not valid JSON: {exc}") from exc


def id_width(text: str, reg: str, default: int = 4) -> int:
    """The register's own zero-padding, read from the ids already in it (AS-08).

    Four digits were hardcoded, so a three-digit register (`CO-051`) was handed `CO-0051`
    and every id it had was reported as a permanent phantom leak. The widest id in the
    file decides; an empty register keeps the old default.
    """
    widths = [len(m) for m in re.findall(r"\b" + re.escape(reg) + r"-(\d+)\b", text)]
    return max(widths) if widths else default


def format_id(reg: str, value: int, width: int = 4) -> str:
    return f"{reg}-{value:0{width}d}"


def id_present(text: str, reg: str, value: int) -> bool:
    """Whether `reg-<value>` appears at ANY zero-padding, as a whole id (AS-08)."""
    return re.search(r"\b" + re.escape(reg) + r"-0*" + str(int(value)) + r"\b", text) is not None


def _rid_from(text: str) -> str:
    """A run id for a key, unique per key (R17, Observatory DECISIONS.md:2023).

    The id was the first twelve `[a-z0-9]` characters of the key, so two keys sharing a
    prefix — `observatory-tick-1` and `observatory-tick-2`, or any two ids a caller builds
    from a common stem — were ONE run: the second process won a lease the first still held,
    and the first's release then "succeeded" against a lease that was no longer its own.

    A key that fits in twelve characters is kept whole, because it cannot collide and the
    readable id is what `status` and the journal show. A longer one keeps six readable
    characters and spends six on a digest of the WHOLE key, so keys that differ anywhere
    differ here. Ids already stamped in a marker are read back unchanged.
    """
    flat = re.sub(r"[^a-z0-9]", "", text.lower())
    if len(flat) <= 12:
        return "r-" + flat
    return "r-" + flat[:6] + hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


def _session_key() -> tuple[str, str]:
    """Who is asking — and how confidently.

    Returns `(key, how)`. `how` is reported to the user, because the three answers are not
    equally strong and an identity nobody can question is how two agents end up as one.

    The hard case is real and was found in production: **two Claude sessions in the same
    checkout.** A hook runs with `CLAUDE_SESSION_ID` in its environment; a plain shell command
    does not. With a single marker file per checkout, the second session adopts whatever the
    first stamped — so both acquire, release and are guarded as one run. The lease then fails to
    separate exactly the case it exists for, silently, and one agent can release the other's
    lease mid-work.

    The process tree is what a plain shell still has. Every Claude session runs under its own
    `claude` process, so the nearest such ancestor identifies the session even when the
    environment does not. A pid can be recycled, so it is paired with that process's start time.
    """
    override = os.environ.get("AGENT_SYNC_RUN_ID")
    if override:
        return "env:" + override, "AGENT_SYNC_RUN_ID"

    session = os.environ.get("CLAUDE_SESSION_ID") or ""
    if session:
        return "session:" + session, "CLAUDE_SESSION_ID"

    # Claude Code exports its session id to every Bash tool call as CLAUDE_CODE_SESSION_ID —
    # the same id a hook reads from its payload and SessionStart stamps. It answers in a
    # repository no SessionStart ever stamped (a second repository, AS-07), where the walk
    # below finds nothing and the shell fell back to the shared identity.
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if session:
        return "session:" + session, "CLAUDE_CODE_SESSION_ID"

    # No session id in this environment — a plain shell command has none. What it does have is
    # an ancestor process that the SessionStart hook ran under, and that hook DID have the id.
    # So the hook stamps `<state>/sessions/<its own parent pid>` with the session, and this walk
    # looks for an ancestor that appears there. That is exact: no command-line parsing, which was
    # tried and failed — the throwaway shell this very command runs in carries claude paths in
    # its argv and matched every heuristic aimed at the CLI.
    try:
        dirs = _session_dirs()
        pid = os.getpid()
        for _ in range(10):
            for sessions in dirs:
                marker = sessions / str(pid)
                if marker.exists():
                    return "session:" + marker.read_text().strip(), "the session that started this shell"
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5)
            ppid = out.stdout.strip()
            if not ppid or ppid in ("0", "1", str(pid)):
                break
            pid = int(ppid)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    return "", "nothing — this identity is shared with any other session in this checkout"


def _session_dirs() -> list[Path]:
    """Where SessionStart stamps live: this checkout, then the MAIN checkout of a linked
    worktree. A session started in the main checkout and working in a worktree stamped
    the main checkout's state dir, so a walk that read only the worktree found nothing
    and fell back to the shared identity (B-150, 2026-10-11)."""
    root = project_root()
    dirs = [root / STATE_DIR / "sessions"]
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        main = Path(common).parent
        if main.resolve() != root.resolve():
            dirs.append(main / STATE_DIR / "sessions")
    return dirs


def _sessions_stamped() -> bool:
    """Has any hook stamped a session id in reach of this checkout?"""
    try:
        return any(d.is_dir() and any(d.iterdir()) for d in _session_dirs())
    except OSError:
        return False


def run_id(root: Path) -> str:
    """One identity per session, whichever way the tool is invoked.

    Load-bearing in both directions. Deriving the id from `CLAUDE_SESSION_ID` alone gave one
    session two identities — the agent acquired a lease as one and was denied by its own guard as
    the other. Keeping a single id per checkout gave two sessions one identity, which is worse:
    the guard let each of them write the other's guarded files and `release` took a lease its run
    never acquired.

    So the marker is a **map**, keyed by whatever `_session_key()` could establish. A key that
    cannot be established falls back to the shared entry — the old behaviour, kept because it is
    better than minting a fresh identity on every shell command, and reported as weak rather than
    presented as separation.
    """
    key, _how = _session_key()
    if key.startswith("env:"):
        return _rid_from(key[4:])

    marker = root / STATE_DIR / "run-id"
    data: dict[str, Any] = {}
    if marker.exists():
        raw = marker.read_text().strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"run": raw, "session": ""}
        if isinstance(parsed, dict) and "runs" in parsed:
            data = parsed
        elif isinstance(parsed, dict) and parsed.get("run"):
            # migrate the single-value marker: it belonged to whichever session stamped it
            legacy_key = ("session:" + parsed["session"]) if parsed.get("session") else "shared"
            data = {"runs": {legacy_key: {"run": parsed["run"], "seen": now_iso()}}}
    data.setdefault("runs", {})

    entry = data["runs"].get(key or "shared")
    if entry and entry.get("run"):
        entry["seen"] = now_iso()
        rid = entry["run"]
    else:
        # Derived from the SESSION the key names, however it was found — env, the
        # CLAUDE_CODE_SESSION_ID a Bash call carries, or a stamp — so a hook and a shell in
        # a checkout with no marker entry yet still mint the same id (AS-07).
        session = key[len("session:"):] if key.startswith("session:") else ""
        rid = _rid_from(session) if session else \
              "r-%06x%s" % (random.getrandbits(24), format(int(time.time()) & 0xFFF, "03x"))
        data["runs"][key or "shared"] = {"run": rid, "seen": now_iso()}

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(data, indent=1))
    return rid


# --------------------------------------------------------------------------- adapters

class Adapter:
    name = "none"
    capabilities = {"atomicAppend": False, "totalOrderRead": False, "search": False,
                    "exclusiveLease": False}
    # What must be in the environment before this adapter can reach anything, and the
    # variable holding its container id. Declared here so `check` and `status` ask the
    # adapter instead of carrying a per-backend branch each — the shape that let the
    # Outline credential check live in one place and be missing from the other.
    REQUIRED_ENV: tuple[str, ...] = ()
    COLLECTION_ENV = ""

    def preflight(self) -> str:
        """One cheap live call proving the credential works, or "" for none.

        `check` used to report "knowledge base reachable" after a call that only
        parsed a string. A check that reports reachability without reaching is the
        defect class this whole suite exists to refuse."""
        return ""

    def create_container(self, name: str) -> str:
        raise Fail(f"backend '{self.name}' has no container to create")

    def configured(self) -> bool:
        raise NotImplementedError

    def tree_ensure(self, path: str) -> str:
        raise NotImplementedError

    def log_append(self, oid: str, line: str) -> None:
        raise NotImplementedError

    def log_read(self, oid: str) -> str:
        raise NotImplementedError

    def doc_put(self, oid: str, text: str) -> None:
        raise NotImplementedError

    def doc_get(self, oid: str) -> str:
        raise NotImplementedError

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return []

    def log_shards(self, prefix: str) -> list[str]:
        """Every object whose title starts with `prefix` — one per writer."""
        raise NotImplementedError

    @property
    def is_lease_authority(self) -> bool:
        return bool(self.capabilities["atomicAppend"]
                    and self.capabilities["totalOrderRead"])



class OutlineAdapter(Adapter):
    """Outline knowledge base. Hosted or self-hosted; the URL is configuration.

    No compare-and-swap exists in this API: documents.update has editMode
    append/replace/prepend/patch and no lastRevision. Coordination state is
    therefore never a document we rewrite.
    """

    name = "outline"
    REQUIRED_ENV = ("AGENT_SYNC_OUTLINE_URL", "AGENT_SYNC_OUTLINE_TOKEN")
    COLLECTION_ENV = "AGENT_SYNC_OUTLINE_COLLECTION"
    # exclusiveLease is FALSE and that is not a formality. Outline has no
    # compare-and-swap, so a decision cannot be made after all contenders have
    # written — only after a settle window that is long enough in practice. Two runs
    # starting inside that window can both win. Measured, not assumed.
    capabilities = {"atomicAppend": True, "totalOrderRead": True, "search": True,
                    "exclusiveLease": False}

    def __init__(self) -> None:
        self.url = (os.environ.get("AGENT_SYNC_OUTLINE_URL") or "").rstrip("/")
        self.token = os.environ.get("AGENT_SYNC_OUTLINE_TOKEN") or ""
        self.collection = os.environ.get("AGENT_SYNC_OUTLINE_COLLECTION") or ""
        self._collection_uuid = ""
        self._ids: dict[str, str] = {}

    def configured(self) -> bool:
        return bool(self.url and self.token)

    def _call(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.configured():
            raise Fail("Outline is not configured (URL or token missing from the environment)")
        payload = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.url}/api/{endpoint}", data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        delay = 1.0
        for attempt in range(7):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode())
                if not data.get("ok", False):
                    raise Fail(f"outline {endpoint}: {data.get('message') or data.get('error')}")
                return data.get("data") or {}
            except urllib.error.HTTPError as exc:
                # The useful part of an Outline failure is in the body. Dropping it
                # turns "collectionId: Invalid UUID" into a bare 400 and costs a
                # debugging round — never swallow the reason.
                detail = ""
                try:
                    payload_err = json.loads(exc.read().decode())
                    detail = payload_err.get("message") or payload_err.get("error") or ""
                except (ValueError, OSError):
                    pass
                if exc.code in (401, 403):
                    raise Fail(
                        f"outline {endpoint}: {exc.code} — the token is rejected"
                        f"{': ' + detail if detail else ''}. "
                        "A credential does not become valid on retry.") from exc
                # 429 and transient 5xx deserve a retry; 401/403 never will.
                if exc.code in (429, 500, 502, 503, 504) and attempt < 6:
                    time.sleep(float(exc.headers.get("Retry-After") or delay)
                               + random.random() * 0.4)
                    delay *= 2
                    continue
                raise Fail(f"outline {endpoint}: HTTP {exc.code}"
                           f"{' — ' + detail if detail else ''}") from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Fail(f"outline {endpoint}: cannot reach the instance ({exc.reason})") from exc
        raise Fail(f"outline {endpoint}: gave up after 7 attempts")

    def preflight(self) -> str:
        self.resolve_collection()
        return "knowledge base reachable and the collection resolves"

    def create_container(self, name: str) -> str:
        data = self._call("collections.create", {"name": name, "description":
                                                 "Coordination plane for agent-sync. Generated pages "
                                                 "are stamped; edit sources in git."})
        return str(data["id"])

    def resolve_collection(self) -> str:
        """Accept a UUID, a urlId, or the whole `name-urlId` slug from the browser.

        The API takes a UUID, and the value a person copies out of the address bar
        is a slug. Rejecting that with 'Invalid UUID' is technically correct and
        useless, so match it instead."""
        if self._collection_uuid:
            return self._collection_uuid
        value = self.collection.strip()
        if not value:
            raise Fail("AGENT_SYNC_OUTLINE_COLLECTION is not set — run `bootstrap` to create "
                       "the container, then put the id it prints into .env.agent-sync")
        if re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            self._collection_uuid = value
            return value

        rows = self._call("collections.list", {"limit": 100})
        rows = rows if isinstance(rows, list) else []
        tail = value.rsplit("-", 1)[-1]
        for c in rows:
            if value in (c.get("urlId"), c.get("name")) or (tail and tail == c.get("urlId")):
                self._collection_uuid = c["id"]
                print(f"note: resolved collection '{c.get('name')}' → {c['id']}\n"
                      f"      put that UUID in AGENT_SYNC_OUTLINE_COLLECTION to skip this lookup",
                      file=sys.stderr)
                return str(c["id"])
        names = ", ".join(repr(c.get("name")) for c in rows) or "none visible to this token"
        raise Fail(f"no collection matches '{value}'. Available: {names}")

    def tree_ensure(self, path: str) -> str:
        if path in self._ids:
            return self._ids[path]
        collection = self.resolve_collection()
        found = self._call("documents.search",
                           {"query": path, "limit": 5, "collectionId": collection})
        for row in (found if isinstance(found, list) else []):
            doc = row.get("document") or {}
            if doc.get("title") == path:
                self._ids[path] = doc["id"]
                return doc["id"]
        doc = self._call("documents.create", {
            "collectionId": collection, "title": path,
            "text": f"{GENERATED_MARKER} container -->\n", "publish": True})
        self._ids[path] = doc["id"]
        return doc["id"]

    def log_append(self, oid: str, line: str) -> None:
        self._call("documents.update",
                   {"id": oid, "text": line.rstrip("\n") + "\n", "editMode": "append"})

    def log_read(self, oid: str) -> str:
        return self._call("documents.info", {"id": oid}).get("text", "")

    def doc_put(self, oid: str, text: str) -> None:
        self._call("documents.update", {"id": oid, "text": text, "editMode": "replace"})

    def doc_get(self, oid: str) -> str:
        return self._call("documents.info", {"id": oid}).get("text", "")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._call("documents.search", {"query": query, "limit": limit})
        out = []
        for row in (rows if isinstance(rows, list) else []):
            doc = row.get("document") or {}
            out.append({"id": doc.get("id"), "title": doc.get("title"),
                        "snippet": row.get("context", "")})
        return out

    def log_shards(self, prefix: str) -> list[str]:
        """Enumerate by the collection's structure, never by the search index.

        `documents.search` matches TEXT; a shard's identity is in its TITLE, and a
        freshly created shard is not reliably returned. Under concurrency that produced
        the worst possible failure: each process saw only its own shard, replayed it,
        and concluded it had won — eight processes, eight winners, one key.
        `documents.list` reads the collection structure and returns a new document at
        once.
        """
        out: list[str] = []
        offset = 0
        while True:
            rows = self._call("documents.list",
                              {"collectionId": self.resolve_collection(),
                               "limit": 100, "offset": offset})
            rows = rows if isinstance(rows, list) else []
            for doc in rows:
                title = doc.get("title") or ""
                if title.startswith(prefix):
                    out.append(doc["id"])
                    self._ids[title] = doc["id"]
            if len(rows) < 100:
                break
            offset += 100
            if offset > 1000:      # a bound, and it is reported rather than silent
                print("agent-sync: more than 1000 documents in the collection; "
                      "shard enumeration truncated", file=sys.stderr)
                break
        return out


class FsAdapter(Adapter):
    """Degraded mode. Files under .agent-sync/, and NOT committed.

    `check` refuses a tracked state directory — a committed run id hands this
    checkout's identity to every clone, and the tree is dirty after every tool call.
    This line claimed the opposite until 2026-08-25; the check was right.

    atomicAppend is FALSE on purpose: agents here are separated by git, not by a
    filesystem, so ordering is decided by a merge after the fact — which is not
    when the protocol needs it. This adapter is never the lease authority.
    """

    name = "fs"
    # A local lock file IS an exclusive primitive between processes on one machine.
    capabilities = {"atomicAppend": False, "totalOrderRead": False, "search": False,
                    "exclusiveLease": True}

    def __init__(self, root: Path) -> None:
        self.base = root / STATE_DIR
        self.base.mkdir(parents=True, exist_ok=True)

    def configured(self) -> bool:
        return True

    def _p(self, path: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-").lower()
        return self.base / f"{safe}.md"

    # A store failure is the tool's own failure type, never a bare OSError. Callers
    # catch `Fail` and turn it into one sentence a reader can act on; an OSError walks
    # straight past them into `main`, and the agent is handed a Python traceback for a
    # read-only directory — which it then reports as the state of the coordination plane.
    def tree_ensure(self, path: str) -> str:
        p = self._p(path)
        try:
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("")
        except OSError as exc:
            raise Fail(f"cannot open the local plane at {p}: {exc}") from exc
        return str(p)

    def log_append(self, oid: str, line: str) -> None:
        try:
            with open(oid, "a") as fh:
                fh.write(line.rstrip("\n") + "\n")
        except OSError as exc:
            raise Fail(f"cannot append to {oid}: {exc}") from exc

    def log_read(self, oid: str) -> str:
        p = Path(oid)
        try:
            return p.read_text() if p.exists() else ""
        except OSError as exc:
            raise Fail(f"cannot read {oid}: {exc}") from exc

    def doc_put(self, oid: str, text: str) -> None:
        try:
            Path(oid).write_text(text)
        except OSError as exc:
            raise Fail(f"cannot write {oid}: {exc}") from exc

    def doc_get(self, oid: str) -> str:
        p = Path(oid)
        try:
            return p.read_text() if p.exists() else ""
        except OSError as exc:
            raise Fail(f"cannot read {oid}: {exc}") from exc

    def log_shards(self, prefix: str) -> list[str]:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", prefix).strip("-").lower()
        return [str(q) for q in sorted(self.base.glob(f"{stem}*.md"))]


class NotionAdapter(Adapter):
    """Notion. The container is a page, every log is a child page of it, and every
    line is one paragraph block appended server-side.

    The append is a real append: `PATCH /v1/blocks/{id}/children` "creates and appends
    new children blocks to the parent block_id specified", at the end by default. There
    is no read-modify-write anywhere in this class, which is what `atomicAppend` claims
    and the only thing it claims.

    `exclusiveLease` is FALSE and no measurement can change it: Notion has no
    compare-and-swap. Two appends racing on one page can both succeed — the endpoint
    documents `conflict_error` (409) for the case where they collide instead — so a
    decision made by replaying this log is a decision made after the fact. Exclusion
    stays with `leaseBackend`.
    """

    name = "notion"
    REQUIRED_ENV = ("AGENT_SYNC_NOTION_TOKEN",)
    COLLECTION_ENV = "AGENT_SYNC_NOTION_COLLECTION"
    API = "https://api.notion.com/v1"
    # Pinned, not floating. Notion versions its API by date and an unpinned client
    # gets whatever is current the morning a breaking change ships.
    NOTION_VERSION = "2026-03-11"
    # One rich_text item caps at 2000 characters. Chunk below it rather than at it:
    # a line that lands exactly on the boundary is the one that fails in production.
    CHUNK = 1900
    # The endpoint's own cap on children per request.
    BATCH = 100

    capabilities = {"atomicAppend": True, "totalOrderRead": True, "search": True,
                    "exclusiveLease": False}

    def __init__(self) -> None:
        self.token = os.environ.get("AGENT_SYNC_NOTION_TOKEN") or ""
        self.parent = os.environ.get("AGENT_SYNC_NOTION_PARENT") or ""
        self.collection = os.environ.get("AGENT_SYNC_NOTION_COLLECTION") or ""
        self._ids: dict[str, str] = {}

    def configured(self) -> bool:
        return bool(self.token and (self.collection or self.parent))

    # -- transport ---------------------------------------------------------

    @staticmethod
    def _uuid(value: str) -> str:
        """Accept what a person copies out of the address bar.

        A Notion URL ends in 32 undashed hex characters; the API takes either form,
        but a slug like `Board-1f2e…` does not resolve, so the digits are extracted
        rather than assumed to be the whole string."""
        raw = re.sub(r"[^0-9a-fA-F]", "", (value or "").rsplit("/", 1)[-1])
        if len(raw) < 32:
            raise Fail(f"'{value}' does not contain a 32-character Notion id — copy the "
                       "page's URL and use the id at its end")
        raw = raw[-32:].lower()
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured():
            raise Fail("Notion is not configured (AGENT_SYNC_NOTION_TOKEN missing from "
                       "the environment, or neither _COLLECTION nor _PARENT is set)")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.API}/{path}", data=data, method=method)
        # The token reaches a header and nothing else — never a command line, never a
        # log line, and never this module's error text.
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Notion-Version", self.NOTION_VERSION)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        delay = 1.0
        # Five attempts on a rate limit, then fail loudly — the contract's number,
        # not a guess.
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode() or "{}")
            except urllib.error.HTTPError as exc:
                detail, code = "", ""
                try:
                    payload = json.loads(exc.read().decode())
                    detail = payload.get("message") or ""
                    code = payload.get("code") or ""
                except (ValueError, OSError):
                    pass
                if exc.code in (401, 403):
                    raise Fail(
                        f"notion {method} {path}: {exc.code} — the token is rejected"
                        f"{': ' + detail if detail else ''}. A credential does not "
                        "become valid on retry.") from exc
                # 429 and 529 carry Retry-After in integer seconds and mean *wait*.
                # 409 conflict_error is two writers touching one page — the same
                # answer, on a shorter clock. 5xx is the server's own bad minute.
                if exc.code in (409, 429, 500, 502, 503, 504, 529) and attempt < 4:
                    hint = exc.headers.get("Retry-After")
                    time.sleep((float(hint) if hint else delay) + random.random() * 0.4)
                    delay *= 2
                    continue
                reason = f"{code}: {detail}" if code and detail else (detail or code)
                raise Fail(f"notion {method} {path}: HTTP {exc.code}"
                           f"{' — ' + reason if reason else ''}") from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Fail(f"notion {method} {path}: cannot reach the API "
                           f"({exc.reason})") from exc
            except TimeoutError as exc:
                # A read that times out AFTER the connection is established raises this
                # directly — `URLError` wraps only the connect failure — so it used to
                # fall into the catch-all below and fail permanently on the first slow
                # response. The contract calls a transport error retryable, and this is
                # one. Measured 2026-08-25: a writer died mid-run with "The read
                # operation timed out" while the workspace was under its own rate limit.
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Fail(f"notion {method} {path}: the API did not answer within "
                           f"{20}s, {attempt + 1} times over") from exc
            except (ValueError, OSError) as exc:
                raise Fail(f"notion {method} {path}: {exc}") from exc
        raise Fail(f"notion {method} {path}: rate limited through 5 attempts — the "
                   "workspace limit is shared by every connection in it, so this is "
                   "not necessarily your own traffic")

    # -- containers --------------------------------------------------------

    def preflight(self) -> str:
        self._call("GET", "users/me")
        self.resolve_collection()
        return "token accepted by Notion and the container id resolves"

    def create_container(self, name: str) -> str:
        if not self.parent:
            raise Fail("AGENT_SYNC_NOTION_PARENT is not set — put the id of the page "
                       "the container should live under in it. Copy the 32-character "
                       "id from the end of that page's URL.")
        page = self._call("POST", "pages", {
            "parent": {"type": "page_id", "page_id": self._uuid(self.parent)},
            "properties": {"title": {"title": [{"type": "text",
                                                "text": {"content": name[:2000]}}]}},
        })
        return str(page["id"])

    def resolve_collection(self) -> str:
        if not self.collection:
            raise Fail("AGENT_SYNC_NOTION_COLLECTION is not set — run `bootstrap` to "
                       "create the container page, then put the id it prints into "
                       f"{ENV_FILE}")
        return self._uuid(self.collection)

    def _children(self, oid: str) -> list[dict[str, Any]]:
        """Every child block, followed to the last page.

        Stopping at the first page would make a long log read as a short one, and a
        short log replays into a wrong answer rather than an error."""
        out: list[dict[str, Any]] = []
        cursor = ""
        while True:
            q = f"blocks/{oid}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            data = self._call("GET", q)
            out.extend(data.get("results") or [])
            if not data.get("has_more"):
                return out
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return out

    def tree_ensure(self, path: str) -> str:
        if path in self._ids:
            return self._ids[path]
        collection = self.resolve_collection()
        # Enumerate by STRUCTURE, never by the search index — Notion's search is
        # eventually consistent, and a shard it has not indexed yet reads as a shard
        # that does not exist. That is how eight processes each see only their own
        # log and each conclude they won.
        for block in self._children(collection):
            if block.get("type") == "child_page" \
                    and (block.get("child_page") or {}).get("title") == path:
                self._ids[path] = block["id"]
                return str(block["id"])
        page = self._call("POST", "pages", {
            "parent": {"type": "page_id", "page_id": collection},
            "properties": {"title": {"title": [{"type": "text",
                                                "text": {"content": path[:2000]}}]}},
        })
        self._ids[path] = page["id"]
        return str(page["id"])

    # -- logs --------------------------------------------------------------

    def _rich(self, text: str) -> list[dict[str, Any]]:
        parts = [text[i:i + self.CHUNK] for i in range(0, len(text), self.CHUNK)] or [""]
        return [{"type": "text", "text": {"content": p}} for p in parts]

    @staticmethod
    def _text_of(block: dict[str, Any]) -> str | None:
        """The literal text of a block, or None when it carries none.

        `None` and `""` are different answers here: an empty paragraph is a line, and
        a divider is not, and folding the two loses a line every time somebody presses
        enter in the Notion editor."""
        body = block.get(block.get("type") or "") or {}
        rich = body.get("rich_text")
        if not isinstance(rich, list):
            return None
        return "".join(r.get("plain_text") or (r.get("text") or {}).get("content") or ""
                       for r in rich)

    def log_append(self, oid: str, line: str) -> None:
        self._call("PATCH", f"blocks/{oid}/children", {"children": [
            {"object": "block", "type": "paragraph",
             "paragraph": {"rich_text": self._rich(line.rstrip("\n"))}}]})

    def log_read(self, oid: str) -> str:
        lines = [t for t in (self._text_of(b) for b in self._children(oid)) if t is not None]
        return "\n".join(lines) + ("\n" if lines else "")

    # -- generated documents ----------------------------------------------

    def doc_put(self, oid: str, text: str) -> None:
        """Generated pages only, and they are ONE code block on purpose.

        The obvious implementation — delete every child, append the new ones — costs
        one request per existing block, and at three requests a second a regenerated
        board would take minutes. A single block is two requests whatever the size."""
        blocks = self._children(oid)
        target = next((b for b in blocks if b.get("type") == "code"), None)
        payload = {"rich_text": self._rich(text), "language": "markdown"}
        if target:
            self._call("PATCH", f"blocks/{target['id']}", {"code": payload})
            return
        for block in blocks:
            self._call("DELETE", f"blocks/{block['id']}")
        self._call("PATCH", f"blocks/{oid}/children",
                   {"children": [{"object": "block", "type": "code", "code": payload}]})

    def doc_get(self, oid: str) -> str:
        blocks = self._children(oid)
        target = next((b for b in blocks if b.get("type") == "code"), None)
        if target is not None:
            return self._text_of(target) or ""
        return self.log_read(oid)

    # -- search ------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = self._call("POST", "search", {"query": query, "page_size": max(1, min(limit, 100))})
        out: list[dict[str, Any]] = []
        for row in (data.get("results") or [])[:limit]:
            title = ""
            for prop in (row.get("properties") or {}).values():
                if isinstance(prop, dict) and prop.get("type") == "title":
                    title = "".join(t.get("plain_text") or "" for t in (prop.get("title") or []))
                    break
            out.append({"id": row.get("id"), "title": title, "snippet": ""})
        return out

    def log_shards(self, prefix: str) -> list[str]:
        out: list[str] = []
        for block in self._children(self.resolve_collection()):
            if block.get("type") != "child_page":
                continue
            title = (block.get("child_page") or {}).get("title") or ""
            if title.startswith(prefix):
                out.append(str(block["id"]))
                self._ids[title] = block["id"]
        return out


# Every backend that talks to a service, in ONE place. `check`, `init`'s argument
# parser, `bootstrap` and `status` all read this dict rather than each carrying a copy
# of the list — the copies are what drift, and a backend missing from one of them is a
# backend that half exists.
CLOUD_ADAPTERS: dict[str, type[Adapter]] = {
    "outline": OutlineAdapter,
    "notion": NotionAdapter,
}
BACKENDS = tuple(sorted(CLOUD_ADAPTERS)) + ("fs",)


def make_adapter(cfg: dict[str, Any], root: Path) -> Adapter:
    backend = os.environ.get("AGENT_SYNC_BACKEND") or cfg.get("backend") or "fs"
    cls = CLOUD_ADAPTERS.get(backend)
    if cls is not None:
        ad = cls()
        if not ad.configured():
            return FsAdapter(root)
        return ad
    return FsAdapter(root)


# --------------------------------------------------------------------------- log

def fmt_line(op: str, key: str, rid: str, **extra: Any) -> str:
    pairs = [f"`op={op}`", f"`key={key}`", f"`run={rid}`"]
    pairs += [f"`{k}={v}`" for k, v in extra.items() if v not in (None, "")]
    return f"- `{now_iso()}` " + " ".join(pairs)


def parse_log(text: str) -> tuple[list[dict[str, str]], int]:
    events: list[dict[str, str]] = []
    bad = 0
    for raw in text.splitlines():
        raw = raw.rstrip()
        # Skip only what is plainly not an entry (blank lines, prose, the generated
        # marker). Anything shaped like an entry must reach LINE_RE, or a silent
        # pre-filter hides malformed lines from the very counter meant to expose them.
        if not CANDIDATE_RE.match(raw):
            continue
        m = LINE_RE.match(raw)
        if not m:
            bad += 1
            continue
        ev = dict(PAIR_RE.findall(m.group("pairs")))
        if not {"op", "key", "run"} <= ev.keys():
            bad += 1
            continue
        ev["ts"] = m.group("ts")
        events.append(ev)
    return events, bad


def resolve_holding(events: list[dict[str, str]], key: str, at: float) -> dict[str, Any] | None:
    """Replay: the holder is the earliest acquire for key that is, at this point,
    neither released nor expired. Pure function of the log text.

    Returns the holding record — run, repo and when it was taken — because a caller
    that only learns *that* a key is held cannot tell an agent where to look."""
    live: list[dict[str, Any]] = []
    for ev in events:
        if ev["key"] != key:
            continue
        if ev["op"] == "acquire":
            live.append({"run": ev["run"], "ts": parse_iso(ev["ts"]),
                         "ttl": int(ev.get("ttl") or DEFAULT_TTL),
                         "repo": ev.get("repo", "")})
        elif ev["op"] == "release":
            live = [h for h in live if h["run"] != ev["run"]]
        elif ev["op"] == "renew":
            for h in live:
                if h["run"] == ev["run"]:
                    h["ts"] = parse_iso(ev["ts"])
    for h in live:
        if at <= h["ts"] + h["ttl"]:
            return h
    return None


def resolve_holder(events: list[dict[str, str]], key: str, at: float) -> str | None:
    h = resolve_holding(events, key, at)
    return str(h["run"]) if h else None


# What a lock file turns out to be once the TTL has been applied to it. `live` is not
# residue; the other three are, and they are not interchangeable — the first may be
# cleared, the other two may only be reported.
LIVE = "live"
REAPABLE = "reapable"
FOREIGN = "foreign"
AMBIGUOUS = "ambiguous"

# The same three-way split for the OTHER plane: a claim tag written into a registry file.
# `held` there means the lease the tag names is still inside its TTL; `orphan` means it is
# not, and `disputed` means the lease is live under a different run than the tag names.
TAG_HELD = "held"
ORPHAN = "orphan"
DISPUTED = "disputed"

DEFAULT_CLAIM_TEMPLATE = "{prev} (claimed: {holder})"


def claim_marker_re(template: str) -> "re.Pattern[str] | None":
    """A regex for this template's claim marker, capturing the run it names.

    `write_claim` builds the marker by emptying `{prev}` and substituting the run id. Read
    back, the run id is the one part not known in advance: it is the capture and everything
    around it is literal, so one function defines the marker in both directions instead of
    two spellings that can disagree.

    `None` for a template with no `{holder}`. Such a marker names no owner, and a tag whose
    owner cannot be read must never be cleared on somebody's behalf — the same rule
    `classify_lock` applies to a lock that records no run.
    """
    marker = template.replace("{prev}", "").strip()
    if "{holder}" not in marker:
        return None
    head, _, tail = marker.partition("{holder}")
    return re.compile(re.escape(head) + r"(?P<holder>[^|\s]+)" + re.escape(tail))


LONG_EXPIRED_DAYS = 7


def long_held(ages: dict[str, float], interval: int | None) -> list[tuple[str, float]]:
    """Leases held longer than the repository's scheduled interval (R19).

    Observatory's scheduled tick stands aside while any lease is held, so a session that
    keeps one for hours makes every tick in that window a no-op — measured as a ledger
    gone stale (project-observatory DECISIONS.md:12873). `scheduledIntervalSeconds` in the
    config names that interval; without it there is nothing to compare against and nothing
    is reported. Ages come from the lock's `since` (written at acquire, kept by renew), or
    from `ts` on a lock written before `since` existed — which can only under-report.
    """
    if not interval or interval <= 0:
        return []
    return sorted(((k, a) for k, a in ages.items() if a > interval), key=lambda kv: -kv[1])


def long_expired(entries: list[dict[str, Any]], older_than_s: int) -> list[dict[str, Any]]:
    """Residue past every reasonable explanation (AS-11). Not Fabric's `abandoned`, which is a
    claim whose OWNER reported an unrecoverable failure — this is time alone.

    An expired lease carries no right — `renew` walks `held()`, which never returns one, and
    `acquire` takes the key over — so what remains is a file, and M-50 keeps a RUN from
    deleting a file it cannot prove is its own. `reap --long-expired` is the swept form of the
    operator decision `--i-own-this` already is: the keys are the ones expired longer than
    `older_than_s`, it is never automatic, it prints and journals each payload it removes,
    and a live lease or one whose expiry cannot be established is never in the list.
    """
    return [e for e in entries
            if e.get("state") != LIVE and e.get("expired_for") is not None
            and e["expired_for"] >= older_than_s]


def classify_lock(key: str, raw: str, *, rid: str, identity_is_strong: bool,
                  repo: str, host: str, default_ttl: int, at: float) -> dict[str, Any]:
    """One lock file, read as a live lease or as one of three kinds of residue.

    Pure — everything it needs is an argument — so each verdict below is a fixture rather
    than a scenario somebody has to reproduce with two sessions and a clock.

    **Why this function has to exist.** Every reader of lease state in this tool folds the
    TTL into the read: `held()`, `_lease_holder()` and `all_holdings()` each answer *none*
    for an expired lock and *none* for a lock that is not there. That is correct for
    exclusion and it is why seventeen expired locks across nine repositories of one family
    were reported by nothing — `status` printed `leases held: none` and `finish` printed
    "no lease left held" standing on top of them. Expiry ends a lease. It does not remove
    a file, and nothing here could see the difference.

    **The split is the load-bearing part.** `reapable` means state this run can PROVE it
    owns and has spent, and the proof is deliberately narrow:

    - the lock is past its TTL — a live lease is held, not residue;
    - it records a `run`, and that run is this one;
    - **this run's identity is not the shared fallback.** `run_id()` keys its marker by
      session, and a shell with no session id is served one shared entry whose own
      docstring says the identity "is shared with any other session in this checkout".
      Under that key a matching run id proves nothing, so it does not license a delete.
    - its `repo` is this checkout, and its `host` — written by both lease modes since
      AS-03, absent only in locks taken before it — is this machine.

    Everything else is reported and left alone: `foreign` where it demonstrably belongs to
    somebody else, `ambiguous` where the question cannot be answered at all. In doubt the
    answer is `ambiguous`, never `reapable`.
    """
    out: dict[str, Any] = {"key": key, "state": AMBIGUOUS, "run": None, "repo": None,
                           "host": None, "ts": "", "expired_for": None, "why": ""}
    try:
        held = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        out["why"] = "the lock is not readable JSON, so nothing in it identifies an owner"
        return out
    if not isinstance(held, dict):
        out["why"] = "the lock is not an object, so nothing in it identifies an owner"
        return out

    out["run"] = held.get("run") or None
    out["repo"] = held.get("repo") or None
    out["host"] = held.get("host") or None
    out["ts"] = str(held.get("ts") or "")

    taken = parse_iso_or_none(out["ts"])
    if taken is None:
        out["why"] = (f"its timestamp ({out['ts'] or 'absent'}) is not one, so whether this "
                      "lock is spent cannot be established")
        return out
    try:
        ttl = int(held.get("ttl", default_ttl))
    except (TypeError, ValueError):
        out["why"] = (f"its ttl ({held.get('ttl')!r}) is not a number, so whether this lock "
                      "is spent cannot be established")
        return out

    expires = taken + ttl
    if at <= expires:
        out["state"] = LIVE
        out["why"] = (f"held by {out['run'] or 'an unnamed run'} for another "
                      f"{int(expires - at)}s")
        return out
    out["expired_for"] = int(at - expires)

    if not out["run"]:
        out["why"] = "it records no run, so there is nobody it can be proved to belong to"
        return out
    if out["host"] and out["host"] != host:
        out["state"] = FOREIGN
        out["why"] = f"it was taken on {out['host']}, which is not this machine"
        return out
    if out["run"] != rid:
        out["state"] = FOREIGN
        out["why"] = f"it belongs to run {out['run']}, not to this one"
        return out
    if out["repo"] and out["repo"] != repo:
        out["why"] = (f"its run id matches, but it names repository {out['repo']} while this "
                      f"checkout is {repo}")
        return out
    if not identity_is_strong:
        out["why"] = ("its run id matches, but this run's identity is the shared fallback — "
                      "any other session in this checkout answers to the same id, so the "
                      "match proves nothing")
        return out
    out["state"] = REAPABLE
    out["why"] = "this run took it and let it expire"
    return out


def since(seconds: int) -> str:
    """A duration an operator can act on."""
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def spent(entry: dict[str, Any]) -> str:
    """How long this lock has been residue — said once, so no surface can phrase it
    differently. A lock whose clock cannot be read is not "expired an unknown time ago";
    it is a lock nobody can say is spent, which is why it is never reaped."""
    if entry["state"] == LIVE:
        return "live"
    if entry.get("expired_for") is None:
        return "expiry could not be established"
    return f"expired {since(entry['expired_for'])} ago"


def resolve_reservations(events: list[dict[str, str]], reg: str) -> tuple[int, list[int], list[tuple[str, int]]]:
    """Allocation over the log. Returns (base, free_list, assignments).

    Two kinds of reserve line coexist:

    - `op=reserve` **with a `value=`** — a *receipt*: the number was allocated before
      the line was written (a git ref compare-and-swap, or a confirmed optimistic
      append), and replay never renumbers it. Shards arriving late, bases appended
      afterwards, permuted merge orders — none of them move a value that is already
      on its line. A receipt whose value is already live lost its race and gets no
      assignment: its run saw the loss on read-back and appended another line.
    - **bare** `op=reserve` — the legacy positional claim: free-list head, else
      `base + served`. Its value is a function of the merged order, which is exactly
      why every new writer stamps the value instead of leaving it to be computed.
    """
    base = None
    free: list[int] = []
    served = 0
    assignments: list[tuple[str, int]] = []
    live: set[int] = set()
    for ev in events:
        if ev["key"] != reg:
            continue
        if ev["op"] == "base":
            value = int(ev.get("value") or 0)
            # A base only ever moves allocation FORWARD. Two runs opening the same register in
            # the same minute both append the same seed, and a base that re-seated
            # unconditionally would restart the count and hand the second run the id the first
            # had just been given — the collision, arriving through the door built to prevent it.
            if base is not None and value <= base + served:
                continue
            base = value
            # A re-base restarts the count. Without this reset, it hands out `new_base + served`
            # and skips as many ids as were served under the old one — and re-basing is not
            # exotic: it is what happens whenever the register grew by a path other than this
            # tool, which is the common case.
            served = 0
            # Ids freed below the new base are not free any more. The register moved past them, so
            # something is written there now, and handing one back would be the very collision the
            # re-base exists to prevent, arriving through the other door.
            free = [f for f in free if f >= base]
            continue
        if ev["op"] == "reserve" and (ev.get("value") or ""):
            # A receipt is honoured even before any base line: the number is issued,
            # and ignoring it would let the positional path hand it out again.
            try:
                value = int(ev["value"])
            except ValueError:
                continue
            if value in live:
                continue
            live.add(value)
            assignments.append((ev["run"], value))
            if value in free:
                free.remove(value)
            elif base is not None and value >= base + served:
                # The positional count follows the receipt forward, so the next bare
                # reserve lands past it instead of on top of it.
                served = value - base + 1
            continue
        if base is None:
            continue
        if ev["op"] == "release_id":
            try:
                freed = int(ev.get("value") or 0)
            except ValueError:
                pass
            else:
                free.append(freed)
                live.discard(freed)
        elif ev["op"] == "reserve":
            if free:
                value = free.pop(0)
            else:
                value = base + served
                served += 1
            live.add(value)
            assignments.append((ev["run"], value))
    return (base or 0), free, assignments


# --------------------------------------------------------------------------- coordinator

class Sync:
    def __init__(self) -> None:
        self.root = project_root()
        os.chdir(self.root)
        load_env_file(self.root)
        self.cfg = load_config(self.root)
        self.adapter = make_adapter(self.cfg, self.root)
        self.rid = run_id(self.root)
        self.ttl = int(self.cfg.get("leaseTtlSeconds") or DEFAULT_TTL)
        self._identity: tuple[str, str] | None = None
        self._holders: dict[str, str | None] = {}
        # The generation THIS OBJECT acquired each key under (FIX-SY-03.02).
        # In-memory on purpose: a zombie session shares the run id and the
        # checkout with its replacement — the only thing it does NOT share is
        # this dict, which is exactly what makes it a fence.
        self._lease_gen: dict[str, int] = {}
        # What each `release(name)` actually released, by key — usually `[name]`, but a
        # name 1.21.2 reported for another key resolves to that key (#25), and the CLI
        # says which key it let go rather than repeating the name it was given.
        self.released_as: dict[str, list[str]] = {}

    def capabilities(self) -> dict:
        """Five SEPARATE capability fields — because `gated` was one boolean
        answering three unrelated questions (FIX-SY-06.01).

        `lease_scope`      where exclusion holds: cross-machine (git) vs
                           machine-local (local). An advisory host must never
                           read as enforced.
        `enforcement_mode` whether exclusion is REAL here: enforced only when the
                           operator asked for it (cfg gated) AND the lease mode
                           actually guarantees it; advisory otherwise.
        `awareness_scope`  whether other agents can SEE this project's state:
                           shared when the record plane carries a total order,
                           isolated when it does not (a pure-fs project has no
                           shared awareness).
        `identity_strength` how strongly a run identity is bound: strong when the
                           lease is a cross-machine CAS, weak when it is a local
                           advisory lock.
        `backend_health`   `up` or `failed` — a backend that cannot be reached is
                           NEVER reported active/green; its enforcement collapses
                           to advisory and the failure is named.
        """
        # `preflight` is the one cheap live call that proves the backend is
        # reachable (a local `fs` returns "" and never raises; a cloud adapter
        # makes one request). A raise means the backend is unreachable RIGHT
        # NOW, which must collapse enforcement to advisory rather than show
        # green.
        health = "up"
        try:
            self.adapter.preflight()
        except Exception:
            health = "failed"
        asked = bool(self.cfg.get("gated", True))
        cross = self.lease_mode == "git"
        # ENFORCED means a real cross-machine compare-and-swap the operator
        # asked for, on a reachable backend. A machine-local lock is genuine
        # exclusion on THIS machine but advisory across machines, so it reports
        # 'advisory' with lease_scope carrying the local nuance — a host that is
        # only locally exclusive must never be described to a team as enforced.
        # A failed backend cannot enforce anything, whatever the config says.
        enforced = asked and cross and health == "up"
        return {
            "lease_scope": "cross-machine" if cross else "machine-local",
            "enforcement_mode": "enforced" if enforced else "advisory",
            "awareness_scope": "shared" if getattr(self.adapter, "is_lease_authority", False)
                               else "isolated",
            "identity_strength": "strong" if (cross and health == "up") else "weak",
            "backend_health": health,
        }

    @property
    def gated(self) -> bool:
        """Legacy compatibility SUMMARY over the five capability fields
        (FIX-SY-06.01). Kept so old callers keep working, but it is derived from
        `capabilities()` now — it is true only when enforcement is real, so an
        advisory host and a failed backend both read as NOT gated.

        Until 1.2.4 this read the record adapter's capabilities, which stopped deciding
        leases in 1.0.0. Both directions were wrong: `outline` with a local lock reported
        `gated` while exclusion was machine-local, and `fs` with git refs reported
        `ungated` while every lease was a genuine cross-machine compare-and-swap. The
        plane carries the record; `leaseBackend` decides the lease.
        """
        return self.capabilities()["enforcement_mode"] == "enforced"

    def log_id(self, which: str) -> str:
        """This run's OWN shard. One writer per document, always.

        Outline's `editMode: append` is not atomic under concurrency: the server reads
        the text, appends and writes it back, so simultaneous requests clobber each
        other — and every one of them returns `ok: true`. Measured: twelve concurrent
        appends, twelve successes reported, three lines present. Nine writes lost
        silently.

        That breaks mutual exclusion outright. If B's write erases A's acquire, A has
        already read back and seen itself win, and B reads back and sees itself win —
        two holders of one lease, each with proof.

        Sharding removes the race rather than fighting it: nobody else writes this
        document, so nothing can be clobbered. The cost is that a total order can no
        longer come from one document's line order, so reads merge the shards and sort
        by (timestamp, run). Every reader computes the SAME order — which is the
        property the protocol actually needs. Clock skew now affects who wins a tie,
        not whether readers agree, and an unfair winner is survivable where
        disagreement is not.
        """
        return self.adapter.tree_ensure(f"{LOGS[which]} — {self.rid}")

    def events(self, which: str) -> tuple[list[dict[str, str]], int]:
        """Merge every shard, plus any pre-sharding single document, into one order."""
        prefix = LOGS[which]
        texts: list[str] = []
        seen: set[str] = set()
        for oid in self.adapter.log_shards(prefix):
            if oid in seen:
                continue
            seen.add(oid)
            texts.append(self.adapter.log_read(oid))

        events: list[dict[str, str]] = []
        bad = 0
        for seq, text in enumerate(texts):
            evs, b = parse_log(text)
            bad += b
            for i, ev in enumerate(evs):
                ev["_shard"] = str(seq)
                ev["_i"] = str(i)
                events.append(ev)

        # Deterministic for every reader: time, then run, then position within a shard.
        events.sort(key=lambda e: (e["ts"], e["run"], int(e["_i"])))

        # Past the threshold the log is refused, not replayed. `MAX_UNPARSEABLE` was
        # declared and never read: the only trace of this rule was a line on the board
        # that printed a warning and returned 0, while SKILL.md, lease-protocol.md and the
        # README all said a log this broken stops the run. Replaying it reports holders who
        # do not exist and silence where the real ones are — which is strictly worse than
        # refusing, because both look like an answer.
        total = len(events) + bad
        if total and bad / total > MAX_UNPARSEABLE:
            raise Fail(
                f"the {which} log is {bad}/{total} unparseable "
                f"({bad / total:.0%}, over the {MAX_UNPARSEABLE:.0%} limit) — refusing to "
                "replay it. Entry-shaped lines that do not match the grammar are counted, "
                "never guessed at; fix or remove them (see references/lease-protocol.md)")
        return events, bad

    # -- leases ------------------------------------------------------------

    # -- git lease: real compare-and-swap, across machines --------------------

    @staticmethod
    def _ref(key: str) -> str:
        return LEASE_REF_PREFIX + lease_name(key)

    @staticmethod
    def _legacy_ref(key: str) -> str | None:
        """The ref agent-sync <= 1.21.2 pushed for `key`, when that is a DIFFERENT ref.

        It slugged every run of characters outside `[A-Za-z0-9._-]` into one `-` and
        stripped the ends, so a path key (`docs/x.md`) lives on remotes as `docs-x.md`.
        Those refs exist and must stay both exclusive and releasable — read, never
        written to under the new name, until the lease they carry ends.
        """
        name = legacy_ref_name(key)
        if not name or name == lease_name(key):
            return None
        return LEASE_REF_PREFIX + name

    def _git_remote(self) -> str:
        return self.cfg.get("leaseRemote") or "origin"

    def _require_git_remote(self, what: str) -> str:
        """The configured lease remote, or a refusal that names it.

        Without this a missing remote surfaced as the wrong failure: `git ls-remote`
        answered nothing, the push was rejected, and `acquire` reported the key "held by
        another run" while `reserve` reported "another allocator is racing" — a holder
        and a contention that did not exist. Local only, so it costs no round-trip.
        """
        remote = self._git_remote()
        if not git_remote_exists(remote, self.root):
            raise Fail(
                f"{what}: leaseBackend is 'git' but remote '{remote}' does not exist in "
                "this repository, so nothing can be decided. Add it "
                f"(`git remote add {remote} <url>`) or point `leaseRemote` at one that does")
        return remote

    def _git_lease_at(self, ref: str, *, strict: bool = False) -> tuple[str | None, dict[str, Any]]:
        """(sha, payload) on the remote at exactly `ref`, or (None, {}) when there is none.

        `strict` is for the callers whose answer is a claim about the remote — `acquire`
        and `release`. A remote that cannot be read is then a `Fail`, never "no ref":
        the lenient read answered (None, {}) for both, and `release` printed "released"
        over a remote it never reached.
        """
        remote = self._git_remote()
        try:
            r = subprocess.run(["git", "ls-remote", remote, ref], capture_output=True,
                               text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            if strict:
                raise Fail(f"could not read {ref} on '{remote}' ({exc})") from exc
            return None, {}
        if r.returncode != 0:
            if strict:
                why = (r.stderr or "").strip().splitlines()
                raise Fail(f"could not read {ref} on '{remote}' "
                           f"({why[-1] if why else 'git ls-remote failed'})")
            return None, {}
        # ls-remote matches by tail; only the exact ref is this lease.
        sha = next((parts[0] for parts in (l.split() for l in r.stdout.splitlines())
                    if len(parts) == 2 and parts[1] == ref), None)
        if not sha:
            return None, {}
        # The object is local whenever this machine wrote it — the common case for a
        # release — so the fetch is paid only when it is not. `release --held` runs under
        # SessionEnd's 2 s limit, and the proof re-read below costs a round-trip of its own.
        body = git("log", "-1", "--format=%B", sha)
        if not body:
            git("fetch", "-q", remote, f"{ref}:refs/agent-sync/fetched")
            body = git("log", "-1", "--format=%B", sha) or git("log", "-1", "--format=%B",
                                                               "refs/agent-sync/fetched")
        try:
            payload = json.loads(body.strip())
        except (json.JSONDecodeError, ValueError):
            payload = {}
        return sha, payload if isinstance(payload, dict) else {}

    def _git_read_lease(self, key: str, *, strict: bool = False
                        ) -> tuple[str | None, dict[str, Any], str]:
        """(sha, payload, ref) of the lease on the remote for `key`, or (None, {}, ref).

        Under its own name first, then under the name 1.21.2 gave it — and a legacy-NAMED
        ref counts as this key's only when 1.21.2 wrote it (no `key` in the payload) or it
        names this key. A ref 1.21.3 wrote for the key `docs-x.md` is not `docs/x.md`'s
        lease just because the old slug of one is the name of the other.
        """
        ref = self._ref(key)
        sha, held = self._git_lease_at(ref, strict=strict)
        if sha:
            return sha, held, ref
        legacy = self._legacy_ref(key)
        if legacy:
            lsha, lheld = self._git_lease_at(legacy, strict=strict)
            if lsha and lheld.get("key") in (None, key):
                return lsha, lheld, legacy
        return None, {}, ref

    def _note_local(self, key: str, payload: str) -> None:
        """Record locally that THIS run won THIS key here.

        The git ref is the authority and stays so — it is what makes the lease exclusive across
        machines. But `held()`, `whoami`, `status` and the PreToolUse guard all ask a *local*
        question: does this run hold that key? Answering it by reading the remote would put a
        network round-trip in front of every single Edit. So the winner leaves a note, and
        `release()` — which already removes it — closes the loop.

        Without this the guard denied the run that held the lease: `acquire` wrote a ref, `held()`
        read a directory nothing had written, and every guarded file became unwritable in git mode.
        """
        lock = self._local_lock(key)
        try:
            body = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            body = {}
        if isinstance(body, dict):
            # The note names its key (#25): the stem is the key's encoded name, and the
            # payload is what `held()` reports, so the two cannot drift apart.
            payload = json.dumps({**body, "key": key})
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
        except OSError as exc:
            # Never fatal: the lease is already won on the remote, which is the part that matters.
            print(f"note: lease won but not noted locally ({exc})", file=sys.stderr)

    def _git_acquire(self, key: str) -> tuple[bool, str | None]:
        """Push a ref that must not already exist. The remote's non-fast-forward rule
        IS the compare-and-swap — verified against a hosted remote, not assumed."""
        remote = self._require_git_remote(f"acquire {key}")
        # Strict: a remote that cannot be read is not a free key. `ref` is the key's own
        # name — or, while 1.21.2's ref for it still exists, that one, so a run on the old
        # version and a run on this one contend on the same ref instead of one each.
        held_sha, held, ref = self._git_read_lease(key, strict=True)
        if held:
            if held.get("run") == self.rid:
                self._note_local(key, json.dumps(held))
                self._touch_renew(key)
                return True, self.rid
            alive = time.time() <= parse_iso(held.get("ts", "")) + int(held.get("ttl", self.ttl))
            if alive:
                return False, held.get("run")

        payload = json.dumps({"key": key, "run": self.rid, "ts": now_iso(), "ttl": self.ttl,
                              "repo": repo_name(), "host": platform.node(),
                              "since": now_iso()})
        empty_tree = git("hash-object", "-t", "tree", os.devnull)
        # A lease object is plumbing, not authorship, so it must not depend on the
        # machine having a git identity. Without these `-c` flags `commit-tree`
        # refuses wherever user.email is unset and cannot be auto-detected — CI
        # runners, containers, a freshly provisioned box — and the lease backend
        # silently becomes unusable on exactly the machines that need it most.
        made = subprocess.run(
            ["git", "-c", "user.name=agent-sync", "-c", "user.email=agent-sync@localhost",
             "commit-tree", empty_tree],
            input=payload, capture_output=True, text=True)
        commit = made.stdout.strip()
        if not commit:
            detail = (made.stderr or "").strip().splitlines()
            why = detail[-1] if detail else "no output from git commit-tree"
            raise Fail(f"could not create the lease object — is this a git repository? ({why})")

        args = ["git", "push", remote, f"{commit}:{ref}"]
        if held_sha:                       # stealing an expired lease, and only that
            args.insert(2, f"--force-with-lease={ref}:{held_sha}")
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            # Rejected: somebody won between our read and our push. Ask who.
            _s, now_held, _r = self._git_read_lease(key)
            return False, now_held.get("run") or "another run"
        self._note_local(key, payload)
        self._touch_renew(key)
        return True, self.rid

    def _git_release(self, key: str) -> tuple[str, str, str]:
        """Delete this run's ref for `key` and PROVE it went. Returns (state, why, ref).

        `state` is `gone` (deleted, and a second read of the remote no longer finds it),
        `absent` (nothing on the remote under either name), or `kept` (the ref is still
        there — another run's, a delete the remote refused, or a remote nobody could read).

        This used to return nothing. A refused push printed a note and the caller went on
        to print "released", exit 0 and drop the local note — so the only trace of a
        lease still on the remote was removed by the command that had failed to remove
        it. The verdict comes from re-reading the remote, never from the push's exit code.
        """
        remote = self._git_remote()
        try:
            sha, held, ref = self._git_read_lease(key, strict=True)
        except Fail as exc:
            return "kept", str(exc), self._ref(key)
        if not sha:
            return "absent", "", ref
        if held.get("run") not in (self.rid, None):
            return "kept", f"{key} is held by {held.get('run')}, not this run", ref
        r = subprocess.run(["git", "push", remote, f"--force-with-lease={ref}:{sha}",
                            f":{ref}"], capture_output=True, text=True)
        try:
            after, _ = self._git_lease_at(ref, strict=True)
        except Fail as exc:
            return "kept", f"could not re-read the remote to prove {ref} went ({exc})", ref
        if after == sha:
            lines = (r.stderr or "").strip().splitlines()
            why = next((l.strip() for l in lines if "remote:" in l or "rejected" in l),
                       lines[-1].strip() if lines else "the delete did not land")
            return "kept", f"{ref} is still on '{remote}' after the delete ({why[:160]})", ref
        # Gone — or replaced by another run's lease after this run's delete landed, which
        # is that run's lease and not a failure of this one.
        return "gone", "", ref

    def _legacy_aliases(self, name: str) -> list[str]:
        """The keys whose 1.21.2 lock stem was `name`, among refs THIS run holds.

        1.21.2 told the run it held `T-1-2` when it held `T-1.2`, and anything scripted
        from that output releases `T-1-2`. Only refs 1.21.2 wrote (no `key` in the
        payload) are matched: a lease taken under 1.21.3 is released by its own key and
        nothing else, so `release T-1-2` can never take this run's `T-1.2` by accident.
        """
        refs, why = self._git_lease_refs()
        if why is not None:
            return []
        out = []
        for sha, comp in refs:
            k = lease_key(comp)
            if k == name or legacy_lock_stem(k) != name:
                continue
            try:
                body = json.loads(self._git_lease_payload(sha) or "{}")
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(body, dict) and "key" not in body and body.get("run") == self.rid:
                out.append(k)
        return sorted(out)

    def migrate_legacy_notes(self) -> list[tuple[str, str]]:
        """Rewrite this run's 1.21.2 notes under the key their ref was actually taken as.

        A 1.21.2 note is named by a lossy slug and carries no `key`, so the local plane
        alone cannot say which key it stands for. The remote can: the ref this run holds
        whose slug is the note's stem. Run by the commands that report or release what a
        run holds — never by the PreToolUse guard, which must not pay a round-trip per
        Edit. Returns (old stem, key) per note rewritten; a note no ref explains is left
        as it is, and reads back as its stem, exactly as before.
        """
        if self.lease_mode != "git":
            return []
        d = self.root / STATE_DIR / "leases"
        legacy = []
        for q in sorted(d.glob("*.lock") if d.exists() else []):
            try:
                h = json.loads(q.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(h, dict) and "key" not in h and h.get("run") == self.rid \
                    and self._lease_alive(h, self.ttl):
                legacy.append(q)
        if not legacy:
            return []
        refs, why = self._git_lease_refs()
        if why is not None:
            return []
        done = []
        for q in legacy:
            matches = []
            for sha, comp in refs:
                k = lease_key(comp)
                if legacy_lock_stem(k) != q.stem:
                    continue
                try:
                    body = json.loads(self._git_lease_payload(sha) or "{}")
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(body, dict) and body.get("run") == self.rid \
                        and body.get("key") in (None, k):
                    matches.append((k, body))
            # One 1.21.2 note could stand for SEVERAL leases (`T-1.2` and `T-1-2` shared
            # it), so every ref it explains gets its own note back.
            for k, body in matches:
                self._note_local(k, json.dumps(body))
                done.append((q.stem, k))
            if matches and all(self._local_lock(k) != q for k, _b in matches):
                q.unlink(missing_ok=True)
        return done

    # -- git id allocation: the same compare-and-swap, pointed at a counter -----

    @staticmethod
    def _id_ref(reg: str) -> str:
        """`refs/agent-sync/ids/<REG>` — the register's name verbatim, or a refusal.

        It used to be a slug, so `A B` and `A-B` were one counter and a name made only of
        punctuation was the invalid ref `refs/agent-sync/ids/`. A name the ref carries
        verbatim is exactly the set that slug left unchanged, so every counter that exists
        is still at the ref it was always at; the rest are refused instead of merged.
        """
        if not reg or lease_name(reg) != reg or legacy_ref_name(reg) != reg:
            raise Fail(
                f"register {reg!r} cannot name its id ref verbatim "
                "(refs/agent-sync/ids/<REG>): use letters, digits, `.`, `_` and `-`, not "
                "starting or ending with `-` or `.` — a name that has to be slugged shares "
                "its counter with every other name that slugs the same way")
        return "refs/agent-sync/ids/" + reg

    def _git_reserve_id(self, reg: str, floor: int, rkey: str | None = None) -> tuple[int, str]:
        """Allocate the next id for `reg` by compare-and-swap on a remote ref.
        Returns (value, winning commit sha) — the sha is the receipt's revision.

        The ref's tip commit body records the next free number. Winning the push IS
        the allocation: the remote accepts exactly one successor per tip, so two
        concurrent reserves cannot both take one number — the loser is rejected,
        re-reads the moved tip and takes the next. The value returned is immutable
        from that moment: no replay recomputes it, and the log line the caller
        writes afterwards is a receipt, never a claim.

        `rkey` closes the crash window between winning the CAS and writing the
        receipt: each counter commit carries the reservation key it served and is
        parented on the tip it replaced, so a retry of the SAME reservation finds
        its own allocation in the ref's history and returns it — one key, one
        number, however many times the caller had to come back.

        Retry is bounded by RESERVE_RETRIES: a remote that keeps moving under us is
        reported as contention, not spun on.
        """
        remote, ref = self._require_git_remote(f"reserve {reg}"), self._id_ref(reg)
        last_err = "push rejected"
        for _attempt in range(RESERVE_RETRIES):
            out = git("ls-remote", remote, ref)
            sha: str | None = None
            recorded = 0
            if out:
                sha = out.split()[0]
                git("fetch", "-q", remote, f"{ref}:refs/agent-sync/fetched-id")
                if rkey:
                    # The crash window: allocation won, receipt never written. The
                    # chain remembers which key each number was served to; a bounded
                    # walk suffices, because a retry follows its crash closely.
                    history = git("log", "-50", "--format=%H%x1f%B%x1e",
                                  "refs/agent-sync/fetched-id")
                    for entry in history.split("\x1e"):
                        entry = entry.strip()
                        if not entry:
                            continue
                        commit_sha, _, body_text = entry.partition("\x1f")
                        try:
                            served = json.loads(body_text.strip())
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if served.get("rkey") == rkey and served.get("reg") == reg \
                                and served.get("run") == self.rid:
                            return int(served["next"]) - 1, commit_sha.strip()
                body = git("log", "-1", "--format=%B", sha) or git(
                    "log", "-1", "--format=%B", "refs/agent-sync/fetched-id")
                try:
                    recorded = int(json.loads(body.strip()).get("next", 0))
                except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                    # An unreadable counter never resets allocation: the floor and the
                    # issued receipts below still push it forward.
                    recorded = 0
            # The register file and the log are floors, for the same reason the log
            # path consults the register: they know what was issued or written by
            # every path — including receipts from before this counter existed, and
            # a person's hand in the file. A floor only ever moves allocation forward.
            events, _ = self.events("reservations")
            _b, _f, assignments = resolve_reservations(events, reg)
            issued_next = max((v for _r, v in assignments), default=-1) + 1
            value = max(floor, recorded, issued_next)
            body_fields = {"reg": reg, "next": value + 1, "run": self.rid,
                           "ts": now_iso(), "repo": repo_name(),
                           "host": platform.node()}
            if rkey:
                body_fields["rkey"] = rkey
            payload = json.dumps(body_fields)
            empty_tree = git("hash-object", "-t", "tree", os.devnull)
            # Parented on the tip it replaces, so the ref keeps the chain a crashed
            # retry reads its own allocation back out of.
            tree_args = ["git", "-c", "user.name=agent-sync", "-c",
                         "user.email=agent-sync@localhost", "commit-tree", empty_tree]
            if sha:
                tree_args += ["-p", "refs/agent-sync/fetched-id"]
            made = subprocess.run(tree_args, input=payload, capture_output=True, text=True)
            commit = made.stdout.strip()
            if not commit:
                detail = (made.stderr or "").strip().splitlines()
                why = detail[-1] if detail else "no output from git commit-tree"
                raise Fail(f"could not create the id object — is this a git repository? ({why})")
            args = ["git", "push", remote, f"{commit}:{ref}"]
            if sha:                        # moving an existing counter, and only that
                args.insert(2, f"--force-with-lease={ref}:{sha}")
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode == 0:
                return value, commit
            err_lines = (r.stderr or "").strip().splitlines()
            last_err = err_lines[-1][:160] if err_lines else "push rejected"
            time.sleep(0.1 + random.random() * 0.2)
        raise Fail(
            f"reserve {reg}: the id ref on '{remote}' moved {RESERVE_RETRIES} times in a "
            f"row — another allocator is racing; retry ({last_err})")

    @property
    def lease_mode(self) -> str:
        return self.cfg.get("leaseBackend") or "local"

    @property
    def lease_is_cross_machine(self) -> bool:
        return self.lease_mode == "git"

    def _local_lock(self, key: str) -> Path:
        d = self.root / STATE_DIR / "leases"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{lease_name(key)}.lock"

    def _legacy_lock(self, key: str) -> Path | None:
        """The lock 1.21.2 wrote for `key`, when that is a different file — or None.

        Its stem turned every character outside `[A-Za-z0-9_-]` into `-`, dots included,
        and it carried no `key`. Such a lock is read as this key's only while it has no
        `key` field: a lock 1.21.3 wrote names its own key and is nobody else's.
        """
        legacy = self.root / STATE_DIR / "leases" / f"{legacy_lock_stem(key)}.lock"
        return None if legacy.name == f"{lease_name(key)}.lock" else legacy

    @staticmethod
    def _read_lock(lock: Path | None) -> dict[str, Any] | None:
        """The lock's payload, `{}` when unreadable, None when there is no such file."""
        if lock is None or not lock.exists():
            return None
        try:
            held = json.loads(lock.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return held if isinstance(held, dict) else {}

    def _legacy_holding(self, key: str) -> dict[str, Any] | None:
        """1.21.2's lock for `key`, if one exists, carries no `key`, and is still alive."""
        held = self._read_lock(self._legacy_lock(key))
        if held and "key" not in held and self._lease_alive(held, self.ttl):
            return held
        return None

    def _lock_names_other_key(self, key: str) -> str | None:
        """The key a lock at THIS key's path already names, when it is a different key.

        The encoding is injective, so on a case-sensitive filesystem this never fires. On
        a case-insensitive one (the macOS default) `T-1.lock` and `t-1.lock` are one file:
        without this check the second acquire overwrote the first's note, `held()` lost
        one of the two, and `release --held` left its ref on the remote — #25 by another
        route. Refused at acquire, where nothing has been written yet.
        """
        held = self._read_lock(self._local_lock(key))
        other = (held or {}).get("key")
        return other if isinstance(other, str) and other != key else None

    def lock_key(self, lock: Path, held: dict[str, Any] | None = None) -> str:
        """The key a lock file stands for: the `key` it names, else its decoded stem."""
        if held is None:
            held = self._read_lock(lock) or {}
        k = held.get("key")
        return k if isinstance(k, str) and k else lease_key(self._key_of(lock))

    # A lock created empty by O_EXCL and filled by a LATER write has a window
    # where a competitor reads it as `{}` — not live, therefore stealable — and
    # steals it while the creator writes on into a now-unlinked inode: two
    # winners (SY-05). The cure is to PUBLISH an already-filled inode atomically,
    # so a reader never sees an empty lock, and to treat any empty/partial lock
    # that does appear as a creation-in-flight (a short grace) rather than as
    # expired.
    CREATE_GRACE_SECONDS = 10

    def _publish_lock(self, lock: Path, body: dict) -> bool:
        """Atomically create `lock` already carrying `body`. Returns False if
        the lock already exists (someone else won the create). The bytes are
        written to a temp inode, fsync'd, then `os.link`ed onto the final name
        — link is a no-replace atomic create of a FULL inode, so no reader ever
        observes an empty lock."""
        tmp = lock.with_name(f"{lock.name}.{os.getpid()}.new")
        try:
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(body))
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(str(tmp), str(lock))   # atomic no-replace publish
            except FileExistsError:
                return False
            return True
        finally:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass

    def _enter_local_section(self, lock: Path):
        """The ONE critical section for every LOCAL lease mutation — steal, renew,
        release. O_EXCL on a second name is the OS-backed mutex between processes
        on this machine, and one section for every writer is what turns
        steal-vs-renew from a race into a sequence. Returns the guard path, or
        None when another run is inside (the caller defers, it never barges). A
        platform where the primitive itself fails raises `unsupported` out loud —
        an unlocked fallback would be exclusion by luck (SY-03)."""
        guard = lock.with_name(lock.name + ".steal")
        try:
            if guard.exists() and time.time() - guard.stat().st_mtime > STEAL_GRACE:
                guard.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            fd = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return None
        except OSError as exc:
            raise Fail(
                "the local lease critical section is unsupported here "
                f"({exc}) — an unlocked fallback would be exclusion by luck; set "
                "leaseBackend: \"git\" or configure a cloud backend") from exc
        os.close(fd)
        return guard

    @staticmethod
    def _exit_local_section(guard) -> None:
        if guard is not None:
            guard.unlink(missing_ok=True)

    @staticmethod
    def _key_of(lock: Path) -> str:
        return lock.name[:-len(".lock")] if lock.name.endswith(".lock") else lock.name

    def _steal_expired(self, lock: Path, payload: str) -> bool:
        """Replace an expired lock — reap and create as ONE critical section.

        They used to be two calls with a gap between them, and the gap is a hole in the
        exclusion: a second stealer that has already read the lock as expired removes the
        lock the first one just created, and both then hold what each believes is an
        exclusive lease. Twelve racing processes never showed it; a 300 ms delay injected
        between the two calls produced two winners out of two, and in production that
        delay is an ordinary scheduler hiccup.

        `O_EXCL` on a second name makes the section itself exclusive, and the expiry is
        re-read INSIDE it — so a run that gets in after the winner sees a live lease and
        loses, rather than reaping the lease it just missed. The section covers two
        filesystem calls, so its own abandonment grace is short; without one, a crash
        between them would cost the key until somebody deleted a file by hand.
        """
        guard = self._enter_local_section(lock)
        if guard is None:
            return False                      # another writer is inside the section
        try:
            raw = ""
            try:
                raw = lock.read_text()
                held = json.loads(raw)
            except (json.JSONDecodeError, OSError):
                held = {}
            if not held:
                # Empty or unparseable: a creation in flight, not an expired
                # lease. Only YIELD it once it is older than the creation grace
                # — arbitrated by the file's own age, never by "the JSON is
                # empty so it is free" (SY-05).
                try:
                    age = time.time() - os.stat(lock).st_mtime
                except OSError:
                    return False
                if age < self.CREATE_GRACE_SECONDS:
                    return False              # let the creator finish
            elif time.time() <= parse_iso(held.get("ts", "")) + int(
                    held.get("ttl", self.ttl)):
                return False                  # renewed, or already stolen and live again
            # The GENERATION moves on every ownership change, never on a renewal —
            # a reader holding a stale generation is holding a stale ownership.
            body = json.loads(payload)
            body["gen"] = int(held.get("gen", 0) or 0) + 1
            self._lease_gen[self._key_of(lock)] = body["gen"]
            lock.unlink(missing_ok=True)
            if not self._publish_lock(lock, body):
                return False                  # someone published between unlink and link
            return True
        except OSError:
            return False
        finally:
            self._exit_local_section(guard)

    def acquire(self, key: str) -> tuple[bool, str | None]:
        won, holder = self._acquire(key)
        if won:
            self._touch_activity(key, reset=True)
        return won, holder

    def _acquire(self, key: str) -> tuple[bool, str | None]:
        """Exclusion comes from an atomic file create; the cloud carries the record.

        This is the third design, and the first that is true. A single shared document
        lost writes (twelve concurrent appends, twelve reported successes, three lines
        present). Sharding fixed the loss and broke the decision: with no way to know a
        contender is still writing, eight processes each read only their own shard and
        eight of them won. No settle window closes that — the store has no
        compare-and-swap, so the question "has everyone written yet?" has no answer.

        `O_EXCL` does have one. It is a genuine mutex between processes on one machine,
        which is how these agents actually run. Across machines it is not, and the tool
        says so rather than implying a guarantee it cannot keep.
        """
        # One key, one name, everywhere (#25): a key that cannot come back as the same
        # string from the ref, the lock, the log and `whoami` is refused before anything
        # is written — never slugged into a different key the run then cannot release.
        problem = lease_key_problem(key)
        if problem:
            raise Fail(f"acquire refused: {problem}")
        other = self._lock_names_other_key(key)
        if other is not None:
            raise Fail(
                f"acquire refused: lease key {key!r} and the held key {other!r} resolve to "
                "one lock file in this checkout (a case-insensitive filesystem folds `T-1` "
                "and `t-1` together) — they cannot both be held here. Use one spelling")
        if self.lease_mode == "git":
            won, holder = self._git_acquire(key)
            if won:
                for n in self.write_claim(key, self.rid):
                    print(f"  {n}")
                try:
                    self.adapter.log_append(self.log_id("claims"), fmt_line(
                        "acquire", key, self.rid, ttl=self.ttl,
                        repo=repo_name(), sha=head_sha()))
                except Fail:
                    pass
            return won, holder

        lock = self._local_lock(key)
        # `host` is written in BOTH lease modes (AS-03). It used to be the git mode's alone,
        # on the reasoning that a `local` lease is machine-local by construction — but the
        # *file* is not: a checkout on a synced or shared directory is read by two machines,
        # and `classify_lock` consumes `host` to decide `foreign`. Without it the classifier
        # had one fewer way to refuse, on 25 of the 25 locks this family had on disk
        # (2026-08-20). Absent stays legal: locks written before this line exist.
        payload = json.dumps({"key": key, "run": self.rid, "ts": now_iso(),
                              "ttl": self.ttl, "repo": repo_name(), "host": platform.node(),
                              "since": now_iso()})

        # A lease another run took under 1.21.2's lock name is still that run's: the
        # upgrade must not hand the same key out a second time under the new name.
        if not lock.exists():
            legacy = self._legacy_holding(key)
            if legacy is not None and legacy.get("run") != self.rid:
                return False, legacy.get("run")

        if lock.exists():
            try:
                held = json.loads(lock.read_text())
            except (json.JSONDecodeError, OSError):
                held = {}
            if held.get("run") == self.rid:
                # An EXPIRED own lease is not refreshed, it is re-taken: the run id
                # matches, but a replacement session shares the run id — only the
                # generation bump tells the two apart, and a refresh here would let
                # the older session's heartbeat keep resurrecting it (FIX-SY-03.02).
                if time.time() > parse_iso(held.get("ts", "")) + int(
                        held.get("ttl", self.ttl)):
                    if self._steal_expired(lock, payload):
                        self._touch_renew(key)
                        return True, self.rid
                    try:
                        other = json.loads(lock.read_text()).get("run")
                    except (json.JSONDecodeError, OSError):
                        other = None
                    return False, other
                self._lease_gen[key] = int(held.get("gen", 0) or 0)
                # MOVE THE LOCK'S OWN `ts`, not just the throttle marker. This branch did
                # exactly what `_refresh_lease`'s docstring describes as the bug it exists
                # to have fixed — touch the throttle file and leave the timestamp the lease
                # is arbitrated by — and the repair landed in `renew` and not here, in the
                # same file, four minor versions ago.
                #
                # Two harms, and the second is the one that bites. The lock stayed expired,
                # so `classify_lock` read it as spent and another run's `acquire` took it
                # over while this one believed it held (measured 2026-09-01: a foreign
                # expired lock IS taken over, timestamp and host rewritten). And
                # `_touch_renew` resets the marker `renew()` throttles on — so a re-acquire
                # actively SUPPRESSED the next real refresh, refreshing nothing itself.
                # That is the mechanism behind a lease expiring three times in one run
                # against a 450-step CI job on 2026-09-01.
                self._refresh_lease(key)
                self._touch_renew(key)
                return True, self.rid
            if time.time() <= parse_iso(held.get("ts", "")) + int(held.get("ttl", self.ttl)):
                return False, held.get("run")
            if not self._steal_expired(lock, payload):
                try:
                    other = json.loads(lock.read_text()).get("run")
                except (json.JSONDecodeError, OSError):
                    other = None
                return False, other
        else:
            # Publish an already-filled lock atomically — no empty window a
            # competitor could read as free (SY-05).
            if not self._publish_lock(lock, {**json.loads(payload), "gen": 1}):
                try:
                    other = json.loads(lock.read_text()).get("run")
                except (json.JSONDecodeError, OSError):
                    other = None
                return False, other
            self._lease_gen[key] = 1

        self._touch_renew(key)
        for n in self.write_claim(key, self.rid):
            print(f"  {n}")
        # Record it for everyone else to see. A failure here costs visibility, never
        # correctness — the lock is already held — so it must not fail the acquire.
        if self.adapter.is_lease_authority:
            try:
                self.adapter.log_append(self.log_id("claims"), fmt_line(
                    "acquire", key, self.rid, ttl=self.ttl,
                    repo=repo_name(), sha=head_sha()))
            except Fail as exc:
                print(f"note: lease held, but not published to the plane ({exc})",
                      file=sys.stderr)
        return True, self.rid

    def _refresh_lease(self, key: str) -> bool:
        """Move the timestamp this lease is expired by, in the plane that arbitrates it.

        This is what `renew` means, and for four minor versions it did not happen. `renew`
        appended `op=renew` to the RECORD plane — which has not decided a lease since
        1.0.0 — and touched a throttle file. The lock's own `ts` was written once, by
        `acquire`. So a run holding a lease lost it at TTL while still working: its own
        guard began denying it, and another run acquired the task it was in the middle of.
        The `PostToolUse` hook changed nothing, because there was nothing for it to move.
        """
        if self.lease_mode == "git":
            sha, held, ref = self._git_read_lease(key)
            if not sha or held.get("run") != self.rid:
                return False
            if time.time() > parse_iso(held.get("ts", "")) + int(
                    held.get("ttl", self.ttl)):
                print(f"note: {key}: this lease expired — a renewal does not "
                      "resurrect it; acquire it again", file=sys.stderr)
                return False
            payload = json.dumps({**held, "key": key, "ts": now_iso()})
            empty_tree = git("hash-object", "-t", "tree", os.devnull)
            made = subprocess.run(
                ["git", "-c", "user.name=agent-sync", "-c", "user.email=agent-sync@localhost",
                 "commit-tree", empty_tree],
                input=payload, capture_output=True, text=True)
            commit = made.stdout.strip()
            if not commit:
                return False
            # Against the exact object just read: a renewal must never overwrite a lease
            # somebody else took while this run was between the read and the push.
            r = subprocess.run(["git", "push", self._git_remote(),
                                f"--force-with-lease={ref}:{sha}",
                                f"{commit}:{ref}"], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"note: could not renew {key} on the remote: {r.stderr.strip()[:160]}",
                      file=sys.stderr)
                return False
            self._note_local(key, payload)
            return True

        lock = self._local_lock(key)
        if not lock.exists():
            return False
        guard = self._enter_local_section(lock)
        if guard is None:
            # A steal (or another writer) is inside the section. Deferring is the
            # serialization: rewriting the timestamp NOW would race the stealer's
            # own expiry re-read, and two writers on one lock file is the defect.
            print(f"note: {key}: another writer holds the local section — "
                  "renewal deferred to the next heartbeat", file=sys.stderr)
            return False
        tmp = lock.with_name(f"{lock.name}.{os.getpid()}.tmp")
        try:
            try:
                held = json.loads(lock.read_text())
            except (json.JSONDecodeError, OSError):
                return False
            if held.get("run") != self.rid:
                return False
            # An expired lease is DEAD, whoever's name is on it. Renewing it here
            # would resurrect what a stealer may already have decided is up for
            # grabs; the owner of an expired lease acquires again (FIX-SY-03.02).
            if time.time() > parse_iso(held.get("ts", "")) + int(
                    held.get("ttl", self.ttl)):
                print(f"note: {key}: this lease expired — a renewal does not "
                      "resurrect it; acquire it again", file=sys.stderr)
                return False
            # The generation fence: a replacement session shares the run id, so
            # the run check above cannot tell the old session from the new one —
            # only the generation this OBJECT acquired under can. A recorded
            # generation that no longer matches the lock's means a steal
            # completed after this session's acquire; its renewal must lose.
            lock_gen = int(held.get("gen", 0) or 0)
            expected = self._lease_gen.get(key)
            if expected is not None and lock_gen != expected:
                print(f"note: {key}: held under generation {expected}, the lock "
                      f"is at {lock_gen} — a newer owner took it; acquire it "
                      "again", file=sys.stderr)
                return False
            held["ts"] = now_iso()            # the generation is OWNERSHIP's; a renewal keeps it
            tmp.write_text(json.dumps(held))
            tmp.replace(lock)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            print(f"note: could not renew {key} ({exc})", file=sys.stderr)
            return False
        finally:
            self._exit_local_section(guard)
        return True

    def renew(self, key: str | None = None, *, active_only: bool = False) -> bool:
        """Refresh leases — each against ITS OWN throttle, never a shared one.

        The throttle used to be one file per checkout. Run A touching it every
        hundred seconds meant run B's heartbeat read "renewed recently" for
        forty-five minutes straight, refreshed nothing, and B's lease expired
        under work in progress — one agent's activity suppressing every other
        run's renewals in the same checkout. The marker is now per (run, key):
        another agent's activity, and this run's OTHER keys, are invisible here,
        which is the point.
        """
        interval = int(self.cfg.get("renewIntervalSeconds") or DEFAULT_RENEW)
        if key is not None:
            if active_only and not self._recent_activity(key, interval):
                return False
            # An explicit renew answers for THIS key: a real refresh, or the
            # precise per-key reason there was none. It never hides behind the
            # heartbeat's throttle — the caller named the key on purpose.
            if self._refresh_lease(key):
                if self.adapter.is_lease_authority:
                    self.adapter.log_append(self.log_id("claims"),
                                            fmt_line("renew", key, self.rid))
                self._touch_renew(key)
                if not active_only:
                    self._touch_activity(key)
                return True
            age = self._renew_age(key)
            why = (f"its renewal marker is {int(age)}s old" if age is not None
                   else "no renewal of it is on record for this run")
            print(f"note: could not renew {key} — this run does not hold it in the "
                  f"lease plane ({why})", file=sys.stderr)
            return False
        due = []
        for k in self.held():
            if active_only and not self._recent_activity(k, interval):
                continue
            age = self._renew_age(k)
            if age is None or age >= interval:
                due.append(k)
        if not due:
            return False
        renewed = [k for k in due if self._refresh_lease(k)]
        if self.adapter.is_lease_authority and renewed:
            oid = self.log_id("claims")
            for k in renewed:
                self.adapter.log_append(oid, fmt_line("renew", k, self.rid))
        for k in renewed:
            self._touch_renew(k)
            if not active_only:
                self._touch_activity(k)
        return bool(renewed)

    def _activity_marker(self, key: str) -> Path:
        return self.root / STATE_DIR / "activity" / f"{self.rid}--{lease_name(key)}"

    def _activity(self, key: str) -> dict:
        try:
            data = json.loads(self._activity_marker(key).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _touch_activity(self, key: str, *, task: str | None = None,
                        reset: bool = False) -> None:
        # Local scheduling metadata, never lease authority or a new wire key.
        data = {} if reset else self._activity(key)
        data["at"] = now_iso()
        if task is not None:
            data["task"] = task
        marker = self._activity_marker(key)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(data))
        except OSError as exc:
            print(f"note: {key}: activity not recorded ({exc}); automatic renewal "
                  "may stop — explicitly renew or release this lease", file=sys.stderr)

    def _recent_activity(self, key: str, interval: int) -> bool:
        ts = parse_iso_or_none(self._activity(key).get("at"))
        return ts is not None and 0 <= time.time() - ts <= 2 * interval

    def _guard_activity(self, resource: str, tasks: list[str]) -> None:
        task = self._activity(resource).get("task")
        if task not in tasks:
            task = tasks[0] if len(tasks) == 1 else None
        self._touch_activity(resource, task=task)
        if task is not None:
            self._touch_activity(task)
        elif len(tasks) > 1:
            print("note: resource activity names no unambiguous task; release stale "
                  "claims or explicitly renew the task still being worked", file=sys.stderr)

    def _renew_marker(self, key: str) -> Path:
        return self.root / STATE_DIR / "renew" / f"{self.rid}--{lease_name(key)}"

    def _renew_age(self, key: str) -> float | None:
        """Seconds since THIS run last renewed THIS key, or None when it never has.
        The marker carries its timestamp in its bytes, not its mtime, so the same
        clock the throttle compares against is the one that wrote it."""
        try:
            ts = parse_iso_or_none(self._renew_marker(key).read_text().strip())
        except OSError:
            return None
        if ts is None:
            return None
        return time.time() - ts

    def _touch_renew(self, key: str) -> None:
        """Stamp the (run, key) marker. Called on a successful refresh — and on
        acquire, whose fresh lease IS a renewal of exactly that key. Never a
        by-product of unrelated activity: that by-product was the defect."""
        marker = self._renew_marker(key)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(now_iso())

    @staticmethod
    def _lease_alive(held: dict, default_ttl: int) -> bool:
        """Is this lease still within its TTL? The ONE definition of held.

        `acquire` has always answered this question — `_steal_expired` exists
        precisely to take a lease whose TTL has run out. `release` did not ask it,
        and read any lock file as held. So the file carried two notions of *held*
        and they disagreed exactly where it mattered: a lease from a run that died
        was expired for `acquire` and eternal for `release`, and no command could
        clear it. Measured in the field at **604x** its 2700-second TTL, released
        only by deleting the file by hand — which is the one thing a coordination
        tool exists to stop anybody doing.
        """
        try:
            return time.time() <= parse_iso(held.get("ts", "")) + int(held.get("ttl", default_ttl))
        except (ValueError, TypeError):
            # An unparseable timestamp is not a licence to hold forever.
            return False

    def _lease_holder(self, key: str) -> str | None:
        """Who holds this lease right now, in whichever plane arbitrates it.

        `None` for an expired lease as well as an absent one: the TTL is the
        contract, and a lease past it is not held by anybody.
        """
        if self.lease_mode == "git":
            sha, held, _ref = self._git_read_lease(key)
            if not sha or not self._lease_alive(held, self.ttl):
                return None
            return held.get("run")
        held = self._read_lock(self._local_lock(key))
        if held is None:
            legacy = self._legacy_holding(key)
            return legacy.get("run") if legacy else None
        return held.get("run") if held and self._lease_alive(held, self.ttl) else None

    def release(self, key: str) -> bool:
        """Release only what this run holds, and say so plainly when it does not.

        This used to clear the board claim and report success unconditionally. The lease
        plane refused correctly — `_git_release` prints a note and returns — but the
        caller printed "released" over the top of it and exited 0, and `write_claim` had
        already blanked the claim cell on the way in. The board then said the task was
        free while the lease said it was taken: the exact disagreement a lease exists to
        prevent, manufactured by the tool. Ownership is therefore checked FIRST, and
        nothing is written when the answer is no.
        """
        holder = self._lease_holder(key)
        if holder is not None and holder != self.rid:
            print(f"note: {key} is held by {holder}, not this run — nothing released",
                  file=sys.stderr)
            return False

        # The remote FIRST, and nothing else is touched unless it is proved gone (#25).
        # This ran after the board claim was blanked and before the note was dropped,
        # and its failure was a stderr note: the command printed "released", exited 0,
        # and removed the one local trace of a lease that was still on the remote.
        git_ref = None
        if self.lease_mode == "git":
            state, why, git_ref = self._git_release(key)
            if state == "kept":
                print(f"note: {why} — NOT released; the local note is kept, because the "
                      "lease is still on the remote", file=sys.stderr)
                return False
            if state == "absent":
                # Nothing under this name — but it may be the name 1.21.2 REPORTED for a
                # lease it took under another (`T-1-2` for `T-1.2`). Resolved only for a
                # name no 1.21.3 note claims, and only to refs 1.21.2 wrote.
                own = self._read_lock(self._local_lock(key))
                if own is None or "key" not in own:
                    aliases = self._legacy_aliases(key)
                    if aliases:
                        print(f"  {key} is the name agent-sync 1.21.2 reported for "
                              f"{', '.join(aliases)} — releasing it under that name")
                        ok = [a for a in aliases if self.release(a)]
                        self.released_as[key] = ok
                        if len(ok) == len(aliases):
                            self._drop_note(self._local_lock(key), key, legacy_only=True)
                        return len(ok) == len(aliases)
                    git_ref = None

        # Releasing the last TASK key releases this run's resource claims too:
        # a file claim only ever rides under a task lease (FIX-SY-04.01), and
        # an orphaned one would hold the registry for a task already finished.
        if not key.startswith(self.RESOURCE_PREFIX):
            remaining = [k for k in self.held()
                         if not k.startswith(self.RESOURCE_PREFIX) and k != key]
            for res in [k for k in self.held()
                        if k.startswith(self.RESOURCE_PREFIX)]:
                if not remaining or self._activity(res).get("task") == key:
                    if not self.release(res):
                        print(f"note: {key}: resource {res} was not released; "
                              "task handoff is incomplete, retry release", file=sys.stderr)
                        return False

        for n in self.write_claim(key, None):
            print(f"  {n}")
        self._drop_note(self._local_lock(key), key)
        # 1.21.2's note for this key, under its slug — only while it names no key.
        self._drop_note(self._legacy_lock(key), key, legacy_only=True)
        if git_ref and git_ref != self._ref(key):
            # The lease lived on its 1.21.2 ref, and `migrate_legacy_notes` may already
            # have re-noted it under that ref's own key: that note goes with it.
            ref_key = lease_key(git_ref[len(LEASE_REF_PREFIX):])
            self._drop_note(self._local_lock(ref_key), key, names={ref_key})
        self.released_as.setdefault(key, [key])
        try:
            self._activity_marker(key).unlink(missing_ok=True)
        except OSError:
            # Scheduling metadata cannot undo the authority's confirmed release.
            print(f"note: {key}: released; activity metadata remains — it cannot "
                  "renew a lease this run no longer holds", file=sys.stderr)
        if self.adapter.is_lease_authority:
            try:
                self.adapter.log_append(self.log_id("claims"),
                                        fmt_line("release", key, self.rid))
            except Fail as exc:
                print(f"note: released locally, not published ({exc})", file=sys.stderr)
        return True

    def _drop_note(self, lock: Path | None, key: str, *, legacy_only: bool = False,
                   names: set[str] | None = None) -> None:
        """Remove a lock/note this release covers: this run's, ownerless, or spent.

        `legacy_only` — only a 1.21.2 lock (no `key`), never one that names a key.
        `names` — only a lock naming one of these keys.
        """
        held = self._read_lock(lock)
        if held is None or lock is None:
            return
        if legacy_only and "key" in held:
            return
        if names is not None and held.get("key") not in names:
            return
        owner = held.get("run")
        if not held or owner in (self.rid, None):
            lock.unlink(missing_ok=True)
        elif not self._lease_alive(held, self.ttl):
            # Reaped, not stolen — and said out loud, because the operator asked to
            # release THEIR lease and is getting somebody else's corpse cleared as well.
            print(f"  reaped {key}: expired lease from {owner}, "
                  f"whose run left it past its TTL")
            lock.unlink(missing_ok=True)

    @property
    def identity(self) -> tuple[str, str]:
        """(key, how) for this run's identity — resolved once, because it walks `ps`."""
        if self._identity is None:
            self._identity = _session_key()
        return self._identity

    @property
    def identity_is_strong(self) -> bool:
        """Whether a matching run id is proof of anything.

        False means `_session_key()` established nothing and `run_id()` served the shared
        entry — the identity every other session in this checkout is also given. Enough to
        coordinate with, and deliberately not enough to delete on.
        """
        return bool(self.identity[0])

    # -- the git plane's own enumerating read (AS-01a) -------------------------

    def _git_lease_refs(self) -> tuple[list[tuple[str, str]], str | None]:
        """Every lease ref on the remote as (sha, name), or the reason it could not look.

        `name` is the ref with the prefix removed; `lease_key(name)` is the key, unless the
        payload names one (1.21.3 writes it there).

        The authority in git mode is `refs/agent-sync/leases/*` on the remote, and a ref
        won on another machine leaves NO local note — `_note_local` only fires for the run
        that won it here. So the local directory walk cannot see it, and for a while
        `residue` could print `nothing on disk` over an expired lease sitting on the
        remote. That is the one shape a residue report must never take.

        The failure is returned rather than raised, because the caller has to be able to
        say `could not look` instead of `nothing there`.
        """
        remote = self._git_remote()
        r = subprocess.run(["git", "ls-remote", remote, "refs/agent-sync/leases/*"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            why = (r.stderr or r.stdout or "").strip().splitlines()
            return [], (why[-1] if why else f"`git ls-remote {remote}` failed")
        out = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) != 2 or not parts[1].startswith("refs/agent-sync/leases/"):
                continue
            # The NAME after the prefix, whole: a lease name never contains `/` (it is
            # escaped), so anything nested here was not written by this tool and is still
            # reported by the name it actually has.
            out.append((parts[0], parts[1][len(LEASE_REF_PREFIX):]))
        return out, None

    def _git_lease_payload(self, sha: str) -> str:
        """The lease object's body — the same JSON the local note carries.

        One shape for both planes, so `classify_lock` is the only classifier in this file.
        A second one would be a second definition of *whose lease this is*, which is the
        question the whole tool exists to answer once.
        """
        body = git("log", "-1", "--format=%B", sha)
        if body:
            return body.strip()
        # The object may not be local yet: the ref lives on the remote.
        subprocess.run(["git", "fetch", "-q", self._git_remote(), sha],
                       capture_output=True, text=True)
        return (git("log", "-1", "--format=%B", sha) or "").strip()

    def git_residue(self) -> tuple[list[dict[str, Any]], str | None]:
        """The git plane, classified by the same rules as the local one."""
        refs, why = self._git_lease_refs()
        if why is not None:
            return [], why
        now, host, repo = time.time(), platform.node(), repo_name()
        out: list[dict[str, Any]] = []
        for sha, comp in sorted(refs, key=lambda x: x[1]):
            raw = self._git_lease_payload(sha)
            try:
                parsed = json.loads(raw) if raw else None
            except (json.JSONDecodeError, ValueError):
                parsed = None
            named = parsed.get("key") if isinstance(parsed, dict) else None
            # The key the ref was taken as: the one its payload names (1.21.3), else its
            # decoded name — which for every ref 1.21.2 pushed is the name itself.
            key = named if isinstance(named, str) and named else lease_key(comp)
            if raw:
                entry = classify_lock(key, raw, rid=self.rid,
                                      identity_is_strong=self.identity_is_strong,
                                      repo=repo, host=host, default_ttl=self.ttl, at=now)
            else:
                entry = {"key": key, "state": AMBIGUOUS, "run": None, "repo": None,
                         "host": None, "ts": "", "expired_for": None,
                         "why": "the lease object carries no readable payload"}
            entry["plane"] = "git"
            # AS-14's revisit condition, as a fact every sweep reports: a payload that parses
            # as an object with no `key` was written by 1.21.2 or older. That dates the write;
            # it does not prove an old writer is still running.
            entry["keyless"] = payload_is_keyless(raw)
            entry["ref"] = LEASE_REF_PREFIX + comp        # the ref that exists, as it exists
            entry["sha"] = sha
            entry["path"] = None
            out.append(entry)
        return out, None

    def git_reap(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Delete a reapable lease ref, and prove it went by looking again.

        Same compare-and-swap `_git_release` uses: `--force-with-lease=<ref>:<sha>` refuses
        if somebody won the key between the read and the delete, which is the whole reason
        the sha travels with the entry. And the verdict comes from a second `ls-remote`,
        not from the push's exit code — a teardown that reports success out of its own
        return value is reporting the wish rather than the state.
        """
        done: list[dict[str, Any]] = []
        for e in entries:
            r = subprocess.run(["git", "push", self._git_remote(),
                                f"--force-with-lease={e['ref']}:{e['sha']}",
                                f":{e['ref']}"], capture_output=True, text=True)
            after, why = self._git_lease_refs()
            still = {LEASE_REF_PREFIX + c for _s, c in after}
            if why is not None:
                e["gone"] = None
                e["why_gone"] = f"could not re-read the remote to prove it went ({why})"
            elif e["ref"] in still:
                e["gone"] = False
                e["why_gone"] = ((r.stderr or "").strip().splitlines() or
                                 ["the ref is still on the remote"])[-1]
            else:
                e["gone"] = True
                e["why_gone"] = ""
            done.append(e)
        return done

    def residue(self) -> list[dict[str, Any]]:
        """Every lock file in this checkout, classified — the live ones included.

        The enumerating read the tool never had. `held()` below globs the same directory
        and keeps only what is BOTH this run's and alive, which makes it a liveness
        predicate: it cannot tell an empty directory from one full of corpses, and no
        other reader here can either.
        """
        d = self.root / STATE_DIR / "leases"
        now, host, repo = time.time(), platform.node(), repo_name()
        out: list[dict[str, Any]] = []
        for p in sorted(d.glob("*.lock") if d.exists() else []):
            try:
                raw = p.read_text()
            except OSError as exc:
                entry: dict[str, Any] = {
                    "key": lease_key(p.stem), "state": AMBIGUOUS, "run": None,
                    "repo": None, "host": None, "ts": "", "expired_for": None,
                    "why": f"the lock cannot be read ({exc})"}
            else:
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    parsed = {}
                key = self.lock_key(p, parsed if isinstance(parsed, dict) else {})
                entry = classify_lock(key, raw, rid=self.rid,
                                      identity_is_strong=self.identity_is_strong,
                                      repo=repo, host=host, default_ttl=self.ttl, at=now)
            entry["path"] = p
            out.append(entry)
        return out

    def stale(self) -> list[dict[str, Any]]:
        """The residue only — every lock whose lease has already ended."""
        return [e for e in self.residue() if e["state"] != LIVE]

    def reap(self, keys: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Clear only `reapable` residue, and prove it went by LOOKING AGAIN.

        The second observation is the whole point. `unlink` returns nothing and raises
        nothing on a filesystem where the entry survives the call — a read-only mount, an
        NFS write that never lands, a directory whose write bit was dropped between two
        commands, another process recreating the name — so a teardown that reports success
        out of its own return value is reporting the wish rather than the state. What comes
        back here is the difference between two reads of the directory.

        Identity decides the second read, not absence: a key that came back as another
        run's live lease WAS torn down, and calling that a failure would teach an operator
        to ignore the one message that matters.
        """
        before = self.residue()
        named = None
        if keys:
            named = set()
            for k in keys:
                named.add(k)
                # A 1.21.2 lock reads back as its slug, so the key it was taken as must
                # still find it.
                named.add(legacy_lock_stem(k))
        wanted = [e for e in before
                  if e["state"] == REAPABLE and (named is None or e["key"] in named)]
        refused = [e for e in before
                   if e["state"] != REAPABLE and named is not None and e["key"] in named]
        if named is not None:
            known = {e["key"] for e in before}
            for k in sorted(named - known):
                if legacy_lock_stem(k) in known:
                    continue
                refused.append({"key": k, "state": "absent", "run": None, "ts": "",
                                "expired_for": None,
                                "why": "there is no lock by that name in this checkout"})

        for e in wanted:
            try:
                e["path"].unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                e["error"] = str(exc)

        after = {e["key"]: e for e in self.residue()}
        reaped, remaining = [], []
        for e in wanted:
            still = after.get(e["key"])
            if still is not None and still["run"] == e["run"] and still["ts"] == e["ts"]:
                remaining.append(e)
            else:
                reaped.append(e)
        return {"reaped": reaped, "remaining": remaining, "refused": refused,
                "left": [e for e in before if e["state"] in (FOREIGN, AMBIGUOUS)]}

    def claim_tags_on_disk(self) -> list[dict[str, Any]]:
        """Every claim tag written into a registry file, with the run each one names.

        The enumerating read for the OTHER plane, and it did not exist. `held()`,
        `_lease_holder()` and `residue()` all answer from the lease; nothing answered from
        the board — so a tag whose lease had ended was reported by no command at all.
        `release` printed success and changed nothing (`write_claim` had no saved cell to
        undo), `residue` said "nothing on disk", `status` said "leases held: none", and
        `reconcile` never mentioned it. Filed as ssheleg/agent-sync#5 and reproduced
        verbatim at 1.14.0.
        """
        out: list[dict[str, Any]] = []
        for pattern, spec in (self.cfg.get("claimTags") or {}).items():
            if spec.get("mode") != "cell":
                continue
            rx = claim_marker_re(spec.get("held") or DEFAULT_CLAIM_TEMPLATE)
            if rx is None:
                continue
            idx = int(spec.get("cell", -1))
            for path in sorted(glob_files(self.root, pattern)):
                if not path.is_file():
                    continue
                try:
                    lines = path.read_text().splitlines()
                except OSError:
                    continue
                rel = str(path.relative_to(self.root))
                for n, line in enumerate(lines, 1):
                    cells = self._row_cells(line)
                    if not cells or not -len(cells) <= idx < len(cells):
                        continue
                    m = rx.search(cells[idx])
                    if not m:
                        continue
                    out.append({"key": cells[0].strip(), "holder": m.group("holder"),
                                "file": rel, "line": n, "cell": cells[idx].strip()})
        return out

    def _holder_of(self, key: str) -> str | None:
        """`_lease_holder`, memoised for the length of one command.

        One notion of held, and only one reader of it. Memoised because in git mode that
        reader is an `ls-remote`: a report over a board with several tags would otherwise
        pay a network round-trip per row. The bound is the number of TAGGED rows, not the
        size of the board — an untagged row is never looked up.
        """
        if key not in self._holders:
            self._holders[key] = self._lease_holder(key)
        return self._holders[key]

    def orphan_claims(self) -> list[dict[str, Any]]:
        """Claim tags with no live lease behind them — one notion of held, both planes.

        The TTL is the contract. A tag is a claim about a lease, so once `_lease_holder` —
        the single reader every other command already trusts — answers `None`, the tag is
        residue in the registry exactly as an expired lock is residue on disk, and is
        reported the same way.

        A tag naming a run that still holds the lease is not residue and never appears
        here. A tag naming one run while the live lease belongs to another is `disputed`:
        reported, never cleared, because clearing it would edit a registry under a run
        that is still working.
        """
        out: list[dict[str, Any]] = []
        for e in self.claim_tags_on_disk():
            holder = self._holder_of(e["key"])
            if holder == e["holder"]:
                continue
            e = dict(e)
            if holder is None:
                e["state"] = ORPHAN
                e["why"] = (f"the tag names run {e['holder']}, and the {self.lease_mode} "
                            f"lease plane holds no live lease for `{e['key']}` — the TTL "
                            "has already ended it")
            else:
                e["state"] = DISPUTED
                e["why"] = (f"the tag names run {e['holder']} while the live lease for "
                            f"`{e['key']}` is held by {holder}")
            out.append(e)
        return out

    def held_ages(self) -> dict[str, float]:
        """Seconds each held lease has been held, from its `since` (or `ts`) — R19."""
        d = self.root / STATE_DIR / "leases"
        held = set(self.held())
        out: dict[str, float] = {}
        now = time.time()
        for q in sorted(d.glob("*.lock") if d.exists() else []):
            try:
                h = json.loads(q.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            key = h.get("key") or q.stem
            if key not in held or h.get("run") != self.rid:
                continue
            t = parse_iso_or_none(str(h.get("since") or h.get("ts") or ""))
            if t is not None:
                out[key] = max(0.0, now - t)
        return out

    def held(self) -> list[str]:
        """The keys this run holds — each spelled exactly as it was acquired (#25).

        Local only, by design: the PreToolUse guard asks this on every Edit. The key is
        the one the note NAMES; the stem is only its encoded filename, and reporting the
        stem is what turned `T-1.2` into `T-1-2`. A 1.21.2 note names no key and reads
        back as its stem until `migrate_legacy_notes` (run by whoami/status/release)
        rewrites it from the remote.
        """
        d = self.root / STATE_DIR / "leases"
        mine = set()
        for q in sorted(d.glob("*.lock") if d.exists() else []):
            try:
                h = json.loads(q.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(h, dict):
                continue
            if h.get("run") == self.rid and \
                    time.time() <= parse_iso(h.get("ts", "")) + int(h.get("ttl", self.ttl)):
                mine.add(self.lock_key(q, h))
        return sorted(mine)

    # -- ids ---------------------------------------------------------------

    def reserve(self, reg: str, rkey: str | None = None) -> int:
        authority, _how = id_allocator(self.cfg, self.adapter)
        if authority is None:
            raise Fail(
                f"backend '{self.adapter.name}' cannot reserve ids safely "
                "(atomicAppend is false and the lease backend is not git). Allocate by "
                "hand and record it, set `leaseBackend: \"git\"`, use `reserve --offline` "
                "for a namespaced offline id, or configure a cloud backend. Pretending "
                "would hand two agents the same id.")
        # A named reservation retried is the SAME reservation. If this run's receipt
        # for the key is already in the merged log, hand its number back — a retry
        # that allocates again is how one crash costs two ids.
        if rkey:
            events, _ = self.events("reservations")
            for ev in events:
                if ev.get("op") == "reserve" and ev.get("key") == reg \
                        and ev.get("run") == self.rid and ev.get("rkey") == rkey \
                        and (ev.get("value") or ""):
                    return int(ev["value"])
        # The register knows what is actually written, by every path including the ones
        # that never touch this tool — a person editing the file, another session's Doc
        # Loop, a merge. It is a **floor**, never a ceiling: it can only push allocation
        # forward, so honouring it never revokes a live reservation.
        floor = self._seed_base(reg)
        oid = self.log_id("reservations")
        if authority == "git":
            # Across machines the shards are git files, and a shard another machine has
            # not pushed yet is invisible here — positional replay over what IS visible
            # handed two machines the same number. The remote id ref is the one piece of
            # state both sides must move through, so the compare-and-swap on it is the
            # allocator; the line below is a receipt of a number already won.
            value, rev = self._git_reserve_id(reg, floor, rkey)
            # The receipt names its authority: which backend allocated, at which
            # revision, for which reservation key. A number nobody can trace to an
            # authority is a rumour with digits.
            self.adapter.log_append(
                oid, fmt_line("reserve", reg, self.rid, value=f"{value:04d}",
                              backend="git", rev=rev[:12], rkey=rkey))
            return value
        # Total-order backends (atomicAppend): optimistic claim. Every shard, not just
        # this run's — `log_id` returns the document THIS run writes, and reading it
        # alone was the whole defect: three runs each replayed a log containing only
        # their own lines, and each was handed the same number. The claim carries its
        # value, so once it wins the read-back it can never be renumbered by a shard
        # that arrives later or a base appended afterwards.
        for _attempt in range(RESERVE_RETRIES):
            events, _ = self.events("reservations")
            base, _free, _assign = resolve_reservations(events, reg)
            if not base:
                self.adapter.log_append(
                    oid, fmt_line("base", reg, self.rid, value=f"{floor:04d}"))
                events, _ = self.events("reservations")
            else:
                # Probed rather than computed: the allocator is asked what it *would*
                # hand out next, by resolving a synthetic reserve. That keeps one
                # implementation of the allocation rule instead of a second copy here
                # that can disagree with it.
                probe = events + [{"op": "reserve", "key": reg, "run": "\x00probe",
                                   "value": ""}]
                _b, _f, probed = resolve_reservations(probe, reg)
                if probed and probed[-1][1] < floor:
                    self.adapter.log_append(
                        oid, fmt_line("base", reg, self.rid, value=f"{floor:04d}"))
                    events, _ = self.events("reservations")
            probe = events + [{"op": "reserve", "key": reg, "run": "\x00probe",
                               "value": ""}]
            _b, _f, probed = resolve_reservations(probe, reg)
            if not probed:
                raise Fail(f"reserve {reg}: could not resolve a candidate — the "
                           "reservations log has no usable base; retry")
            candidate = probed[-1][1]
            self.adapter.log_append(
                oid, fmt_line("reserve", reg, self.rid, value=f"{candidate:04d}",
                              backend="log", rkey=rkey))
            time.sleep(0.25 + random.random() * 0.15)
            events, _ = self.events("reservations")
            _b, _f, assignments = resolve_reservations(events, reg)
            winners = [r for r, v in assignments if v == candidate]
            if winners and winners[-1] == self.rid:
                return candidate
            if not any(r == self.rid and v == candidate for r, v in assignments) and \
                    not winners:
                raise Fail(f"reserve {reg}: the append did not read back — retry")
            # Somebody's receipt for the same number sits earlier in the merged order.
            # That run keeps it; this one re-reads and takes the next number.
        raise Fail(f"reserve {reg}: lost the allocation race {RESERVE_RETRIES} times "
                   "in a row — another allocator is racing; retry")

    def _seed_base(self, reg: str) -> int:
        spec = (self.cfg.get("idRegisters") or {}).get(reg)
        if not spec:
            raise Fail(f"register '{reg}' is not declared in .claude/agent-sync.json")
        path = self.root / spec["file"]
        if not path.exists():
            raise Fail(f"register file {spec['file']} does not exist")
        pat = id_pattern(spec)
        if not pat:
            raise Fail(f"register has no id pattern — set `pattern` (or the legacy "
                       f"`nextFreeIdPattern`) for {spec['file']}; without one there is "
                       f"nothing to read a next free id out of")
        m = re.search(pat, path.read_text())
        if not m:
            raise Fail(f"could not read the next free id out of {spec['file']}")
        return int(m.group(1))

    def release_id(self, reg: str, value: str) -> None:
        """Return an id to the pool — or say plainly that nothing recorded it.

        On a backend that cannot order writes this used to do nothing and print
        "released" anyway. The id stayed a hole the board reports as a leak, and the only
        party who could have fixed that had been told it was handled."""
        if id_allocator(self.cfg, self.adapter)[0] is None:
            raise Fail(
                f"backend '{self.adapter.name}' cannot record a released id "
                "(atomicAppend is false and the lease backend is not git), so nothing "
                "was returned to the pool. Note it in the register by hand, or "
                f"configure a backend that can: {reg}-{value}")
        self.adapter.log_append(self.log_id("reservations"),
                                fmt_line("release_id", reg, self.rid, value=value))

    def reserve_offline(self, reg: str) -> str:
        """A namespaced id issued with NO global authority — `<REG>-o-<run>-<seq>`.

        Deliberately SHAPED so it cannot collide with the numeric sequence: the
        global allocator hands out digits, this hands out an `o-` composite keyed
        by run identity. There is no fake global counter behind it, which is the
        point — an offline allocator that pretends to know the next number is the
        two-agents-one-id defect with extra steps. Map it onto a real number later
        with `map_offline`, append-only."""
        if (self.cfg.get("idRegisters") or {}).get(reg) is None:
            raise Fail(f"register '{reg}' is not declared in .claude/agent-sync.json")
        oid = self.log_id("reservations")
        events, _ = self.events("reservations")
        mine = sum(1 for ev in events if ev.get("op") == "reserve_offline"
                   and ev.get("key") == reg and ev.get("run") == self.rid)
        suffix = re.sub(r"[^a-z0-9]", "", self.rid.lower())[-8:] or "anon"
        token = f"o-{suffix}-{mine + 1:03d}"
        self.adapter.log_append(oid, fmt_line("reserve_offline", reg, self.rid, value=token))
        return f"{reg}-{token}"

    def map_offline(self, reg: str, offline_id: str, number: str) -> None:
        """Bind an offline id to a real, properly reserved number — append-only.

        The same fact twice is one fact; a DIFFERENT number for a mapped id is
        refused, never rewritten — the documents already carrying the offline id
        were written against the first answer."""
        token = offline_id[len(reg) + 1:] if offline_id.startswith(f"{reg}-") else offline_id
        if not re.fullmatch(r"\d+", number):
            raise Fail(f"map_offline binds a NUMBER from `reserve` — {number!r} is not one")
        events, _ = self.events("reservations")
        issued = any(ev.get("op") == "reserve_offline" and ev.get("key") == reg
                     and ev.get("value") == token for ev in events)
        if not issued:
            raise Fail(f"{reg}-{token} was never issued by reserve_offline — nothing to map")
        for ev in events:
            if ev.get("op") == "map_offline" and ev.get("key") == reg \
                    and ev.get("value") == token:
                if ev.get("mapped") == number:
                    return
                raise Fail(
                    f"{reg}-{token} is already mapped to {reg}-{ev.get('mapped')} — the "
                    "mapping is append-only; reserve a new number instead of rebinding")
        self.adapter.log_append(self.log_id("reservations"),
                                fmt_line("map_offline", reg, self.rid,
                                         value=token, mapped=number))

    # -- journal / signals -------------------------------------------------

    def _publish(self, which: str, line: str) -> bool:
        """Write to the plane, and never let that failure destroy the caller's work.

        The plane carries visibility, not correctness. A rate limit or an outage must
        surface loudly and leave the run able to continue — swallowing it would hide a
        gap in the record, and raising would make a knowledge base an availability
        dependency of doing any work at all.
        """
        try:
            self.adapter.log_append(self.log_id(which), line)
            return True
        except Fail as exc:
            print(f"agent-sync: NOT published to the plane ({exc}). The work stands; "
                  "the record has a gap — re-run this step when the store is reachable.",
                  file=sys.stderr)
            return False

    def journal(self, text: str) -> bool:
        try:
            oid = self.adapter.tree_ensure(f"20 Runs — {self.rid}")
            self.adapter.log_append(oid, fmt_line(
                "journal", self.rid, self.rid, sha=head_sha(),
                note=text.replace("`", "'")[:400]))
            return True
        except Fail as exc:
            print(f"agent-sync: journal NOT published ({exc})", file=sys.stderr)
            return False

    def signal(self, dep: str, state: str) -> bool:
        allowed = {"filed", "accepted", "delivered", "closed", "refused"}
        if state not in allowed:
            raise Fail(f"state must be one of {sorted(allowed)}")
        return self._publish("signals", fmt_line(
            "signal", dep, self.rid, state=state, repo=repo_name(), sha=head_sha()))

    # -- awareness ---------------------------------------------------------

    @staticmethod
    def _fingerprint(ev: dict[str, str]) -> str:
        return "|".join((ev.get("ts", ""), ev.get("run", ""), ev.get("key", ""),
                         ev.get("op", ""), ev.get("state", "")))

    def _seen(self, which: str, events: list[dict[str, str]]) -> tuple[set[str], str]:
        """What this run has already been shown, by identity rather than by position.

        This was an INDEX into a list re-sorted on every read. An entry appended by
        another run with an earlier timestamp — clock skew, or a shard that was
        unreachable a moment ago — lands before the mark, shifts everything after it, and
        is never reported: the slice hands back an entry already seen instead. The one
        section of `status` whose job is to say "this changed while you were away" went
        quiet about precisely the change that arrived late.

        Returns the seen fingerprints and a floor timestamp. The floor bounds the file:
        older entries fell out of the kept window, and anything below it was necessarily
        shown in an earlier run.
        """
        p = self.root / STATE_DIR / "seen.json"
        try:
            entry = json.loads(p.read_text()).get(which)
        except (OSError, ValueError, AttributeError):
            return set(), ""
        if isinstance(entry, dict):
            return set(entry.get("fingerprints") or []), str(entry.get("floor") or "")
        if isinstance(entry, int):
            # The old index watermark meant "the first N were shown". Honour that reading
            # once, so upgrading does not re-announce a year of signals.
            return {self._fingerprint(e) for e in events[:entry]}, ""
        return set(), ""

    def _set_seen(self, which: str, events: list[dict[str, str]]) -> None:
        p = self.root / STATE_DIR / "seen.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = {}
        kept = events[-SEEN_CAP:]
        # The floor exists ONLY to cover entries that fell out of the kept window. Setting
        # it whenever anything is remembered would re-create the bug in a new shape: an
        # entry that arrives with an older timestamp is below the floor, and gets filtered
        # out as "necessarily seen" when nothing has ever shown it.
        data[which] = {"fingerprints": [self._fingerprint(e) for e in kept],
                       "floor": kept[0]["ts"] if len(events) > SEEN_CAP and kept else ""}
        p.write_text(json.dumps(data))

    def activity(self, limit: int = 6, mark_read: bool = True) -> dict[str, Any]:
        """What OTHER runs are doing, and what changed since this run last looked.

        Coordination is not only mutual exclusion. An agent that cannot see the
        others is merely blocked by them: it learns a task is taken and nothing
        about who has it, what they are touching, or what landed while it was away.
        """
        others = {k: v for k, v in self.all_holdings().items() if v["run"] != self.rid}

        signals, _ = self.events("signals")
        seen, floor = self._seen("signals", signals)
        fresh = [e for e in signals
                 if self._fingerprint(e) not in seen and e.get("ts", "") >= floor]
        if mark_read:
            self._set_seen("signals", signals)

        return {"others": others, "signals": signals[-limit:], "new_signals": fresh}

    # -- claim tags ---------------------------------------------------------

    def _claim_targets(self, key: str) -> list[tuple[Path, dict[str, Any]]]:
        out = []
        for pattern, spec in (self.cfg.get("claimTags") or {}).items():
            for path in sorted(glob_files(self.root, pattern)):
                out.append((path, spec))
        return out

    @staticmethod
    def _split_row(line: str) -> tuple[str, list[str], str] | None:
        """(prefix, cells, suffix) — everything needed to put the row back untouched.

        The row used to be rebuilt from its cells alone, so the indentation and the
        original line ending were dropped: a round-trip left a diff on a shared registry
        file that nobody made, on the one kind of file agents are told never to touch
        casually. `SKILL.md` promises `git diff` empty after acquire-then-release, and now
        the bytes outside the edited cell are carried through rather than reconstructed.
        """
        stripped = line.lstrip()
        if not stripped.startswith("|"):
            return None
        prefix = line[:len(line) - len(stripped)]
        core = stripped.rstrip()
        suffix = stripped[len(core):]
        if core.endswith("|"):
            core, suffix = core[:-1], "|" + suffix
        return prefix, core[1:].split("|"), suffix

    @staticmethod
    def _row_cells(line: str) -> list[str] | None:
        """Split a markdown table row, or None if this is not one."""
        split = Sync._split_row(line)
        return split[1] if split else None

    # -- branch discipline -------------------------------------------------

    @property
    def integration_branch(self) -> str:
        """Where work lands. Configured, or asked of the repository — never assumed."""
        return self.cfg.get("integrationBranch") or default_branch()

    @property
    def on_integration_branch(self) -> bool:
        return current_branch() == self.integration_branch

    def merge_log(self) -> tuple[Path, int]:
        spec = self.cfg.get("mergeLog") or {}
        return (self.root / (spec.get("file") or DEFAULT_MERGE_LOG),
                int(spec.get("retentionDays") or DEFAULT_MERGE_RETENTION))

    def merge_log_append(self, entry: dict[str, str] | None = None) -> Path:
        """Append one merge, then compact anything past the retention window.

        Two audiences, one file. An agent that just arrived needs the last few days in
        enough detail to know what changed under it; nobody needs that detail from three
        weeks ago, and a log that only grows stops being read — which is the same as not
        having one. Compaction happens on write, so it needs no cron and no second command.
        """
        path, retention = self.merge_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        detailed, compacted = self._read_merge_log(path)

        if entry is not None:
            detailed.insert(0, entry)
        cutoff = time.time() - retention * 86400
        keep, aged = [], []
        for e in detailed:
            (keep if parse_iso(e.get("ts", "")) >= cutoff else aged).append(e)
        compacted = [self._one_line(e) for e in aged] + compacted

        path.write_text(self._render_merge_log(keep, compacted, retention))
        return path

    @staticmethod
    def _one_line(e: dict[str, str]) -> str:
        return (f"- {e.get('ts', '')[:10]} · `{e.get('key', '—')}` · {e.get('branch', '?')} → "
                f"{e.get('target', '?')} · `{e.get('sha', '?')}` · {e.get('summary', '')}".rstrip())

    @staticmethod
    def _read_merge_log(path: Path) -> tuple[list[dict[str, str]], list[str]]:
        if not path.exists():
            return [], []
        text = path.read_text()
        body, _, tail = text.partition("\n## Compacted\n")
        compacted = [l for l in tail.splitlines() if l.startswith("- ")]
        entries: list[dict[str, str]] = []
        for block in body.split("\n### ")[1:]:
            lines = block.splitlines()
            head = lines[0]
            e = {"ts": "", "key": "—", "branch": "?", "target": "?", "sha": "?",
                 "run": "?", "files": "?", "conflicts": "none", "summary": ""}
            parts = [p.strip() for p in head.split("·")]
            if parts:
                e["ts"] = parts[0]
            if len(parts) > 1:
                e["key"] = parts[1].strip("`")
            if len(parts) > 2 and "→" in parts[2]:
                e["branch"], _, e["target"] = (x.strip() for x in parts[2].partition("→"))
            if len(parts) > 3:
                e["sha"] = parts[3].strip("`")
            for l in lines[1:]:
                for field in ("run", "files", "conflicts", "summary"):
                    if l.startswith(f"- {field}: "):
                        e[field] = l.split(": ", 1)[1]
            entries.append(e)
        return entries, compacted

    @staticmethod
    def _render_merge_log(entries: list[dict[str, str]], compacted: list[str],
                          retention: int) -> str:
        out = [MERGE_LOG_MARKER, "", "# Merge log", "",
               f"Written by `agent_sync.py merge`. Entries newer than {retention} days keep "
               "their detail; older ones are compacted to one line each on the next write. "
               "Read it before starting work: it is the shortest answer to *what landed while "
               "I was on my branch*.", ""]
        for e in entries:
            out += [f"### {e['ts']} · `{e['key']}` · {e['branch']} → {e['target']} · `{e['sha']}`",
                    f"- run: {e['run']}",
                    f"- files: {e['files']}",
                    f"- conflicts: {e['conflicts']}",
                    f"- summary: {e['summary']}", ""]
        # The placeholder deliberately does not start with "- ": the reader counts list
        # items, and an empty log that reports one compacted entry is a lie about history.
        out += ["## Compacted", ""] + (compacted or ["_nothing older than the window yet_"])
        return "\n".join(out) + "\n"

    def write_claim(self, key: str, holder: str | None) -> list[str]:
        """Write the claim through to git, or restore it. Surgical and reversible.

        One row, one cell, one substitution. Ambiguity is refused rather than guessed:
        this edits a shared registry, so a wrong line is exactly the collision the lease
        exists to prevent. The previous cell text is stored in the lock file, so release
        restores what was there rather than an assumed default.
        """
        # A claim is a statement about the integration branch, so it is only written
        # there. Committed on a feature branch it is invisible to everyone until the
        # merge — and it turns the shared roadmap into a file two branches both edit,
        # which is a merge conflict on the one file that exists to prevent collisions.
        # While the work is on a branch the holder lives in the coordination plane,
        # where `status` and the board already read it.
        if holder is not None and not self.on_integration_branch:
            return [f"claim for `{key}` left in the coordination plane — this run is on "
                    f"'{current_branch()}', not {self.integration_branch}. `status` shows the "
                    f"holder to every agent; `merge` records the outcome."]

        notes: list[str] = []
        for path, spec in self._claim_targets(key):
            if spec.get("mode") != "cell":
                continue
            idx = int(spec.get("cell", -1))
            lines = path.read_text().splitlines(keepends=True)
            hits = [i for i, l in enumerate(lines)
                    if re.search(rf"(?<![A-Za-z0-9-]){re.escape(key)}(?![A-Za-z0-9-])", l)
                    and self._row_cells(l) is not None]
            rel = path.relative_to(self.root)
            if not hits:
                continue
            # On RELEASE the id is the wrong key to search by. `acquire` wrote a marker naming
            # this run; by the time the work is done the id may appear in rows that did not exist
            # when it was taken — which is what happened on 2026-08-07, when a task id gained a
            # second mention mid-run and release refused, leaving `(claimed: r-…)` in a status cell
            # permanently: a live claim for a lease nobody holds, which is worse than no claim.
            # The marker is unambiguous however many rows mention the id, so narrow by it first.
            if holder is None and len(hits) > 1:
                marker = ((spec.get("held") or "{prev} (claimed: {holder})")
                          .replace("{prev}", "").replace("{holder}", self.rid).strip())
                if marker:
                    narrowed = [i for i in hits if marker in lines[i]]
                    if len(narrowed) == 1:
                        hits = narrowed

            # A board says which row is which in its FIRST cell. Every other match is a
            # row CITING this id — "closed by B-12", "blocked until T-1 ships" — and boards
            # cross-reference constantly: on the family's own board 14 rows cited others, so
            # 19 of 41 ids had two candidate rows and their claim was refused. The refusal
            # was right; granting the lease anyway was not, because the registry then carries
            # no claim while `acquire` reports `won`. Tried after the marker so a release
            # still trusts what it actually wrote.
            if len(hits) > 1:
                own = []
                for i in hits:
                    cells = self._row_cells(lines[i])
                    if cells and cells[0].strip() == key:
                        own.append(i)
                if len(own) == 1:
                    hits = own

            if len(hits) > 1:
                notes.append(f"{rel}: `{key}` appears in {len(hits)} table rows — refusing "
                             "to guess which one is the claim. Narrow the pattern or edit by hand")
                continue

            i = hits[0]
            split = self._split_row(lines[i])
            assert split is not None
            row_prefix, cells, row_suffix = split
            if idx < 0:
                idx = len(cells) + idx
            if not 0 <= idx < len(cells):
                notes.append(f"{rel}: cell {spec.get('cell')} is out of range for `{key}`'s row")
                continue

            # Kept beside the run state, not in the lock file: the git lease mode has no
            # lock file, and a release that cannot find what it replaced leaves the claim
            # written through forever — which is worse than never writing it.
            store = self.root / STATE_DIR / "claims.json"
            try:
                state = json.loads(store.read_text())
            except (json.JSONDecodeError, OSError):
                state = {}
            saved = (state.get(key) or {}).get(str(rel))

            current = cells[idx]
            if holder is not None:
                if saved is not None:
                    continue                      # already written through
                template = spec.get("held") or "{prev} (claimed: {holder})"
                new = template.replace("{prev}", current.strip()).replace("{holder}", holder)
                cells[idx] = f" {new.strip()} "
                state.setdefault(key, {})[str(rel)] = current
            else:
                if saved is None:
                    # NOT "nothing to undo" — that reading is ssheleg/agent-sync#5. The
                    # state file is this run's memory of what it overwrote, and a tag
                    # outlives it routinely: written by a run that died, by a session on
                    # another machine, or with `.agent-sync/` wiped between the acquire and
                    # the release. `release` then printed success, exited 0, and left
                    # `(claimed: r-…)` on the board with nothing behind it — a claim no
                    # command could reach.
                    #
                    # The TTL decides, exactly as it decides for a lock file: the lease
                    # plane is asked who holds the key, and only a tag with no live lease
                    # behind it is cleared. A live lease is somebody working, and its tag
                    # is left alone whoever runs this.
                    template = spec.get("held") or DEFAULT_CLAIM_TEMPLATE
                    rx = claim_marker_re(template)
                    m = rx.search(current) if rx else None
                    if m is None:
                        continue                  # no tag here either, so nothing to undo
                    whose = m.group("holder")
                    live = self._holder_of(key)
                    if live is not None and live != self.rid:
                        notes.append(
                            f"{rel}: `{key}` carries a claim tag naming {whose}, and the "
                            f"lease is live under {live} — left alone")
                        continue
                    stripped = (current[:m.start()] + current[m.end():]).strip()
                    cells[idx] = f" {stripped} " if stripped else " "
                    notes.append(
                        f"{rel}: cleared an orphaned claim tag on `{key}` — it named run "
                        f"{whose}, and no live lease stands behind it (ssheleg/agent-sync#5)")
                    lines[i] = row_prefix + "|" + "|".join(cells) + row_suffix
                    tmp = path.with_suffix(path.suffix + ".agent-sync.tmp")
                    tmp.write_text("".join(lines))
                    tmp.replace(path)
                    continue
                # Restoring VERBATIM loses any edit made while the claim was held, and in
                # this family the claim cell IS the status cell — so `close then release`
                # silently reopened a row closed with evidence minutes earlier (B-35,
                # 2026-08-14, caught by `finish` reporting the board uncommitted, not by
                # anybody reading it). The protocol's intent is to remove this run's
                # marker, not to rewind the cell, so when the text moved on, strip the
                # marker and keep the movement.
                template = spec.get("held") or "{prev} (claimed: {holder})"
                marker = template.replace("{prev}", "").replace("{holder}", self.rid).strip()
                expected = template.replace("{prev}", saved.strip()).replace("{holder}", self.rid)
                if current.strip() == expected.strip() or not marker:
                    cells[idx] = saved
                else:
                    stripped = current.replace(marker, "").rstrip()
                    cells[idx] = (stripped if stripped.strip() else saved)
                    notes.append(f"{rel}: `{key}`'s cell changed while the claim was held — "
                                 f"kept the change and removed only the claim marker")
                state.get(key, {}).pop(str(rel), None)
                if not state.get(key):
                    state.pop(key, None)

            lines[i] = row_prefix + "|" + "|".join(cells) + row_suffix
            tmp = path.with_suffix(path.suffix + ".agent-sync.tmp")
            tmp.write_text("".join(lines))
            tmp.replace(path)
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(json.dumps(state, indent=2))
            notes.append(f"{rel}: `{key}` claim "
                         + ("written through" if holder else "restored"))
        return notes

    # -- claim divergence ---------------------------------------------------------

    def claim_divergence(self) -> list[str]:
        """Where a held lease and the durable git claim tag disagree.

        DEC-0216 makes the git tag the durable record and the lease the live one, with
        the run writing the tag through. The tool verifies rather than edits: a process
        that rewrites a shared registry file on its own is the exact mechanism that
        clobbers another agent's work, and it would do it from a hook, unattended.
        So this reports, and the agent writes.

        Both directions, since AS-04. It used to return on `if not held` — so divergence
        was reported only to a run holding a lease, and the one shape that needs reporting
        most, a tag with NO lease behind it, was structurally invisible: nobody holds it,
        so nobody could be told (ssheleg/agent-sync#5). The lease side is still keyed by
        what this run holds; the registry side is now swept whether it holds anything or not.
        """
        out: list[str] = []
        tags = self.cfg.get("claimTags") or {}
        if not tags:
            return out
        held = set(self.held())
        for pattern, spec in tags.items():
            for path in sorted(self.root.glob(pattern)):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text()
                except OSError:
                    continue
                rel = path.relative_to(self.root)
                marker = (spec.get("held") or "").replace("{holder}", self.rid)
                for key in sorted(held):
                    if key not in text:
                        continue
                    line = next((l for l in text.splitlines() if key in l), "")
                    if marker and marker in line:
                        continue
                    if spec.get("open") and spec["open"] in line:
                        out.append(f"{rel}: `{key}` still reads `{spec['open']}` while "
                                   "this run holds the lease — write the claim through")
                    elif spec.get("open") and spec["open"] in text:
                        # The tag exists in the file but not on the id's line. The
                        # configured mapping cannot be verified for this key, and saying
                        # so beats passing silently — an unverifiable check that reports
                        # clean is indistinguishable from one that works.
                        out.append(f"{rel}: cannot verify the claim tag for `{key}` — "
                                   f"`{spec['open']}` appears in the file but not on that "
                                   "id's line. Fix claimTags, or write the tag by hand")

        # The other direction: a tag whose lease the TTL has already ended, or one naming a
        # run that is not the run holding the key. Reported to every session, not only to a
        # session that happens to hold a lease.
        for e in self.orphan_claims():
            remedy = ("`release` it" if e["state"] == ORPHAN
                      else "ask the holder, and do not edit the row")
            out.append(f"{e['file']}:{e['line']}: `{e['key']}` [{e['state']}] — "
                       f"{e['why']}. NEXT: {remedy}")
        return out

    # -- as-built record and reconciliation ---------------------------------

    def record(self, text: str, decision: str = "", files: str = "") -> bool:
        """Append what was ACTUALLY built. Not a plan, not an intention."""
        return self._publish("asbuilt", fmt_line(
            "asbuilt", decision or "-", self.rid, repo=repo_name(), sha=head_sha(),
            files=files.replace("`", "'")[:200],
            note=text.replace("`", "'")[:400]))


    def _allocated_ids(self, reg: str, spec: dict[str, Any]) -> set[str]:
        """Ids that actually exist, excluding the register's "next free" pointer.

        That line names the id nobody has taken yet. Scraping it as an allocated id
        makes every reconcile demand an as-built record for a decision that has not
        been written — and poisons the baseline with a number one higher than reality.
        """
        path = self.root / spec["file"]
        if not path.exists():
            return set()
        text = path.read_text()
        ids = set(re.findall(rf"\b{reg}-\d+\b", text))
        # Through the shim, not the raw key. Reading `nextFreeIdPattern` directly meant
        # a config written with the modern `pattern` key never discarded its own
        # next-free line, and the baseline was stamped one higher than reality —
        # exactly what the docstring above warns about. Reproduced 2026-08-25.
        pattern = id_pattern(spec)
        if pattern:
            m = re.search(pattern, text)
            if m:
                ids.discard(f"{reg}-{m.group(1)}")
        return ids

    def set_baseline(self) -> dict[str, int]:
        """Stamp today's highest id per register as the line before which nothing is
        expected to carry an as-built record. Idempotent-ish: re-stamping moves the
        line forward, which is why it prints what it did."""
        out = {}
        oid = self.log_id("asbuilt")
        for reg, spec in (self.cfg.get("idRegisters") or {}).items():
            path = self.root / spec["file"]
            if not path.exists():
                continue
            nums = [int(i.rsplit("-", 1)[1]) for i in self._allocated_ids(reg, spec)]
            top = max(nums) if nums else 0
            self.adapter.log_append(oid, fmt_line(
                "baseline", reg, self.rid, value=f"{top:04d}", repo=repo_name()))
            out[reg] = top
        return out

    def reconcile(self) -> list[dict[str, str]]:
        """Compare intent (git) against the as-built record (cloud).

        Only mechanical divergence is decided here. Whether a built thing actually
        matches what the document describes is a reading, not a diff — this reports
        where to look and refuses to pretend it judged the substance.
        """
        findings: list[dict[str, str]] = []
        notes_backlog: list[str] = []
        events, _ = self.events("asbuilt")

        # 1. Recorded as built, but the commit is not in this history: recorded from a
        #    branch that never landed, or from a different repository.
        for ev in events:
            sha = ev.get("sha", "")
            repo = ev.get("repo", "")
            if not sha or sha == "unknown" or repo != repo_name():
                continue
            # `git cat-file -e` prints nothing on success, so the exit code is the
            # only signal — a stdout-returning helper cannot answer this.
            if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True).returncode != 0:
                findings.append({
                    "kind": "as-built commit missing from git",
                    "detail": f"{sha} recorded by {ev['run']}: {ev.get('note', '')[:90]}",
                    "means": "recorded as built, but that commit is not in this history"})

        # 2. Intent with no as-built record — as a RATCHET, not a flood.
        #    A project adopting this on day one has every prior decision unrecorded, and
        #    a check that reports all of them reports nothing: it is noise, and noise is
        #    what gets a gate switched off. Ids at or below the baseline are counted as a
        #    backlog that may only shrink; ids after it fail.
        recorded_ids = {ev["key"] for ev in events if ev["key"] != "-"}
        baselines = {ev["key"]: int(ev.get("value") or 0)
                     for ev in events if ev["op"] == "baseline"}
        for reg, spec in (self.cfg.get("idRegisters") or {}).items():
            path = self.root / spec["file"]
            if not path.exists():
                continue
            ids = self._allocated_ids(reg, spec)
            base = baselines.get(reg)
            if base is None:
                findings.append({
                    "kind": f"{reg} has no as-built baseline",
                    "detail": f"{len(ids)} ids exist and none is evaluated",
                    "means": "run `reconcile --set-baseline` once, then only new ids are checked"})
                continue
            missing_new, backlog = [], 0
            for i in sorted(ids - recorded_ids):
                num = int(i.rsplit("-", 1)[1])
                if num > base:
                    missing_new.append(i)
                else:
                    backlog += 1
            if missing_new:
                findings.append({
                    "kind": f"{reg} written after the baseline with no as-built record",
                    "detail": ", ".join(missing_new[:8]) +
                              (f" (+{len(missing_new)-8} more)" if len(missing_new) > 8 else ""),
                    "means": "decided since adoption; nothing reports it was built"})
            if backlog:
                notes_backlog.append(f"{reg}: {backlog} pre-baseline ids unevaluated (backlog)")

        # 3. As-built citing an id that does not exist in git: built against something
        #    that was never recorded as a decision.
        # Only judge what this checkout can actually judge. The as-built log is shared by
        # every repository on the plane, while id registers are per-repository — a service
        # repo declares none, because decisions live in the umbrella. Comparing the shared
        # log against a local register reported every umbrella decision as an orphan when
        # run from a submodule: a false finding produced by scope, and the loudest possible
        # way to teach people to ignore the check.
        registers = self.cfg.get("idRegisters") or {}
        if registers:
            known: set[str] = set()
            for reg, spec in registers.items():
                known |= self._allocated_ids(reg, spec)
            prefixes = tuple(f"{reg}-" for reg in registers)
            orphan = sorted({ev["key"] for ev in events
                             if ev.get("repo") == repo_name()
                             and ev["key"].startswith(prefixes)
                             and ev["key"] not in known})
            if orphan:
                findings.append({
                    "kind": "as-built cites an unknown id",
                    "detail": ", ".join(orphan[:8]),
                    "means": "built against a decision that is not in the git register"})
        else:
            notes_backlog.append(
                "no id registers declared here, so register checks are not evaluated in "
                "this repository — run reconcile in the umbrella for those")

        # 4. A claim tag on the board whose lease the TTL has already ended. Mechanical,
        #    two-plane divergence — the definition of what this command reports — and it
        #    was the one kind `reconcile` never mentioned (ssheleg/agent-sync#5).
        for e in self.orphan_claims():
            findings.append({
                "kind": f"claim tag with no live lease ({e['state']})",
                "detail": f"{e['file']}:{e['line']} `{e['key']}` names {e['holder']}",
                "means": e["why"]})

        self.backlog = notes_backlog
        return findings

    # -- guard -------------------------------------------------------------

    RESOURCE_PREFIX = "res--"

    def resource_key(self, path: str) -> str:
        """Canonical repo identity + canonical path — the FILE's own key
        (FIX-SY-04.01). A task id names work; this names the thing two tasks
        would collide on, so two runs editing one register serialize on the
        register, not on whoever's task id sorts first."""
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(str(self.root)))
        # `[A-Za-z0-9_-]` only, which `lease_name` stores unchanged — so the key is
        # byte-identical to its ref name and its lock stem, and reads back from `held()`
        # as written. Kept exactly as 1.21.2 built it: a different key for the same file
        # would let two versions claim one register under two names.
        canon = re.sub(r"[^A-Za-z0-9_-]+", "-", rel).strip("-")
        repo = re.sub(r"[^A-Za-z0-9_-]+", "-", repo_name() or "repo")
        return f"{self.RESOURCE_PREFIX}{repo}--{canon}"[:120]

    def guard(self, path: str) -> tuple[bool, str]:
        # realpath on BOTH sides: canonical identity is the point (SY-04) — a
        # /var vs /private/var symlink split makes one file two names, and a
        # guard that sees two names guards neither.
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(str(self.root)))
        patterns = self.cfg.get("guardedFiles") or []
        if not any(matches_glob(rel, p) for p in patterns):
            return True, "not a guarded file"

        # A lease is required in every mode. What differs between backends is how
        # strongly it is arbitrated, and that is what `gated` reports — not whether
        # the check runs. A local lock file is genuine mutual exclusion between
        # agents on one machine; it is only across machines that fs cannot arbitrate.
        #
        # And a TASK lease is ownership of the task, never of the file
        # (FIX-SY-04.01): two runs holding two different task ids used to both
        # pass here and interleave writes to one shared registry. A guarded
        # file now also takes the file's OWN claim — resource identity =
        # canonical repo + canonical path — auto-claimed under the task lease,
        # so a single agent feels nothing while two agents on one file
        # serialize. Independent files carry independent keys and never
        # serialize without cause.
        held = self.held()
        task_keys = [k for k in held if not k.startswith(self.RESOURCE_PREFIX)]
        res = self.resource_key(path)
        if res in held:
            if not task_keys:
                return False, (f"{rel}: resource claim remains but no live task lease "
                               "is held — acquire the current task before resuming")
            self._guard_activity(res, task_keys)
            return True, f"resource claim held for {rel} ({res})"
        if task_keys:
            won, holder = self.acquire(res)
            note = "" if self.gated else " (advisory: arbitrated locally only)"
            if won:
                self._guard_activity(res, task_keys)
                return True, (f"held by this run ({', '.join(task_keys)}); resource "
                              f"claim taken for {rel}{note}")
            return False, (f"{rel}: another run ({holder}) holds this FILE's "
                           f"resource claim ({res}) — a task lease authorizes the "
                           f"task, not the file. Wait for the claim to release or "
                           f"expire, then retry.")

        # Name the OTHER key, never just the other run. "r-x holds a lease right now"
        # beside a path reads as "r-x holds this file" — which is not what was checked,
        # and an agent that repeats it puts a fact in the transcript with no source.
        other = self._any_other_holder()
        who = (f" Another run ({other[0]}) holds {other[1]} — a different task, not this file."
               if other else "")
        return False, (f"{rel} is a guarded registry file and this run holds no lease.{who} "
                       f"Acquire one first: agent_sync.py acquire <TASK-ID>")

    def long_held_notice(self) -> str:
        """R19's warning where a working session sees it — at most once per lease per run.

        It printed only from `status`, which runs at SessionStart, when a fresh session holds
        nothing: the one moment it can never fire (seam S9). The guard runs on every write,
        so it says it here, once, and leaves a marker so the next write stays quiet."""
        try:
            interval = int(self.cfg.get("scheduledIntervalSeconds") or 0)
        except (TypeError, ValueError):
            return ""
        if interval <= 0:
            return ""
        lines = []
        for key, age in long_held(self.held_ages(), interval):
            if key.startswith(self.RESOURCE_PREFIX):
                continue
            mark = self.root / STATE_DIR / "warned" / f"{self.rid}--{lease_name(key)}"
            if mark.exists():
                continue
            try:
                mark.parent.mkdir(parents=True, exist_ok=True)
                mark.write_text(now_iso())
            except OSError:
                continue
            lines.append(f"{key} held for {since(int(age))}")
        if not lines:
            return ""
        return (f"agent-sync: {'; '.join(lines)} — longer than this repository's scheduled "
                f"interval ({since(interval)}). Scheduled jobs stand aside while it is held; "
                "release it between batches.")

    def _any_other_holder(self) -> tuple[str, str] | None:
        """(run, key) of some lease another run holds — both halves, or neither."""
        for key, holding in sorted(self.all_holdings().items()):
            if holding.get("run") and holding["run"] != self.rid:
                return str(holding["run"]), key
        return None

    # -- board -------------------------------------------------------------

    def all_holdings(self) -> dict[str, dict[str, Any]]:
        """Every key currently held, with who holds it and in which repository.

        The repository matters: work spans several repos that are entered from one
        umbrella, so "r-alpha holds ASC-072" is only actionable once you know which
        checkout r-alpha is in."""
        now = time.time()
        if self.adapter.is_lease_authority:
            events, _ = self.events("claims")
            out: dict[str, dict[str, Any]] = {}
            for key in sorted({e["key"] for e in events}):
                holding = resolve_holding(events, key, now)
                if holding:
                    out[key] = holding
            return out
        out = {}
        d = self.root / STATE_DIR / "leases"
        for p in sorted(d.glob("*.lock") if d.exists() else []):
            try:
                held = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if now <= parse_iso(held.get("ts", "")) + int(held.get("ttl", self.ttl)):
                out[self.lock_key(p, held)] = {"run": str(held.get("run")), "repo": repo_name(),
                               "ts": parse_iso(held.get("ts", ""))}
        return out

    def all_holders(self) -> dict[str, str]:
        return {k: str(v["run"]) for k, v in self.all_holdings().items()}

    def board(self) -> str:
        """The cross-repository view — identical whoever generates it.

        Four repositories share one plane and every one of them may regenerate this
        page, so its content must not depend on who did. It once did: the header named
        the generating repo and the id-leak section read that repo's registers, so a
        submodule agent's run replaced the umbrella's board with a narrower one. Repo-
        local findings now live on their own page (`12 Repo — <name>`); only facts that
        are true from every checkout belong here.
        """
        events, bad = self.events("claims")
        total = max(len(events) + bad, 1)
        rows = [f"| `{k}` | {h['run']} | {h.get('repo') or '—'} | held |"
                for k, h in self.all_holdings().items()]

        lines = [
            f"{GENERATED_MARKER} source={repo_name()}@{head_sha()} at={now_iso()} "
            "— edit in git, not here -->",
            "",
            "# Board — the coordination plane",
            "",
            "Every repository on this plane writes and reads this page. It carries only "
            "facts that are true from any of them.",
            "",
            f"- record plane: `{self.adapter.name}` · lease: `{self.lease_mode}` — "
            f"**{lease_guarantee(self.lease_mode)[0]}**",
            f"- runs are recorded as **{'gated' if self.gated else 'ungated'}**",
            f"- unparseable log lines: {bad}/{total}"
            f"{'  ⚠ over 2% — the log cannot be replayed reliably' if bad / total > 0.02 else ''}",
            "",
            "## Live leases",
            "",
            "| Key | Holder | Repo | State |",
            "|---|---|---|---|",
        ]
        lines += rows or ["| — | — | — | none held |"]

        sig, _ = self.events("signals")
        if sig:
            lines += ["", "## Recent cross-repo signals", "",
                      "| Dependency | State | By | Repo |", "|---|---|---|---|"]
            for ev in sig[-10:]:
                lines.append(f"| `{ev['key']}` | {ev.get('state','?')} | {ev['run']} "
                             f"| {ev.get('repo','—')} |")
        return "\n".join(lines) + "\n"

    def config_digest(self) -> str:
        """Identity of the configuration this snapshot describes.

        Stamping the commit instead made the very first adoption look stale: the config
        is added in the same commit as the snapshot, so a commit-range diff always found
        a change. A content hash has no such boundary.
        """
        raw = (self.root / CONFIG_PATH).read_bytes()
        return hashlib.sha256(raw).hexdigest()[:12]

    def _snapshot_credentials(self, env_path: Path | None) -> list[str]:
        """The wiring snapshot's credential line(s). For notion the token's primary home is
        the Observatory vault since 1.22.0, and the snapshot said `.env.agent-sync` for every
        backend — a committed description that contradicted the tool (fix round 2, item 6)."""
        where = (f"`{env_path.name}`"
                 f"{' in ' + str(env_path.parent.name) if env_path and env_path.parent != self.root else ''}"
                 if env_path else "`(none found)`")
        backend = os.environ.get("AGENT_SYNC_BACKEND") or self.cfg.get("backend") or "fs"
        if backend == "notion":
            return [f"- record-plane token: the Observatory vault slot "
                    f"`{vault_project(self.root)}/{VAULT_SECRET}` first — every command that "
                    "writes to Notion re-runs itself under `use_secret.py run --vault-only`; "
                    f"{where} only as a DEGRADED fallback (`status` names which one served). "
                    "Never sourced, never committed",
                    f"- other settings (parent page, collection) read from {where} — "
                    "gitignored, never committed"]
        return [f"- credentials read from {where} — gitignored, never committed"]

    def setup_snapshot(self) -> str:
        """A snapshot of how THIS project is actually wired, generated from the config.

        Written into the repository so every agent — and every human — reads the same
        description of the documentation pipeline before touching it, instead of
        inferring it from behaviour. Generated, never hand-written: a hand-written
        description of a configuration drifts from it, which is the failure this whole
        tool exists to surface.
        """
        cfg = self.cfg
        regs = cfg.get("idRegisters") or {}
        guarded = cfg.get("guardedFiles") or []
        gates = cfg.get("gates") or []
        mirror = cfg.get("mirror") or {}
        env_path = find_env_file(self.root)
        L = [
            f"{GENERATED_MARKER} source={repo_name()}@{head_sha()} "
            f"cfg={self.config_digest()} at={now_iso()} "
            "— regenerate with `agent_sync.py setup`, do not hand-edit -->",
            "",
            f"# How documentation and coordination work in {repo_name()}",
            "",
            "This file is **generated** from the live configuration. If it disagrees with",
            "what the tool does, the tool is right and this file is stale — regenerate it.",
            "",
            "## Two documentation sources",
            "",
            "| Source | Answers | Where |",
            "|---|---|---|",
            "| Git documents | *how it should be* — intent, decisions, contracts | this repository |",
            "| As-built record | *how it actually is* — what agents wrote, with commits | the coordination plane |",
            "",
            "Neither outranks the other; they answer different questions. **The gap between",
            "them is the finding.** Reconcile before starting a task and after finishing it.",
            "",
            "## This project's wiring",
            "",
            f"- record plane: **{self.adapter.name}** · lease: **{self.lease_mode}** — "
            f"{lease_guarantee(self.lease_mode)[0]} · runs recorded "
            f"**{'gated' if self.gated else 'ungated'}**",
            f"- lease TTL {cfg.get('leaseTtlSeconds', DEFAULT_TTL)}s, renewed every "
            f"{cfg.get('renewIntervalSeconds', DEFAULT_RENEW)}s",
            *self._snapshot_credentials(env_path),
            "",
            "### Id registers — reserve before you write",
            "",
        ]
        if regs:
            L += ["| Register | File | Reserve with |", "|---|---|---|"]
            L += [f"| `{r}` | `{s['file']}` | `agent_sync.py reserve {r}` |" for r, s in sorted(regs.items())]
            L += ["", "Reading a *next free id* line is **not** reserving it — two agents read the same number."]
            authority, how = id_allocator(cfg, self.adapter)
            L += ["", f"Ids are allocated by {how}." if authority else
                  f"**No allocator serves these registers** — {how}, so `reserve` refuses "
                  "and `check` reports it."]
        else:
            L += ["None declared here. Ids live in the parent repository; reserve them there."]

        L += ["", "### Guarded files — a live lease is required to write these", ""]
        L += ([f"- `{g}`" for g in guarded] or ["- none"])
        L += ["", "### Gates run before a change is considered done", ""]
        L += ([f"- `{g}`" for g in gates] or ["- none configured"])

        L += ["", "### Mirrored into the plane (read-only rendering of git)", ""]
        L += ([f"- `{s}`" for s in (mirror.get("sources") or [])]
              if mirror.get("enabled") else ["- disabled"])

        L += [
            "",
            "## What is written where, and what is never deleted",
            "",
            "| Information | Home | Lifetime |",
            "|---|---|---|",
            "| Decisions, specs, contracts, user-facing behaviour | git | permanent, append-only register |",
            "| What was actually built, with its commit | as-built log | permanent, append-only |",
            "| Cross-repo dependency state | signal log | permanent, append-only |",
            "| Who holds a task right now | claims log | expires by TTL |",
            "| A lock left by a run that stopped | the lease directory | until it is "
            "reported and reaped |",
            "| Per-run narrative | that run's journal | permanent |",
            "| The board and these pages | generated | replaced on every regeneration |",
            "",
            "**Nothing in a log is edited or deleted.** A mistake is corrected by appending",
            "the correcting entry, because the logs are replayed in order and a deletion",
            "would silently rewrite a decision every other agent already read. A lease is",
            "released, never removed. A reserved id that is not used is returned with",
            "`release-id`, which appends — it does not erase.",
            "",
            "Generated pages are the exception: they are rewritten wholesale, and a page",
            "whose first line lost its generated marker is **refused**, not overwritten.",
            "",
            "## The cycle, per task",
            "",
            "```",
            "merges      → what landed while you were away",
            "status      → who else is working, and what changed since you last looked",
            "reconcile   → resolve every divergence BEFORE writing code",
            "branch      → work happens on one; the integration branch is somebody",
            "              else's stable base",
            "acquire ID  → take the lease. On the integration branch the claim tag is",
            "              written through to git; on any other branch the holder stays",
            "              in the coordination plane, where `status` shows it to everyone",
            "   … work …",
            "record      → what you ACTUALLY built, with the decision id and files",
            "   … update the git documents in the same change …",
            "reconcile   → check both sides again",
            "board       → regenerate the shared view",
            "merge --key → land the branch: conflicts checked first, the merge recorded,",
            "              that lease released. Without a branch, `release ID` by hand",
            "              — on every path, including failure",
            "residue     → what this run leaves on disk. Expiry ends a lease and leaves",
            "              the file, so `status` and `finish` enumerate them; `reap`",
            "              clears only what THIS run can prove it owns and has spent,",
            "              and reports foreign or ambiguously owned locks untouched",
            "```",
            "",
            f"This project's integration branch is `{self.integration_branch}`.",
            "",
            "Full doctrine ships with the skill: `references/two-sources.md`,",
            "`references/lease-protocol.md`, `references/branching.md`,",
            "`references/roadmap.md`, `references/pipeline-binding.md`.",
        ]
        return "\n".join(L) + "\n"

    def mirror(self, limit: int = 120) -> list[str]:
        """Render the configured git documents into the plane, one-way and stamped.

        A rendering, never a source: each page carries the commit it was made from, and
        the drift gate compares that stamp with HEAD. It is not a place to edit — a page
        whose generated marker is gone is refused rather than overwritten.
        """
        cfg = self.cfg.get("mirror") or {}
        if not cfg.get("enabled"):
            return ["mirror: disabled in this project's config"]

        files: list[Path] = []
        for source in cfg.get("sources") or []:
            q = self.root / source
            if q.is_file():
                files.append(q)
            elif q.is_dir():
                files += sorted(f for f in q.rglob("*.md") if f.is_file())
        files = sorted(set(files))

        out: list[str] = []
        truncated = 0
        if len(files) > limit:
            truncated = len(files) - limit
            files = files[:limit]

        sha = head_sha()
        for f in files:
            rel = f.relative_to(self.root)
            title = f"90 Mirror — {rel}"
            body = (f"{GENERATED_MARKER} source={repo_name()}:{rel}@{sha} at={now_iso()} "
                    "— a rendering of git, edit the source there -->\n\n" + f.read_text())
            out.append(self.put_generated(title, body))

        # A silent cap reads as "everything is mirrored" when it is not.
        if truncated:
            out.append(f"NOTE: {truncated} further file(s) not mirrored — raise the limit "
                       "or narrow mirror.sources; they are absent, not up to date")
        return out

    def mirror_drift(self) -> list[str]:
        """Mirror pages whose stamped commit is not this repository's HEAD.

        The docstring beside `mirror` claimed this gate existed before any code did —
        prose asserting a check that was never written, which is worse than silence
        because a reader stops looking.
        """
        cfg = self.cfg.get("mirror") or {}
        if not cfg.get("enabled"):
            return []
        sha = head_sha()
        out: list[str] = []
        for oid in self.adapter.log_shards("90 Mirror — "):
            text = self.adapter.doc_get(oid)
            m = re.search(r"source=[^@]+@(\S+)", text.splitlines()[0] if text else "")
            if m and m.group(1) != sha:
                out.append(f"mirror page stamped {m.group(1)}, HEAD is {sha} — "
                           "regenerate with `board --mirror`")
        return out

    def setup_path(self) -> Path:
        configured = self.cfg.get("setupFile")
        if configured:
            return self.root / configured
        return self.root / ("docs/AGENT_SYNC.md" if (self.root / "docs").is_dir()
                            else "AGENT_SYNC.md")

    def repo_page(self) -> str:
        """Findings only this checkout can produce — registers it owns, its own history."""
        res_events, _ = self.events("reservations")
        lines = [
            f"{GENERATED_MARKER} source={repo_name()}@{head_sha()} at={now_iso()} "
            "— edit in git, not here -->",
            "",
            f"# {repo_name()}",
            "",
            f"- registers declared here: "
            f"{', '.join(sorted(self.cfg.get('idRegisters') or {})) or 'none — they live in the parent repository'}",
            f"- guarded files: {len(self.cfg.get('guardedFiles') or [])}",
        ]
        leaks = self._leaks(res_events)
        lines += ["", "## Reserved ids not found in git", ""]
        lines += ([f"- `{self.fmt_id(r, v)}` reserved by {run}" for r, v, run in leaks]
                  or ["- none"])

        findings = self.reconcile()
        lines += ["", "## Intent vs as-built", ""]
        lines += ([f"- **{f['kind']}** — {f['detail']}" for f in findings] or
                  ["- no mechanical divergence found"])
        for n in getattr(self, "backlog", []):
            lines.append(f"- {n}")
        return "\n".join(lines) + "\n"

    def _register_text(self, reg: str) -> str:
        spec = (self.cfg.get("idRegisters") or {}).get(reg) or {}
        path = self.root / spec["file"] if spec.get("file") else None
        return path.read_text() if path and path.exists() else ""

    def fmt_id(self, reg: str, value: int) -> str:
        """`CO-051` in a three-digit register, `B-0051` in a four-digit one (AS-08)."""
        return format_id(reg, value, id_width(self._register_text(reg), reg))

    def _leaks(self, events: list[dict[str, str]]) -> list[tuple[str, int, str]]:
        out = []
        for reg, spec in (self.cfg.get("idRegisters") or {}).items():
            _b, _f, assignments = resolve_reservations(events, reg)
            path = self.root / spec["file"]
            text = path.read_text() if path.exists() else ""
            for run, value in assignments:
                if not id_present(text, reg, value):
                    out.append((reg, value, run))
        return out

    def put_generated(self, path: str, text: str) -> str:
        oid = self.adapter.tree_ensure(path)
        current = self.adapter.doc_get(oid)
        if current.strip() and not current.lstrip().startswith(GENERATED_MARKER):
            return (f"REFUSED: '{path}' was not written by agent-sync "
                    "(no generated marker on line 1). Someone took it over; "
                    "reporting instead of overwriting.")
        self.adapter.doc_put(oid, text)
        return f"wrote '{path}'"


# --------------------------------------------------------------------------- init

ENV_TEMPLATE = """# agent-sync — identity lives here, shape lives in .claude/agent-sync.json
# This file is gitignored on purpose. Never commit it, never paste its contents.
AGENT_SYNC_BACKEND={backend}
{extra}"""


def cmd_init(args: argparse.Namespace) -> int:
    root = project_root()
    os.chdir(root)
    cfg_path = root / CONFIG_PATH
    backend = args.backend

    if backend == "outline" and not args.url:
        raise Fail("--url is required for the outline backend "
                   "(the instance URL, e.g. https://wiki.example.com)")

    if cfg_path.exists() and not args.force:
        print(f"• {CONFIG_PATH} already exists — left untouched (use --force to replace)")
    else:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(default_config(backend), indent=2) + "\n")
        print(f"✓ wrote {CONFIG_PATH}")

    extra = ""
    if backend == "outline":
        extra = (f"AGENT_SYNC_OUTLINE_URL={args.url}\n"
                 "AGENT_SYNC_OUTLINE_TOKEN=\n"
                 "AGENT_SYNC_OUTLINE_COLLECTION=\n")
    elif backend == "notion":
        extra = ("AGENT_SYNC_NOTION_TOKEN=\n"
                 "AGENT_SYNC_NOTION_PARENT=\n"
                 "AGENT_SYNC_NOTION_COLLECTION=\n")
    env_path = root / ENV_FILE
    if env_path.exists() and not args.force:
        print(f"• {ENV_FILE} already exists — left untouched")
    else:
        env_path.write_text(ENV_TEMPLATE.format(backend=backend, extra=extra))
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        print(f"✓ wrote {ENV_FILE} (mode 600)")

    ensure_gitignored(root, str(ENV_FILE))
    ensure_gitignored(root, f"{STATE_DIR}/")
    ensure_untracked(root, f"{STATE_DIR}/")

    print()
    if backend == "notion":
        print("NEXT — three things only you can do:")
        print("  1. Create a personal access token: Notion's developer portal →")
        print("     Personal access tokens → New token. It begins `ntn_`.")
        print("     Reference: https://developers.notion.com/docs/create-a-notion-integration")
        print("  2. Put the token in the Observatory vault — its primary home since 1.22.0.")
        print("     Write ONLY the token (one line, no `NAME=`) into a mode-600 file outside")
        print("     the repository, then:")
        print(f"       python3 \"$(project-observatory full-path)/tools/vault.py\" put "
              f"{vault_project(root)} local {VAULT_SECRET} < that-file")
        print("     and delete that file. Every command that touches Notion re-runs itself")
        print("     under `use_secret.py run --vault-only`, so the value never reaches argv.")
        print(f"     No Observatory on this machine: paste it into {ENV_FILE} instead —")
        print("     `status` reports that as degraded. Do not `source` the file either way.")
        print(f"  3. Put the page the container should live under in {ENV_FILE}:")
        print("       AGENT_SYNC_NOTION_PARENT=<the 32-character id ending that page's URL>")
        print()
        print("  Then run `bootstrap` — it creates the container page and prints the id")
        print("  to paste into AGENT_SYNC_NOTION_COLLECTION. Share that page in Notion")
        print("  with the other people working this repository; each of them uses their")
        print("  OWN token, and yours stays yours.")
        print()
        print("  The lease is decided by `leaseBackend`, not by Notion — Notion has no")
        print("  compare-and-swap. See references/backend-notion.md.")
    elif backend == "outline":
        print("NEXT — two things only you can do:")
        print(f"  1. Create an API token in your Outline instance at {args.url}")
        print("     (Settings → API and access), then put it in this line of "
              f"{ENV_FILE}:")
        print("       AGENT_SYNC_OUTLINE_TOKEN=<paste it here>")
        print(f"  2. Nothing to load: every command and hook reads {ENV_FILE} itself.")
        print("     Do not `source` it into your shell — that puts the token in every")
        print("     process the shell starts.")
        print()
        print("  Then run `status` again — it will create the cloud layout and print "
              "the collection id to paste into AGENT_SYNC_OUTLINE_COLLECTION.")
        print()
        print("  The token is yours alone: do not paste it into a chat, a commit, "
              "or a command line.")
    else:
        print("Backend 'fs' needs no credentials. It is the record plane only, and a")
        print("local one: agents on another machine see none of this project's leases,")
        print("signals or board. The lease itself is decided by `leaseBackend` —")
        print(f"default `local`, which is {lease_guarantee('local')[0]}.")
        print("See references/backend-fs.md.")
    return 0


def default_config(backend: str) -> dict[str, Any]:
    return {
        "backend": backend,
        "leaseTtlSeconds": DEFAULT_TTL,
        "renewIntervalSeconds": DEFAULT_RENEW,
        "gated": True,
        "idRegisters": {},
        "guardedFiles": [],
        "claimTags": {},
        "gates": [],
        "mirror": {"enabled": False, "sources": []},
        "mergeLog": {"file": DEFAULT_MERGE_LOG, "retentionDays": DEFAULT_MERGE_RETENTION},
    }


def ensure_gitignored(root: Path, entry: str) -> None:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if any(line.strip() == entry for line in lines):
        print(f"• .gitignore already ignores {entry}")
        return
    header = "# agent-sync"
    with open(gi, "a") as fh:
        if lines and lines[-1].strip():
            fh.write("\n")
        if header not in lines:
            fh.write(f"{header}\n")
        fh.write(f"{entry}\n")
    print(f"✓ added {entry} to .gitignore")


def ensure_untracked(root: Path, entry: str) -> None:
    """Ignoring a path does nothing once git is already tracking it.

    Found in a real project: the state directory had been committed before the ignore rule
    existed, so `git status` reported it modified after **every** tool call — the repository was
    permanently dirty and no run could report itself finished. `run-id` is the worse half: it is
    this checkout's agent identity, and committed it reaches every clone, so two machines
    coordinate as one run.
    """
    tracked = git("ls-files", "--", entry.rstrip("/"), cwd=root)
    if not tracked:
        return
    if git("rm", "-r", "--cached", "-q", "--", entry.rstrip("/"), cwd=root) is None:
        return
    n = len(tracked.split("\n"))
    print(f"✓ untracked {n} committed file(s) under {entry} — commit that removal; "
          "ignoring a tracked path has no effect")


# --------------------------------------------------------------------------- status

def cmd_status(_args: argparse.Namespace) -> int:
    root = project_root()
    os.chdir(root)
    load_env_file(root)
    print(f"agent-sync {VERSION} — {repo_name()}@{head_sha()}")

    if not (root / CONFIG_PATH).exists():
        print("\n✗ not initialised.")
        print("\nNEXT: run init. It asks nothing it can guess and writes nothing secret.")
        print("  agent_sync.py init --backend outline --url <instance-url>")
        print("  agent_sync.py init --backend fs        # local, degraded, no credentials")
        return 1

    s = Sync()
    ad = s.adapter
    headline, detail = lease_guarantee(s.lease_mode)
    print(f"  record plane   : {ad.name}"
          f"{'' if ad.is_lease_authority else ' — local only, not shared between machines'}")
    print(f"  lease          : {s.lease_mode} — {headline}")
    if VAULT_DEGRADED:
        print(f"  token          : degraded: {VAULT_DEGRADED}")
    if TOKEN_SOURCE == "file":
        print(f"  token          : degraded: plaintext token file {ENV_FILE} — move it to the "
              f"Observatory vault:\n    python3 \"$(project-observatory full-path)/tools/vault.py\" "
              f"put {vault_project(s.root)} local {VAULT_SECRET} < <a mode-600 file holding only "
              f"the token>\n    then delete the {VAULT_SECRET} line from {ENV_FILE}")
    elif TOKEN_SOURCE in ("vault", "env"):
        print(f"  token          : from the {'Observatory vault' if TOKEN_SOURCE == 'vault' else 'environment'}")
    print(f"  runs recorded  : {'gated' if s.gated else 'UNGATED'}")
    print(f"  run id         : {s.rid}")

    if not s.gated:
        print("\n⚠ Nothing here is enforced. Do not describe this project as protected.")
        print(f"  {detail}")
    elif not s.lease_is_cross_machine:
        print(f"  ({detail})")

    if ad.COLLECTION_ENV and not getattr(ad, "collection", ""):
        print(f"\n✗ {ad.COLLECTION_ENV} is empty.")
        print("\nNEXT: create the container, then paste the id into "
              f"{ENV_FILE}:")
        print("  agent_sync.py bootstrap")
        return 1

    try:
        s.migrate_legacy_notes()
        held = s.held()
    except Fail as exc:
        print(f"\n✗ {exc}")
        return 1
    print(f"  leases held    : {', '.join(held) if held else 'none'}")
    try:
        interval = int(s.cfg.get("scheduledIntervalSeconds") or 0)
    except (TypeError, ValueError):
        interval = 0
    for key, age in long_held(s.held_ages(), interval):
        print(f"  ⚠ held too long: {key} for {since(int(age))} — longer than this repository's "
              f"scheduled interval ({since(interval)}).\n    Scheduled jobs stand aside while "
              "it is held; release it between batches.")

    # A run produces more than a diff, and what it leaves behind has to be reported by the
    # command every session runs. Until this line existed, `status` printed `leases held:
    # none` beside three expired locks in the directory it had just read — every reader of
    # lease state here applies the TTL, so "expired" and "absent" were one answer.
    stale = s.stale()
    reapable = [e for e in stale if e["state"] == REAPABLE]
    left_alone = [e for e in stale if e["state"] != REAPABLE]
    if not stale:
        print("  expired locks  : none")
    else:
        print(f"  expired locks  : {len(stale)} — {len(reapable)} this run's to clear, "
              f"{len(left_alone)} left alone")
        gone = long_expired(stale, LONG_EXPIRED_DAYS * 86400)
        if gone:
            # The lock directory only: `status` runs at every SessionStart and does not pay a
            # remote round-trip; `residue` and `reap --long-expired` sweep the git plane too.
            print(f"  long expired   : {len(gone)} in the lock directory expired "
                  f"{LONG_EXPIRED_DAYS}+ days ago — the operator reviews them with: "
                  "agent_sync.py reap --long-expired"
                  + (" (which also sweeps the remote)" if s.lease_mode == "git" else ""))
        print("\n  Expired leases still on disk. Nobody holds these: the TTL ended the "
              "lease\n  and left the file.")
        # Age splits the list, because age is what decides whether a line is news.
        # A lease that expired an hour ago may still be a run in trouble; one that
        # expired thirteen days ago is residue nobody will ever clear, and printing
        # it in full at every SessionStart is how an operator learns to skim the
        # whole block — including the recent lines that mattered. Measured on the
        # machine that filed this: sixteen entries, none newer than two days, the
        # oldest thirteen days old, printed identically every session for a fortnight.
        fresh = [e for e in stale
                 if e.get("expired_for") is not None and e["expired_for"] < STALE_DETAIL_SECONDS]
        old_ones = [e for e in stale if e not in fresh]
        for e in fresh[:6]:
            print(f"    · {e['key']}  [{e['state']}] {e['why']} ({spent(e)})")
        if len(fresh) > 6:
            print(f"    · … and {len(fresh) - 6} more expired in the last "
                  f"{since(STALE_DETAIL_SECONDS)}")
        if old_ones:
            # One line, and it still names them: a summary that hides WHICH keys
            # cannot be acted on, and the point of collapsing is to keep the block
            # readable rather than to stop reporting.
            keys = ", ".join(e["key"] for e in old_ones[:8])
            more = f", +{len(old_ones) - 8} more" if len(old_ones) > 8 else ""
            print(f"    · {len(old_ones)} expired over {since(STALE_DETAIL_SECONDS)} ago "
                  f"— {keys}{more}")
        if reapable:
            print("    Clear what this run owns:  agent_sync.py reap")
        if left_alone:
            print("    The rest are foreign or ambiguously owned — reported, not touched.")

    # The registry plane's residue, on the same footing as the lease plane's. A claim tag
    # is a claim about a lease, and until this line the TTL could end the lease while the
    # tag stayed on the board reading live — with `leases held: none` printed above it.
    try:
        orphans = s.orphan_claims()
    except Fail:
        orphans = []
    if not (s.cfg.get("claimTags") or {}):
        pass
    elif not orphans:
        print("  orphan claims  : none")
    else:
        print(f"  orphan claims  : {len(orphans)} claim tag(s) with no live lease behind "
              "them")
        for e in orphans[:6]:
            print(f"    · {e['file']}:{e['line']}  {e['key']}  [{e['state']}] {e['why']}")
        if len(orphans) > 6:
            print(f"    · … and {len(orphans) - 6} more")
        print("    Clear one:  agent_sync.py release <KEY>")

    # Who else is in here, and what landed while this run was away. Without this a
    # lease only tells an agent it is blocked, never who by or on what.
    plane_broken = False

    # The two logs this command reports on, checked before it reports on them. With a
    # local lease the claims log is a record rather than the source of holdings, so
    # nothing on the awareness path would have touched it — and `status` would print a
    # confident "other runs: none" over a log that cannot be replayed at all.
    for which in ("claims", "signals"):
        try:
            s.events(which)
        except Fail as exc:
            print(f"\n✗ {exc}")
            plane_broken = True

    try:
        act = s.activity()
    except Fail as exc:
        # Not a warning to read past: with the plane unreadable this run cannot see who
        # else is working, which is the half of coordination that is not the lease.
        print(f"\n✗ could not read the coordination plane: {exc}")
        act = {"others": {}, "signals": [], "new_signals": []}
        plane_broken = True

    if act["others"]:
        print("\n  Other runs working this project right now:")
        for key, h in sorted(act["others"].items()):
            where = h.get("repo") or "unknown repo"
            mine = " ← this repo" if where == repo_name() else ""
            print(f"    · {h['run']} holds {key}  in {where}{mine}")
        print("    Do not take these on. If one looks abandoned, its lease expires on its own.")
    else:
        print("  other runs     : none holding anything")

    if act["new_signals"]:
        print(f"\n  New since you last looked ({len(act['new_signals'])}):")
        for ev in act["new_signals"][-6:]:
            print(f"    · {ev['key']} → {ev.get('state', '?')} "
                  f"(by {ev['run']}, {ev.get('repo', 'unknown repo')})")
        print("    A dependency that moved may unblock — or invalidate — what you were about to do.")
    elif act["signals"]:
        print(f"  signals        : {len(act['signals'])} recent, nothing new since you last looked")

    claim_issues = s.claim_divergence()
    if claim_issues:
        print("\n  Claim tags out of step with the lease plane:")
        for c in claim_issues:
            print(f"    ! {c}")

    try:
        drift = s.mirror_drift()
    except Fail:
        drift = []
    if drift:
        print(f"\n  Mirror drift ({len(drift)} page(s)): regenerate with `board --mirror`")

    if plane_broken:
        print("\nNEXT: repair the coordination plane — until it reads, this run is working")
        print("  blind to every other one.")
        return 1

    # The same verdict `check` gives, from the command every session actually runs. Two
    # commands answering one question differently is how a broken setup stays invisible:
    # `status` used to print "NEXT: acquire a lease" on a project `check` called unhealthy.
    #
    # Reported BEFORE the task-pipeline gate, and the order is the point: this is a fact
    # about the project, that is a fact about the machine. Behind the gate, a defect in
    # the project stayed invisible on every machine without the dependency installed —
    # which is every CI runner, and is how this ordering was found.
    try:
        _ok, _warn, setup_problems = check_setup(root)
    except Fail as exc:
        setup_problems = [str(exc)]
    if setup_problems:
        print(f"\n✗ `check` reports {len(setup_problems)} problem(s) with this setup:")
        for problem in setup_problems[:4]:
            print(f"    · {problem}")
        if len(setup_problems) > 4:
            print(f"    · … and {len(setup_problems) - 4} more")
        print("\nNEXT: fix the setup before coordinating on it —")
        print("  agent_sync.py check")
        return 1

    if not pipeline_installed(cfg=s.cfg):
        print("\n✗ task-pipeline is not installed. agent-sync binds to its stages and")
        print("  will not improvise a substitute flow.")
        print("\nNEXT:\n  npx sshlg-skills install")
        return 1

    print("\nNEXT: acquire a lease before you touch a guarded file —")
    print("  agent_sync.py acquire <TASK-ID>")
    return 0


# Every host layout task-pipeline can be installed under — NOT just Claude's.
# A detector that proves absence from ONE host's layout is wrong on a machine
# whose task-pipeline lives in another host's cache (FIX-SY-08.01). The plugin
# CACHE glob and the plain-skills path are listed per host; the shared hub is
# host-agnostic. New host? add its two lines here, not a branch elsewhere.
PIPELINE_HOST_LAYOUTS = (
    (".claude/plugins/cache/task-pipeline/**/skills/task-pipeline/SKILL.md", None),
    (".codex/plugins/cache/task-pipeline/**/skills/task-pipeline/SKILL.md", None),
    (".gemini/plugins/cache/task-pipeline/**/skills/task-pipeline/SKILL.md", None),
    (None, ".claude/skills/task-pipeline/SKILL.md"),
    (None, ".codex/skills/task-pipeline/SKILL.md"),
    (None, ".gemini/skills/task-pipeline/SKILL.md"),
    (None, ".agents/skills/task-pipeline/SKILL.md"),   # the shared hub, host-agnostic
)


def pipeline_installed(home: "Path | None" = None, cfg: "dict | None" = None) -> bool:
    """Whether task-pipeline is reachable to THIS machine, across every host
    layout — or at an explicit path the operator configured (FIX-SY-08.01).

    `home` is injectable so a test can point at a synthetic HOME with no side
    effects. An explicit `pipelinePath` in the config wins over discovery: a
    machine that resolved the skill some other way says so, and the detector
    does not overrule a stated fact with a filesystem guess.
    """
    home = home or Path.home()
    explicit = (cfg or {}).get("pipelinePath")
    if explicit:
        return (Path(explicit) if os.path.isabs(explicit) else home / explicit).exists()
    for glob_pat, direct in PIPELINE_HOST_LAYOUTS:
        if glob_pat and list(home.glob(glob_pat)):
            return True
        if direct and (home / direct).exists():
            return True
    return False


def cmd_bootstrap(_args: argparse.Namespace) -> int:
    root = project_root()
    load_env_file(root)
    cfg_path = root / CONFIG_PATH
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    backend = os.environ.get("AGENT_SYNC_BACKEND") or cfg.get("backend") or "fs"
    cls = CLOUD_ADAPTERS.get(backend)
    if cls is None:
        raise Fail(f"backend '{backend}' has no container to create — `bootstrap` is "
                   f"for {', '.join(sorted(CLOUD_ADAPTERS))}")
    ad = cls()
    if not ad.configured():
        raise Fail(f"set {' and '.join(cls.REQUIRED_ENV)} first")
    if getattr(ad, "collection", ""):
        print(f"container already set: {ad.collection}")
        return 0
    name = f"agent-sync — {repo_name()}"
    oid = ad.create_container(name)
    print(f"✓ created container '{name}'")
    print(f"\nNEXT: put this in {ENV_FILE}:")
    print(f"  {cls.COLLECTION_ENV}={oid}")
    return 0


# --------------------------------------------------------------------------- cli

def cmd_acquire(args: argparse.Namespace) -> int:
    # B-150: where hooks run with a session id, a lease won under the SHARED fallback is
    # one the guard hook never recognises — "won" here, "this run holds no lease" on the
    # next Edit. Refuse at the source and say how to share the hook's identity.
    key, _how = _session_key()
    if not key and _sessions_stamped():
        print("refused: this shell has no session identity, so the lease would be won by the "
              "shared fallback while the guard hook checks its own session and refuses every "
              "edit. Re-run with CLAUDE_SESSION_ID=<this session's id> (or AGENT_SYNC_RUN_ID=<id> "
              "for a non-Claude caller).", file=sys.stderr)
        return 2
    s = Sync()
    won, holder = s.acquire(args.key)
    if won:
        register_session_repo(s.root)
        headline, detail = lease_guarantee(s.lease_mode)
        print(f"won {args.key} (run {s.rid}, ttl {s.ttl}s)")
        print(f"  {headline} — {detail}")
        if not s.gated:
            print("⚠ this lease is advisory, not enforced")
        print("Remember: release it on every path, including failure.")
        return 0
    print(f"lost {args.key} — held by {holder or 'another run'}")
    return 1


def cmd_renew(args: argparse.Namespace) -> int:
    if getattr(args, "session_repos", False):
        own = _session_prelude()
        key, children = _start_session_children("renew", own,
                                                active_only=getattr(args, "active_only", False))
        own_holds = None
        try:
            s = Sync()
            s.renew(args.key, active_only=getattr(args, "active_only", False))
            own_holds = bool(s.held())
        except Fail:
            own_holds = False   # the session's own project may not be coordinated
        except Exception as exc:                          # noqa: BLE001
            print(f"note: {own}: {type(exc).__name__}: {exc}", file=sys.stderr)
        _finish_session_children("renew", key, children, own, own_holds)
        return 0
    s = Sync()
    s.renew(args.key, active_only=getattr(args, "active_only", False))
    if getattr(args, "report_held", False):
        print(f"{HELD_MARK} {len(s.held())}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    if getattr(args, "held", False):
        if args.key:
            raise Fail("release takes a key OR --held, not both")
        if getattr(args, "session_repos", False):
            own = _session_prelude()
            key, children = _start_session_children("release", own)
            rc, own_holds = 0, None
            held_after: dict[str, int] = {}
            try:
                rc = _release_held(held_after=held_after)
                own_holds = held_after.get("n", 1) > 0
            except Fail:
                own_holds = False   # the session's own project is not coordinated
            except Exception as exc:                      # noqa: BLE001
                print(f"note: {own}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return _finish_session_children("release", key, children, own, own_holds) or rc
        return _release_held(report=getattr(args, "report_held", False))
    if not args.key:
        raise Fail("release needs a key, or --held for everything this run holds")
    # Exit non-zero when nothing was released. A caller that scripts `release` in a
    # cleanup path has no other way to learn the lease is still out there. The reason —
    # another run's lease, a delete the remote refused, a remote nobody reached — is
    # printed by `release` itself; this line only refuses to say "released" over it.
    s = Sync()
    s.migrate_legacy_notes()
    if not s.release(args.key):
        print(f"NOT released: {args.key}", file=sys.stderr)
        return 1
    print(f"released {', '.join(s.released_as.get(args.key) or [args.key])}")
    return 0


def session_repos_dir() -> Path:
    """`~/.agent-sync/session-repos`, resolved at each call from the CURRENT `HOME`.

    It was a module constant read at import, so a test that pointed `HOME` at a temporary
    directory after importing the module — or an in-process caller that changed it — still
    wrote into the real home: 14 files in the operator's `~/.agent-sync` came from one test
    run (seam S1, 2026-10-12)."""
    return Path.home() / ".agent-sync" / "session-repos"


# The hook's whole budget, from this process's start: session-end.sh gives `release` 2 s
# for EVERY repository, renew.sh gives `renew` 10 s. The children share it.
SESSION_REPOS_BUDGET = {"release": 1.6, "renew": 9.0}
HELD_MARK = "agent-sync-held:"
# The environment this process STARTED with, before `load_env_file` filled it from one
# repository's `.env.agent-sync`. A child acting in another repository starts from this,
# never from the filled one: `load_env_file` only sets absent keys, so a second repository
# acted on in-process inherited the first one's backend, URL, collection and token.
_STARTED_ENV = dict(os.environ)


def _hook_started_at(environ: dict[str, str], now_mono: float, now_wall: float) -> float:
    """The monotonic instant the HOOK's process started — across a vault re-exec. Pure.

    The budget was measured from this module's import, and `execv` under the vault imports
    it again: the re-run process started its 1.6 s afresh after the probe had already spent
    part of the 2 s the host gives. The first process records its wall-clock start in
    AGENT_SYNC_HOOK_T0 just before the exec; the re-run counts from there."""
    t0 = environ.get(HOOK_T0_ENV)
    if t0 and environ.get(VAULT_REEXEC):
        try:
            return now_mono - min(max(0.0, now_wall - float(t0)), 3600.0)
        except ValueError:
            pass
    return now_mono


_STARTED_AT = _hook_started_at(os.environ, time.monotonic(), time.time())


def session_registry_key() -> str:
    """Whose list of repositories this is — the SESSION, never the project directory.

    AS-07 keyed the list by `CLAUDE_PROJECT_DIR`, which a Bash tool call does not carry: the
    `acquire` that should have registered a second repository found no project and wrote
    nothing. And two sessions in one project shared one list, so the first SessionEnd
    deleted it while the other still held a lease there. The key is `_session_key()`'s —
    `env:<AGENT_SYNC_RUN_ID>` or `session:<id>` — which `hooks/_lib.sh` computes the same
    way from the environment or the hook payload. Empty when no identity is established:
    the shared fallback is nobody's list.
    """
    key, _how = _session_key()
    return key


def session_repos_file(key: str) -> Path:
    """`<sha256 of the key's bytes>.json` — ASCII whatever the key, any length, and the same
    digest `printf '%s' "$key" | sha256sum` gives in bash under any locale (raw bytes on
    both sides: `os.fsencode` undoes the decoding Python applied to the environment)."""
    return session_repos_dir() / (hashlib.sha256(os.fsencode(key)).hexdigest() + ".json")


def session_repos(key: str) -> list[str]:
    try:
        data = json.loads(session_repos_file(key).read_text())
        return [r for r in data if isinstance(r, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _update_session_repos(key: str, add: list[str] = (), drop: list[str] = ()) -> None:
    """Add and drop under one lock, re-reading the list inside it — an `acquire` registering
    a repository while a renew's children are still out must not be overwritten by the
    renew's stale copy. An empty list removes the file, so the bash check stays a file test."""
    f = session_repos_file(key)
    try:
        # Inside the try: a host without fcntl (Windows Python) raised ImportError past the
        # OSError handler below and crashed `acquire` AFTER it had won the lease (seam S4).
        # Without the lock the list is still written; two writers racing is the lesser harm.
        try:
            import fcntl
        except ImportError:
            fcntl = None
        d = session_repos_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / ".lock", "a") as lock:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            repos = [r for r in session_repos(key) if r not in set(drop)]
            repos = sorted(set(repos) | set(add))
            if repos:
                tmp = f.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(repos))
                os.replace(tmp, f)
            elif f.exists():
                f.unlink()
    except OSError:
        pass


def register_session_repo(root: Path) -> None:
    """After a lease is won in `root`: list it under this session, so the session's hooks
    renew and release it wherever the session itself is rooted (AS-07)."""
    key = session_registry_key()
    if key:
        try:
            _update_session_repos(key, add=[str(root.resolve())])
        except Exception as exc:                          # noqa: BLE001 - best effort, never fail a won acquire
            print(f"note: this session's list of repositories was not updated "
                  f"({type(exc).__name__}) — renew/release this lease here by hand",
                  file=sys.stderr)


def _child_env(key: str) -> dict[str, str]:
    env = dict(_STARTED_ENV)
    if env.pop(VAULT_REEXEC, None) is not None:
        env.pop(VAULT_SECRET, None)      # the vault put THIS repository's token here
    env.pop(HOOK_T0_ENV, None)
    if key.startswith("session:") and not any(
            env.get(v) for v in ("AGENT_SYNC_RUN_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")):
        # Found by walking to a session stamp in THIS checkout; the child's checkout has none.
        env["CLAUDE_SESSION_ID"] = key[len("session:"):]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _start_session_children(verb: str, own: Path | None, *, active_only: bool = False) -> tuple[str, list]:
    """One child process per OTHER listed repository, started in parallel (AS-07).

    Each runs in its own repository with the environment this process started with, so it
    reads that repository's `.env.agent-sync` — and its own vault slot — and nothing of
    this one's. Returns (registry key, [(repo, process or None)]).
    """
    key = session_registry_key()
    if not key:
        return key, []
    argv = ["renew", "--report-held"] if verb == "renew" else ["release", "--held", "--report-held"]
    if verb == "renew" and active_only:
        argv.append("--active-only")
    env = _child_env(key)
    out: list = []
    for repo in session_repos(key):
        try:
            r = Path(repo)
            if own is not None and r.resolve() == own:
                continue                                  # acted on in this process
            if not r.is_dir() or not (r / CONFIG_PATH).is_file():
                print(f"note: {repo}: no longer a coordinated repository — dropped from this "
                      "session's list", file=sys.stderr)
                out.append((repo, None))
                continue
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__)] + argv, cwd=repo,
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            out.append((repo, p))
        except Exception as exc:                          # noqa: BLE001 - one repo never strands the rest
            print(f"note: {repo}: could not start ({type(exc).__name__}: {exc}) — kept listed",
                  file=sys.stderr)
            out.append((repo, False))
    return key, out


def _finish_session_children(verb: str, key: str, children: list, own: Path | None,
                             own_holds: bool | None) -> int:
    """Wait for the children inside the hook's budget, relay what each said, and keep a
    repository listed while this session may still hold anything there."""
    if not key:
        return 0
    deadline = _STARTED_AT + SESSION_REPOS_BUDGET[verb]
    rc, drop = 0, []
    for repo, p in children:
        if p is None:
            drop.append(repo)
            continue
        if p is False:
            continue
        try:
            out, err = p.communicate(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            print(f"note: {repo}: not finished inside the hook's {SESSION_REPOS_BUDGET[verb]:g}s "
                  "budget — kept listed", file=sys.stderr)
            rc = rc or 1
            continue
        except Exception as exc:                          # noqa: BLE001
            print(f"note: {repo}: {type(exc).__name__}: {exc} — kept listed", file=sys.stderr)
            continue
        held_after = None
        for line in (out or "").splitlines():
            if line.startswith(HELD_MARK):
                try:
                    held_after = int(line[len(HELD_MARK):].strip())
                except ValueError:
                    held_after = None
            else:
                print(f"{repo}: {line}")
        for line in (err or "").splitlines():
            print(f"{repo}: {line}", file=sys.stderr)
        if p.returncode not in (0, None):
            rc = rc or p.returncode
        if held_after == 0:
            drop.append(repo)
    if own is not None and own_holds is False:
        drop.append(str(own))
    if drop:
        _update_session_repos(key, drop=drop)
    return rc


def _session_prelude() -> Path | None:
    """The session's own repository, with its env loaded BEFORE any child starts: if this
    process is going to re-run itself under the vault, it must do so with no child out."""
    root = project_root()
    try:
        load_env_file(root)
    except Exception:                                     # noqa: BLE001 - never block the hook
        pass
    try:
        return root.resolve()
    except OSError:
        return None


def _bash_writes():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_sync_bash_writes", Path(__file__).resolve().parent / "bash_writes.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def bash_write_targets(cmd: str, cwd: str = ".") -> list[str]:
    """See `bash_writes.py` (AS-09) — kept here so `write-targets` and tests share one parser."""
    return _bash_writes().bash_write_targets(cmd, cwd)


def cmd_write_targets(_args: argparse.Namespace) -> int:
    """Print, one per line, the files a Bash tool call would write (payload on stdin)."""
    return _bash_writes().main()


def _release_held(report: bool = False, held_after: dict[str, int] | None = None) -> int:
    """Everything this run holds, in ONE process — the SessionEnd path.

    The hook used to spend `whoami` plus one `release` process per key, each paying the
    interpreter start and the config read again; under the 3 s both hosts give a SessionEnd
    handler, the tail of the list stayed out until its TTL. `held()` is re-read after every
    release because releasing a run's last task key releases its resource claims with it.
    """
    s = Sync()
    # 1.21.2 named its notes by a slug; resolve them against the remote first, so the
    # keys released below are the keys the refs were taken as (#25).
    s.migrate_legacy_notes()
    released, refused = [], []
    for _ in range(len(s.held()) + 1):
        pending = [k for k in s.held() if k not in refused]
        if not pending:
            break
        key = pending[0]
        try:
            ok = s.release(key)
        except Fail as exc:
            # One key's failure must not strand the rest: this is the SessionEnd path.
            print(f"note: {key}: {exc}", file=sys.stderr)
            ok = False
        if ok:
            released.extend(s.released_as.get(key) or [key])
        else:
            refused.append(key)
    remaining = len(s.held())
    if held_after is not None:
        held_after["n"] = remaining
    if report:
        print(f"{HELD_MARK} {remaining}")
    if not released and not refused:
        print("released nothing — this run holds nothing")
        return 0
    if released:
        print(f"released {', '.join(released)}")
    if refused:
        print(f"NOT released: {', '.join(refused)}", file=sys.stderr)
        return 1
    return 0


def cmd_reserve(args: argparse.Namespace) -> int:
    s = Sync()
    if getattr(args, "offline", False):
        print(s.reserve_offline(args.register))
        return 0
    value = s.reserve(args.register, rkey=getattr(args, "rkey", None))
    print(s.fmt_id(args.register, value))
    return 0


def cmd_map_offline(args: argparse.Namespace) -> int:
    Sync().map_offline(args.register, args.offline_id, args.number)
    print(f"mapped {args.offline_id} -> {args.register}-{args.number}")
    return 0


def cmd_release_id(args: argparse.Namespace) -> int:
    Sync().release_id(args.register, args.value)
    print(f"released {args.register}-{args.value}")
    return 0


def cmd_residue(_args: argparse.Namespace) -> int:
    """What this run leaves behind, classified — and never quietly cleared.

    Reporting and clearing are two commands on purpose. This one is safe to run anywhere,
    including in somebody else's checkout, because it cannot remove anything.
    """
    s = Sync()
    entries = s.residue()
    print(f"run {s.rid} · identity from {s.identity[1]}")
    print(f"lease mode {s.lease_mode} · ttl {s.ttl}s · {len(entries)} lock file(s) in "
          f"{STATE_DIR}/leases\n")
    if not entries:
        print("  nothing in the lock directory — no lease has been taken in this checkout, "
              "or every\n  one was released")
    for e in entries:
        print(f"  {e['key']}")
        print(f"    state : {e['state']}"
              + ("" if e["state"] == LIVE else f" ({spent(e)})"))
        print(f"    run   : {e['run'] or '—'} · repo {e['repo'] or '—'}"
              f"{' · host ' + e['host'] if e['host'] else ''}")
        print(f"    why   : {e['why']}")
    reapable = [e for e in entries if e["state"] == REAPABLE]
    other = [e for e in entries if e["state"] in (FOREIGN, AMBIGUOUS)]
    if entries:
        print()
        if reapable:
            print(f"  {len(reapable)} reapable — this run's own, spent: agent_sync.py reap")
        if other:
            print(f"  {len(other)} foreign or ambiguous — reported, never cleared from here. "
                  "An expired\n  lock in another run's name is that run's to explain, and a "
                  "lock whose owner\n  cannot be established is nobody's to delete.")
        if not reapable and not other:
            print("  no residue — every lock on disk is a live lease")

    # The second plane. A claim tag is residue too, and reporting only the lock directory
    # was the whole of ssheleg/agent-sync#5: `residue` printed "nothing on disk" over a
    # board row reading `(claimed: r-…)` that no command could reach.
    if s.cfg.get("claimTags"):
        orphans = s.orphan_claims()
        tags = len(s.claim_tags_on_disk())
        print(f"\n  claim tags: {tags} on disk, {len(orphans)} with no live lease behind them")
        for e in orphans:
            print(f"    · {e['file']}:{e['line']}  {e['key']}  [{e['state']}]")
            print(f"      why   : {e['why']}")
            if e["state"] == ORPHAN:
                print(f"      clear : agent_sync.py release {e['key']}")
            else:
                print("      leave : the lease is live under another run — ask the holder")
        if tags and not orphans:
            print("    every tag names the run that still holds its key")
    else:
        print("\n  claim tags: not configured here, so none are swept "
              "(`claimTags` in the config)")

    # AS-01a. The sweep, and the disclosure is now about what it COULD NOT reach rather
    # than about a plane nobody read. A check that cannot look must not read as one that
    # looked — and a check that CAN look must not keep printing that it cannot.
    if s.lease_mode == "git":
        remote = s.cfg.get("leaseRemote") or "origin"
        refs, why = s.git_residue()
        print(f"\n  git plane · {remote} · refs/agent-sync/leases/*")
        if why is not None:
            print(f"    ⚠ COULD NOT LOOK — {why}")
            print("    So this is not an empty sweep: the refs may be there and unread. "
                  "Fix the remote, or")
            print(f"    enumerate by hand: git ls-remote {remote} "
                  "'refs/agent-sync/leases/*'")
        elif not refs:
            print("    no lease refs on the remote — swept and empty, not unread")
        else:
            local_keys = {e["key"] for e in entries}
            reapable = [e for e in refs if e["state"] == REAPABLE]
            for e in refs:
                also = " · also noted locally" if e["key"] in local_keys else ""
                print(f"    · {e['key']}  [{e['state']}]{also}")
                print(f"      ref   : {e['ref']} @ {e['sha'][:10]}")
                if e.get("run"):
                    print(f"      run   : {e['run']}"
                          + (f" · host {e['host']}" if e.get("host") else ""))
                # `spent()` says this once for both planes: a second phrasing here is a
                # second definition of what residue means.
                print(f"      state : {spent(e)}")
                if e.get("why"):
                    print(f"      why   : {e['why']}")
                if e["state"] == REAPABLE:
                    print(f"      clear : agent_sync.py reap {e['key']}")
            print(f"    {len(refs)} ref(s) on the remote, {len(reapable)} this run can prove "
                  "it owns and has spent")
            print_keyless_warning(refs, indent="    ")
    return 0


def payload_is_keyless(raw: str | None) -> bool:
    """True only for a payload that parsed as a JSON OBJECT carrying no `key` — the shape
    1.21.2 and older wrote. An unreadable or non-object payload is not evidence of an old
    writer, and was flagged as one (fix round 2, item e). Pure."""
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    key = parsed.get("key")
    return not (isinstance(key, str) and key)


def print_keyless_warning(refs: list[dict[str, Any]], indent: str = "  ") -> None:
    """One line when a lease ref carries no `key` — AS-14's revisit condition, made a check.

    1.21.3 writes the key into every ref's payload; a ref without one was pushed by 1.21.2
    or older, so a writer that old is still running somewhere — the precondition for the
    one race #25 left open (two runs pushing one PATH key under its two names). Measured
    2026-10-12 on the Fabric remote: `docs-architecture-report-workspace.md`, written
    2026-10-08 by r-d8d027823, beside `docs%2Farchitecture%2Freport-workspace.md`.
    """
    keyless = [e for e in refs if e.get("keyless")]
    if keyless:
        names = ", ".join(e["ref"][len(LEASE_REF_PREFIX):] for e in keyless[:4])
        more = f", +{len(keyless) - 4} more" if len(keyless) > 4 else ""
        stamps = ", ".join(str(e.get("ts") or "?") for e in keyless[:4])
        print(f"{indent}⚠ {len(keyless)} lease ref(s) carry no `key` ({names}{more}) — each was "
              f"written by agent-sync ≤1.21.2 (ts {stamps}).\n"
              f"{indent}  That dates the write; it does not prove a ≤1.21.2 writer is still "
              "running. A session started\n"
              f"{indent}  before the upgrade keeps running the old plugin cache "
              "(CLAUDE_PLUGIN_ROOT pins it):\n"
              f"{indent}  restart sessions that predate the upgrade (AS-14).")


def cmd_reap(args: argparse.Namespace) -> int:
    """Clear this run's spent locks, and verify the teardown by reading the state again."""
    s = Sync()
    days = getattr(args, "older_than_days", None)
    if not getattr(args, "long_expired", False):
        if days is not None or getattr(args, "apply", False):
            print("  --older-than-days and --apply belong to `reap --long-expired`; without it "
                  "they would be ignored, so nothing was done.", file=sys.stderr)
            return 2
    else:
        # `is None`, not `or`: 0 used to become 7 silently — an operator asking for "all of
        # it" was handed the default and told nothing.
        return _reap_long_expired(s, LONG_EXPIRED_DAYS if days is None else days,
                                  bool(getattr(args, "apply", False)))
    if getattr(args, "own", False):
        return _reap_by_operator_decision(s, args.keys)
    result = s.reap(args.keys or None)
    for e in result["reaped"]:
        print(f"  reaped {e['key']} — {e['why']}, confirmed gone by re-reading "
              f"{STATE_DIR}/leases")
    for e in result["remaining"]:
        detail = f" ({e['error']})" if e.get("error") else ""
        print(f"  ✗ {e['key']} is STILL PRESENT after the delete{detail} — the teardown was "
              "not\n    verified, whatever the call returned. Nothing was reported as "
              "cleared.", file=sys.stderr)
    for e in result["refused"]:
        print(f"  · {e['key']} [{e['state']}] left alone — {e['why']}")
    if not args.keys:
        for e in result["left"]:
            print(f"  · {e['key']} [{e['state']}] left alone — {e['why']}")
    # AS-01a. The git plane is a second place residue lives, and until this it was a
    # second place nothing could clear: `reap` walked the lock directory only, so an
    # expired ref this run had won stayed on the remote with no command able to reach it.
    git_bad = 0
    if s.lease_mode == "git":
        refs, why = s.git_residue()
        remote = s.cfg.get("leaseRemote") or "origin"
        if why is not None:
            print(f"  ⚠ the git plane could not be read ({why}) — its refs are "
                  "neither reaped nor reported clean.", file=sys.stderr)
            print(f"    Enumerate by hand: git ls-remote {remote} "
                  "'refs/agent-sync/leases/*'", file=sys.stderr)
            git_bad = 1
        else:
            named = set(args.keys or [])
            want = [e for e in refs if e["state"] == REAPABLE and (not named or e["key"] in named)]
            for e in s.git_reap(want):
                if e["gone"]:
                    print(f"  reaped {e['key']} on {remote} — {e['why']}, confirmed gone by "
                          "re-reading the remote")
                else:
                    print(f"  ✗ {e['key']} is STILL on {remote} after the delete "
                          f"({e['why_gone']}) — the compare-and-swap refused, which means "
                          "somebody won it between the read and the delete",
                          file=sys.stderr)
                    git_bad = 1
            for e in refs:
                if e["state"] != REAPABLE and (not named or e["key"] in named):
                    print(f"  · {e['key']} [{e['state']}] on {remote} left alone — {e['why']}")
            if not refs:
                print(f"  no lease refs on {remote} — swept and empty, not unread")
    if not result["reaped"] and not result["remaining"] and not result["refused"] \
            and s.lease_mode != "git":
        print("  nothing this run can prove it owns and has spent — nothing reaped")
    return 1 if (result["remaining"] or result["refused"] or git_bad) else 0


def _reap_long_expired(s: "Sync", days: int, apply: bool) -> int:
    """`reap --long-expired` — AS-11: a sweep by AGE ALONE, and therefore an operator's call.

    This is the one path that removes state by time rather than by proof of ownership, which
    `references/lease-protocol.md` otherwise forbids a sweep to do — the dated section there
    ("A sweep by age", 2026-10-12) is the amendment and its limits. Selects every lock, on
    both planes, expired at least `days` days ago (`long_expired()`), and prints each with
    the classifier's own verdict, so the operator sees what a run could NOT prove before
    deciding. With `--apply` it clears exactly those entries through
    `_reap_by_operator_decision` — the same refusals (live, unreadable), the same printed
    payload, the same journal line — and nothing younger on the other plane. Without
    `--apply` it only lists: the default is the read. Never automatic, never an agent's.
    """
    if days < 1:
        print(f"  --older-than-days {days} refused: it must be at least 1. A sweep by age "
              "that reaches a lease\n  expired minutes ago is not residue clearing; for one "
              "key, `reap --i-own-this KEY`.", file=sys.stderr)
        return 2
    entries = list(s.residue())
    for e in entries:
        e.setdefault("plane", "fs")
    git_note = ""
    if s.lease_mode == "git":
        refs, why = s.git_residue()
        if why is not None:
            git_note = f"the git plane could not be read ({why}) — only the lock directory was swept"
            print(f"  ⚠ {git_note}.", file=sys.stderr)
        entries += refs
    gone = long_expired(entries, days * 86400)
    if not gone:
        print(f"  nothing expired {days} day(s) ago or more — nothing to sweep"
              + (f" ({git_note})" if git_note else ""))
        return 0
    keys = sorted({e["key"] for e in gone})
    print(f"  Expired {days} day(s) ago or more — the classifier's verdict on each, unchanged:")
    for e in gone:
        print(f"  · {e['key']} [{e.get('plane', 'fs')}] verdict {e['state']} · run "
              f"{e.get('run') or '—'}{' · host ' + e['host'] if e.get('host') else ''} · {spent(e)}")
        print(f"      why: {e.get('why') or '—'}")
        if e.get("keyless"):
            print(f"      ⚠ no `key` in the payload: was written by agent-sync ≤1.21.2 "
                  f"(ts {e.get('ts') or '?'}) — not proof of a live writer (AS-14)")
    if not apply:
        print(f"\n  {len(keys)} key(s). Nothing was removed. Clearing by age is the OPERATOR's "
              "decision, never an\n  agent's: an expired lease does not prove its writer "
              "stopped. If you are the operator:\n    agent_sync.py reap --long-expired"
              + (f" --older-than-days {days}" if days != LONG_EXPIRED_DAYS else "") + " --apply")
        return 0
    return _reap_by_operator_decision(s, keys, why=f"long expired (>= {days}d)",
                                      min_expired_s=days * 86400)


def _reap_by_operator_decision(s: "Sync", keys: list[str], why: str = "--i-own-this",
                               min_expired_s: int | None = None) -> int:
    """`--i-own-this` — the one path that clears state no RUN can prove is its own.

    AS-01b. The classifier is right to refuse: a run whose identity is the shared fallback
    cannot prove a matching run id means anything, so every such lock is `ambiguous` and
    nothing reaps it. The consequence is that expired locks accumulate with no path out for
    anybody — 28 of them on this machine by 2026-08-20, the oldest overdue by 21 days
    against a 2700-second TTL.

    M-50 forbids a RUN from deleting what it cannot prove. A person deciding, per key, is
    not a run guessing — so the decision is available, and three things make it a decision
    rather than a sweep:

    * **it takes named keys and refuses to run without them.** A blanket override is the
      thing the classifier exists to prevent, wearing a flag;
    * **it refuses a LIVE lease.** Residue is what this clears; a live lease belongs to a
      run that may still be working, and taking it by hand is the collision the tool exists
      to prevent;
    * **it prints the payload it destroyed** — run, timestamp, machine — so the decision is
      auditable afterwards by somebody who was not there, and journals it where a record
      plane is configured.

    **Both planes, because AS-01b came back on the other one.** Until v1.20.2 this walked
    the lock DIRECTORY only. In git mode the authority is `refs/agent-sync/leases/*` on the
    remote, a ref outlives the checkout that wrote it, and `reap` clears a git ref only when
    the classifier calls it `reapable` — this run's own. So an expired ref in a DEAD run's
    name had no path out from anywhere: `residue` listed it, `reap` left it alone, and
    `--i-own-this` answered *there is no lock by that name in this checkout*, which is true
    and useless. Measured on this machine 2026-09-14: **161 refs from two runs that ended on
    2026-09-09 and 2026-09-10**, every one expired more than four days against a 2700-second
    TTL, with no command able to reach them. That is the identical shape AS-01b closed for
    the local plane — "expired locks accumulate with no path out for anybody" — and the
    remedy is the identical one, applied where the state actually lives.
    """
    if not keys:
        print("reap --i-own-this needs the keys, one or more, by name.\n"
              "  A blanket override is the sweep the classifier exists to refuse, wearing a "
              "flag.\n"
              "  `agent_sync.py residue` lists what is there and why each one is unclearable.",
              file=sys.stderr)
        return 2

    # A key can exist on BOTH planes — a lock file here and a ref on the remote — and the
    # two are separate pieces of state with separate deletes. Collected as a LIST per key
    # rather than a dict, because clearing one and calling the key done is how the git ref
    # survived every sweep that ever ran.
    by_key: dict[str, list[dict[str, Any]]] = {}

    def offer(entry: dict[str, Any]) -> None:
        by_key.setdefault(entry["key"], []).append(entry)
        # The names 1.21.2 printed for this key — its lock slug and its ref slug — so a
        # key copied from that version's output still reaches the state it named.
        for alias in {legacy_lock_stem(entry["key"]), legacy_ref_name(entry["key"])}:
            if alias and alias != entry["key"]:
                by_key.setdefault(alias, []).append(entry)

    for e in s.residue():
        e.setdefault("plane", "fs")
        offer(e)
    git_unreadable = None
    if s.lease_mode == "git":
        refs, git_unreadable = s.git_residue()
        for e in refs:
            offer(e)
        if git_unreadable is not None:
            print(f"  ⚠ the git plane could not be read ({git_unreadable}) — anything on it "
                  "is neither cleared nor\n    reported clean. Enumerate by hand: git "
                  f"ls-remote {s.cfg.get('leaseRemote') or 'origin'} "
                  "'refs/agent-sync/leases/*'", file=sys.stderr)

    rc = 0
    for k in keys:
        entries = (by_key.get(k) or by_key.get(legacy_lock_stem(k))
                   or by_key.get(legacy_ref_name(k)) or [])
        # Deduplicate: the stem alias can offer the same object twice.
        seen_ids, unique = set(), []
        for e in entries:
            if id(e) in seen_ids:
                continue
            seen_ids.add(id(e))
            unique.append(e)
        if not unique:
            where = "this checkout" if s.lease_mode != "git" else (
                "this checkout or on the remote" if git_unreadable is None
                else "this checkout (the remote could not be read)")
            print(f"  · {k} — there is no lock by that name in {where}", file=sys.stderr)
            rc = 1
            continue
        for e in unique:
            plane = e.get("plane", "fs")
            if min_expired_s is not None and e["state"] != LIVE and (
                    e.get("expired_for") is None or e["expired_for"] < min_expired_s):
                # The sweep chose this KEY for one plane's entry; the other plane's entry is
                # younger than the threshold and is not what the operator was shown.
                print(f"  · {e['key']} [{plane}] left: {spent(e)}, under the sweep's threshold")
                continue
            if e["state"] == LIVE:
                print(f"  ✗ {e['key']} [{plane}] is LIVE under {e.get('run') or 'a run'}"
                      f"{' on ' + e['host'] if e.get('host') else ''} — not cleared. An override "
                      "is for residue;\n    a live lease belongs to a run that may still be "
                      "working. Ask the holder, or wait for the TTL.", file=sys.stderr)
                rc = 1
                continue
            had = (f"run {e.get('run') or 'unknown'}"
                   f"{' · host ' + e['host'] if e.get('host') else ''}"
                   f"{' · ' + e['ts'] if e.get('ts') else ''}"
                   f" · {spent(e)}")
            if plane == "git":
                # The same compare-and-swap the ordinary reap uses, and the same proof:
                # a second read of the remote, never the push's exit code.
                done = s.git_reap([e])[0]
                if not done.get("gone"):
                    print(f"  ✗ {e['key']} is STILL on the remote after the delete "
                          f"({done.get('why_gone') or 'no reason given'}) — either somebody "
                          "won it between the read\n    and the delete, or the remote "
                          "refused. Nothing was reported as cleared.", file=sys.stderr)
                    rc = 1
                    continue
            else:
                try:
                    e["path"].unlink()
                except OSError as exc:
                    print(f"  ✗ {e['key']} could not be removed ({exc})", file=sys.stderr)
                    rc = 1
                    continue
                # Proved gone by looking again, the same rule the ordinary reap follows.
                if any(x["key"] == e["key"] for x in s.residue()):
                    print(f"  ✗ {e['key']} is STILL PRESENT after the delete — the teardown was not "
                          "verified, whatever the call returned", file=sys.stderr)
                    rc = 1
                    continue
            print(f"  cleared {e['key']} [{plane}] by operator decision ({why}) — it held {had}")
            print(f"    the classifier called it `{e['state']}`, and that has not changed: this "
                  "was a person's\n    call, not a proof of ownership.")
            try:
                s.journal(f"reap {why} {e['key']} [{plane}] — was {had}, "
                          f"classified {e['state']}")
            except Exception:                            # noqa: BLE001 - the record plane is optional
                pass
    return rc


def cmd_journal(args: argparse.Namespace) -> int:
    return 0 if Sync().journal(" ".join(args.text)) else 1


def cmd_signal(args: argparse.Namespace) -> int:
    if not Sync().signal(args.dep, args.state):
        return 1
    print(f"{args.dep} → {args.state}")
    return 0


NOTICE_MARK = "AGENT_SYNC_NOTICE "


def cmd_guard(args: argparse.Namespace) -> int:
    """Exit 0 = allowed, 2 = denied. Any other code is non-blocking in Claude Code,
    so internal failures must also exit 2 rather than fail open.

    Takes every path a tool call writes, in ONE process (seam S5): the hook used to start
    the coordinator once per target, ~155 ms each. The first refusal decides; the paths
    after it are not checked, because the call is refused whole.
    """
    global _GIT_MEMO
    paths = args.path if isinstance(args.path, list) else [args.path]
    _GIT_MEMO = {}
    try:
        s = Sync()
        for path in paths:
            allowed, reason = s.guard(path)
            if not allowed:
                print(f"agent-sync: {reason}", file=sys.stderr)
                return 2
            print(reason)
        notice = s.long_held_notice()
    except NotCoordinated:
        # Nothing to protect here. Denying made every path in an uncoordinated
        # repository uncommittable, with a message about a lease no lease could satisfy.
        print("agent-sync: coordination is not configured in this repository; nothing to guard")
        return 0
    except Fail as exc:
        print(f"agent-sync guard: {exc}", file=sys.stderr)
        return 2
    if notice:
        # One JSON line the hook forwards (R19, seam S9): `systemMessage` reaches the person,
        # PreToolUse `additionalContext` the model, which is the one holding the lease.
        print(NOTICE_MARK + json.dumps({"systemMessage": notice, "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "additionalContext": notice}}, ensure_ascii=True))
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    s = Sync()
    results = [s.put_generated("10 Board", s.board()),
               s.put_generated(f"12 Repo — {repo_name()}", s.repo_page())]
    if getattr(args, "mirror", False):
        results += s.mirror()
    for r in results:
        print(r)
    # A refusal must be visible to a gate, not just to a reader.
    return 1 if any(r.startswith("REFUSED") for r in results) else 0


def cmd_record(args: argparse.Namespace) -> int:
    # Non-zero when the entry did not land. Printing "recorded" over a stderr line saying
    # the opposite is how an agent ends up reporting an as-built record that does not exist.
    if not Sync().record(" ".join(args.text), decision=args.decision or "",
                         files=args.files or ""):
        return 1
    print("recorded")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Mechanical divergence only. The semantic read is the agent's job, and the
    output says so rather than implying the check was complete."""
    s = Sync()
    if getattr(args, "set_baseline", False):
        stamped = s.set_baseline()
        for reg, top in stamped.items():
            print(f"baseline {reg} = {s.fmt_id(reg, top)} — ids after this must carry an as-built record")
        return 0
    findings = s.reconcile()
    print("Intent (git) vs as-built (coordination plane)\n")
    if not findings:
        print("  no mechanical divergence found")
    for f in findings:
        print(f"  ! {f['kind']}\n      {f['detail']}\n      → {f['means']}")
    for n in getattr(s, "backlog", []):
        print(f"  · {n}")
    print("\nThis compares ids, commits and presence. It does NOT judge whether the")
    print("built thing matches what the document describes — read both and decide.")
    print("Before starting: resolve divergence or record why it stands.")
    print("After finishing: update BOTH sides, then run this again.")
    return 1 if findings else 0


REGISTER_HINTS = [
    (r"\*\*Next free ID:\*\*\s*`([A-Z]{2,4})-(\d+)`", "explicit next-free-id line"),
    (r"^#{2,4}\s+([A-Z]{2,4})-\d+\s+—", "id-prefixed headings"),
]

DOC_NAMES = ("DECISIONS", "OPEN_QUESTIONS", "ROADMAP", "WORKSTREAMS", "DEPENDENCIES",
             "BUILD_ORDER", "INDEX", "ADR", "TESTING")


def cmd_adopt(_args: argparse.Namespace) -> int:
    """Inspect an existing project and PROPOSE a configuration.

    Adoption is where a coordination tool most easily starts lying: guess a register
    wrong and every later check is confidently about the wrong file. So this reads the
    repository, shows what it found and what it could not decide, and prints a config
    for a human to approve. It writes nothing.
    """
    root = project_root()
    os.chdir(root)
    print(f"agent-sync {VERSION} — adopting {repo_name()}\n")

    docs_dir = "docs" if (root / "docs").is_dir() else ""
    candidates: list[Path] = []
    for pattern in ("*.md", "docs/*.md", "docs/**/*.md", "doc/*.md"):
        candidates += [q for q in root.glob(pattern) if q.is_file()]
    candidates = sorted({q for q in candidates if ".git" not in q.parts})[:400]

    registers: dict[str, dict[str, str]] = {}
    guarded: list[str] = []
    notes: list[str] = []

    for q in candidates:
        rel = str(q.relative_to(root))
        try:
            text = q.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(REGISTER_HINTS[0][0], text)
        if m:
            registers[m.group(1)] = {
                "file": rel,
                "nextFreeIdPattern": r"\*\*Next free ID:\*\* `" + m.group(1) + r"-(\d{" + str(len(m.group(2))) + r"})`",
            }
            guarded.append(rel)
            continue
        if any(n in q.stem.upper() for n in DOC_NAMES):
            guarded.append(rel)
            ids = set(re.findall(r"\b([A-Z]{2,4})-\d{3,4}\b", text))
            if ids:
                notes.append(f"{rel}: carries ids {', '.join(sorted(ids)[:4])} but no "
                             "\"Next free ID\" line — allocation cannot be reserved safely "
                             "until one exists, or a pattern is written by hand")

    gates = []
    for cmd, probe in (("bash scripts/check-docs.sh", "scripts/check-docs.sh"),
                       ("python3 docs/ux/lint.py --strict", "docs/ux/lint.py"),
                       ("npm test", "package.json"),
                       ("pytest -q", "pyproject.toml")):
        if (root / probe).exists():
            gates.append(cmd)

    sub = git("rev-parse", "--show-superproject-working-tree")
    if sub:
        notes.append(f"this is a submodule of {Path(sub).name} — declare ONLY this "
                     "repository's registers; decisions belong to the parent")
        registers = {}

    print("Found:")
    print(f"  documents scanned : {len(candidates)}")
    print(f"  id registers      : {', '.join(registers) or 'none detected'}")
    print(f"  registry files    : {len(guarded)}")
    print(f"  gates             : {', '.join(gates) or 'none detected'}")
    print(f"  setup snapshot    : {'docs/AGENT_SYNC.md' if docs_dir else 'AGENT_SYNC.md'}")
    if notes:
        print("\nNeeds a human decision:")
        for n in notes:
            print(f"  ! {n}")

    proposed = default_config("outline")
    proposed["idRegisters"] = registers
    proposed["guardedFiles"] = sorted(set(guarded))
    proposed["gates"] = gates
    proposed["mirror"] = {"enabled": bool(docs_dir), "sources": [docs_dir] if docs_dir else []}

    print("\nProposed .claude/agent-sync.json — review every line, then write it:\n")
    print(json.dumps(proposed, indent=2))
    print("\nNothing was written. Confirm the registers and guarded files with the operator")
    print("first: a register pointed at the wrong file makes every later check confidently")
    print("wrong. Then run `init`, paste this config, `reconcile --set-baseline`, and `setup`.")
    return 0


OPEN_QUESTIONS_SEED = """# Open questions

**One job: what is not decided yet, and what settling it would unblock.**

**Next free ID:** `OQ-0001`

A question here is answered by a decision, never by a conversation: when it is settled, its row
becomes `Resolved→DEC-####` and the reasoning goes in that decision. A question with no owner and
no consequence is not an open question — it is a note, and it belongs somewhere else.

| ID | Question | Area | Status | Affects |
|---|---|---|---|---|
"""

INDEX_SEED = """# Index — one row per decision

**One job: find the decision without reading the register.** Generated by hand and gated: a
decision with no row here fails the docs gate, because a register nobody can scan is a register
nobody reads.

**It quotes no counts and no rule.** A restated rule is a second source with a decay rate; this
file holds titles and status only.

| ID | Title | Status |
|---|---|---|
"""

DEPENDENCIES_SEED = """# Dependencies — the only place a fact about two repositories lives

**One job: name what one repository needs from another, who produces it, and who is waiting.**

A row carries **both sides**. A dependency with no producer task is a dependency nobody is going to
build, and the block it causes is invisible from either side alone — the consumer says *blocked on
DEP-003*, and DEP-003 names nobody.

**No status rollup.** Each row names the producer's task; the current answer is read at its source,
never copied here where it drifts.

| ID | What is needed | Producer | Consumer | State | Notes |
|---|---|---|---|---|---|
"""

DATA_MODEL_SEED = """# Data model — one definition per thing

**One job: every entity defined once, with an address, so nothing is described twice and differently.**

Two layers, and the distinction is not cosmetic:

- **Conceptual entity** — what the thing *is* in the product, its identity, its relationships, and
  the rules that travel with it. Here.
- **Physical table** — columns, types, nullability, indexes. In the owning service's own schema
  document.

A physical table **must name the conceptual entity it implements**; one that names none is a
finding, because that is where two services drift without either being wrong. The reverse is a
finding too: an entity with no table anywhere is a thing everyone agreed on and nobody built.

Every entity heading carries an explicit anchor — `## <a id="thing"></a>Thing` — so a mention
elsewhere links to the definition rather than to the top of this file. An explicit id is checkable
at both ends; an auto-generated slug changes the moment somebody rewords a heading, and every
inbound link breaks silently.

## <a id="entity_register"></a>Entity register

| Entity | Address | Physical table in | Introduced by |
|---|---|---|---|
"""

CHECK_DOCS_SEED = r'''#!/usr/bin/env bash
# The documentation gate. Seeded by agent-sync; extend it, do not replace it.
#
# It exists because linked documentation rots quietly: a decision cites a document that never
# mentions it, a link points at a file that moved, an id is minted twice, an index row is missing.
# None of those break anything visibly, and all of them cost the next reader an hour.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
fail=0; err() { printf 'FAIL: %s\n' "$1"; fail=1; }
DOCS="${DOCS_DIR:-docs}"; [ -d "$DOCS" ] || DOCS="."
md=$(find "$DOCS" -name '*.md' 2>/dev/null; ls ./*.md 2>/dev/null)

# 1. every id that is cited is defined somewhere
for reg in DEC OQ DEP; do
  file=$(grep -rl "Next free ID:\*\* \`$reg-" $md 2>/dev/null | head -1)
  [ -z "$file" ] && continue
  # A definition is a heading or a table row in the register itself. The template block and the
  # allocation line are neither, and a gate that counts them fails a project on the day it is
  # created — which teaches everyone that the gate is noise.
  body=$(sed -n '/^```/,/^```/!p' "$file" | grep -v "Next free ID")
  defined=$(echo "$body" | grep -ohE "^#+ $reg-[0-9]{3,4}|^\| \[?$reg-[0-9]{3,4}" | grep -oE "$reg-[0-9]{3,4}" | sort -u)
  cited=$(cat $md | sed -n '/^```/,/^```/!p' | grep -v "Next free ID" | grep -ohE "\b$reg-[0-9]{3,4}\b" | sort -u)
  missing=$(comm -13 <(echo "$defined") <(echo "$cited"))
  [ -n "$missing" ] && err "$reg cited but never defined: $(echo $missing | tr '\n' ' ')"
  # 2. the next-free-ID line is the next one, or two agents mint the same number
  next=$(grep -oE "Next free ID:\*\* \`$reg-[0-9]{3,4}" "$file" | grep -oE '[0-9]{3,4}$')
  max=$(echo "$defined" | grep -oE '[0-9]{3,4}$' | sort -n | tail -1)
  if [ -n "$next" ] && [ -n "$max" ] && [ "$((10#$next))" -le "$((10#$max))" ]; then
    err "$reg next free id is $next but $reg-$max exists"
  fi
done

# 3. every relative link resolves
while IFS= read -r hit; do
  src="${hit%%:*}"; link="${hit#*:}"
  tgt="$(cd "$(dirname "$src")" && cd "$(dirname "$link")" 2>/dev/null && pwd)/$(basename "$link")"
  [ -e "$tgt" ] || err "$src → $link does not exist"
done < <(grep -rhoE '\]\(\.{1,2}/[A-Za-z0-9_./-]+\.md' $md 2>/dev/null | sed 's/](//' | sort -u | while read -r l; do grep -rl -- "]($l" $md | head -1 | sed "s|$|:$l|"; done)

# 4. every #anchor a link points at exists in the file it points at
python3 - "$ROOT" <<'PYEOF' || fail=1
import os, re, sys
root = sys.argv[1]; bad = 0; cache = {}
def ids(path):
    if path not in cache:
        try: t = open(path, errors="replace").read()
        except OSError: cache[path] = None; return None
        cache[path] = set(re.findall(r'<a id="([^"]+)"', t))
    return cache[path]
for dp, _, fns in os.walk(root):
    if any(p in dp for p in (".git", "node_modules")): continue
    for fn in fns:
        if not fn.endswith(".md"): continue
        src = os.path.join(dp, fn)
        for rel, frag in re.findall(r'\]\((\.{1,2}/[A-Za-z0-9_./-]+\.md)#([A-Za-z0-9_-]+)\)',
                                    open(src, errors="replace").read()):
            tgt = os.path.normpath(os.path.join(dp, rel)); have = ids(tgt)
            if have is not None and frag not in have:
                print("FAIL: %s -> %s#%s (no such anchor)" % (os.path.relpath(src, root), rel, frag)); bad = 1
sys.exit(bad)
PYEOF

# 5. every decision has an index row
idx=$(ls "$DOCS/INDEX.md" 2>/dev/null || true)
dec=$(ls "$DOCS/DECISIONS.md" 2>/dev/null || true)
if [ -n "$idx" ] && [ -n "$dec" ]; then
  for d in $(sed -n '/^```/,/^```/!p' "$dec" | grep -ohE '^#+ DEC-[0-9]{3,4}' | grep -oE 'DEC-[0-9]{3,4}'); do
    grep -q "$d" "$idx" || err "$d has no INDEX row"
  done
fi

[ "$fail" -eq 0 ] && printf 'OK: documentation consistent.\n'
exit "$fail"
'''

DECISIONS_SEED = """# Decisions

Every settled decision about this project, append-only. A decision is any answer that
shapes the product, architecture, scope, security, data or process — recorded here so it
is never lost to a chat log.

**Reserve an id before you write one.** Reading the line below is not reserving it: two
agents read the same number and both use it. Run `agent-sync reserve DEC`.

**Next free ID:** `DEC-0001`

---

## How to write one

```
### DEC-0001 — a title that states the decision, not the topic
- **Date:** YYYY-MM-DD · **Status:** Accepted
- **Context:** what forced the decision, with evidence
- **Decision:** what we do now, in numbered clauses
- **Consequences / affects:** every document this changes — each MUST then cite this id
- **Source:** where this came from
```

**Never edit a decision to change it.** Add a new one that names what it supersedes, and
annotate the old entry's status line. The body of the old entry stays as history.

---
"""

AGENTS_SEED = """# AGENTS.md — working protocol

**Read [`{snapshot}`]({snapshot}) first, and follow the cycle it states.** That file is
generated from the live configuration: which registers exist, which files need a lease,
which gates run, what is written where, what is never deleted, and the order to do it in.

This file deliberately does **not** restate that cycle. It is seeded once and never
overwritten, so a copy of the protocol here would be frozen on the day the project was
created while the tool moved on — and the two would disagree in front of an agent with no
way to tell which is current. One fact, one home; the home is the generated snapshot,
because it can be regenerated and `agent-sync check` fails when it goes stale.

## The three that are true in every project

1. **Several agents may work this repository at once.** `agent-sync status` before you
   start: who holds what, and what changed since you last looked.
2. **Work on a branch, and land it with `agent-sync merge`.** The integration branch is
   somebody else's stable base — nothing about work in flight is committed there.
3. **No decision lives only in chat.** Record it in the decision register, propagate it to
   every document it affects, and commit referencing the id.
"""


def cmd_scaffold(args: argparse.Namespace) -> int:
    """Create the documentation architecture a project needs to be coordinated.

    Only what is absent, never a line over anything that exists. A tool that rewrites a
    project's own conventions on adoption is worse than one that does nothing, so this
    seeds the minimum — a register with an allocation line, and an agent protocol that
    points at the generated snapshot — and leaves every existing file alone.
    """
    root = project_root()
    os.chdir(root)
    docs = root / "docs" if (root / "docs").is_dir() or args.docs_dir else root
    snapshot = "docs/AGENT_SYNC.md" if docs != root else "AGENT_SYNC.md"

    created, skipped = [], []

    def seed(path: Path, body: str) -> None:
        if path.exists():
            skipped.append(str(path.relative_to(root)))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        created.append(str(path.relative_to(root)))

    seed(docs / "DECISIONS.md", DECISIONS_SEED)
    seed(root / "AGENTS.md", AGENTS_SEED.format(snapshot=snapshot))
    if args.full:
        # The rest of the architecture that keeps documentation LINKED rather than merely present:
        # a question register that resolves into decisions, an index nobody has to scan the
        # register to use, a place for facts about two repositories, one definition per entity —
        # and the gate, because every one of those rots silently without a check that fails.
        seed(docs / "OPEN_QUESTIONS.md", OPEN_QUESTIONS_SEED)
        seed(docs / "INDEX.md", INDEX_SEED)
        seed(docs / "DEPENDENCIES.md", DEPENDENCIES_SEED)
        seed(docs / "DATA_MODEL.md", DATA_MODEL_SEED)
        gate = root / "scripts" / "check-docs.sh"
        seed(gate, CHECK_DOCS_SEED)
        if gate.exists():
            gate.chmod(0o755)

    for c in created:
        print(f"  + {c}")
    for s in skipped:
        print(f"  · {s} already exists — untouched")

    print()
    if created:
        print("Scaffolded. Now: `adopt` to see the config it implies, `init` to write it,")
        print("`reconcile --set-baseline` once, `setup` to generate the snapshot, `check`.")
    else:
        print("Nothing to scaffold — this project already has the files. Run `adopt`.")
    return 0


def _repo_state(path: Path, label: str, ignore: str) -> list[str]:
    """Is this repository clean, and is its work anywhere but here?

    Both halves matter and only one of them is obvious. Uncommitted work is visible to whoever
    is sitting in front of it; work committed and never pushed is invisible to everyone else
    while looking finished to its author — the roadmap says done, the test suite is green, and
    nobody else can fetch a line of it.
    """
    problems: list[str] = []
    porcelain = git("status", "--porcelain", cwd=path)
    dirty = [ln for ln in porcelain.split("\n")
             if ln.strip() and not re.search(ignore, ln.split()[-1] if ln.split() else "")]
    if dirty:
        problems.append(f"{label} has uncommitted work: " + ", ".join(d.split()[-1] for d in dirty[:6]))
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=path)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    if upstream:
        ahead = git("rev-list", "--count", f"{upstream}..HEAD", cwd=path) or "0"
        if ahead != "0":
            problems.append(f"{label} is {ahead} commit(s) ahead of {upstream} — pushed nowhere")
    elif branch == "HEAD":
        # A submodule sits at a detached pointer by design; what matters is that the commit exists
        # somewhere others can fetch from.
        if not git("branch", "-r", "--contains", "HEAD", cwd=path):
            problems.append(f"{label} is at a commit on no remote branch — nobody else can fetch it")
    else:
        problems.append(f"{label} ({branch}) tracks no remote — its work cannot be seen by anyone")
    return problems


def cmd_finish(args: argparse.Namespace) -> int:
    """The gate expressions this plugin's pipeline binding declares, actually executed.

    `check` answers *is this project wired correctly*. This answers *is the work finished*, and
    they are different questions with different failure modes. The one it exists for is silent by
    construction: a project of git submodules records each submodule's commit as a pointer in its
    parent, and moving the submodule does not move the pointer. So the submodule is pushed, its CI
    is green, its roadmap says `done` — and anyone who clones the parent gets the commit before the
    work. Nothing in either repository looks wrong on its own; the disagreement only exists between
    them, which is why nothing had been checking it.
    """
    s = Sync()
    root = project_root()
    ok: list[str] = []
    problems: list[str] = []
    ignore = r"\.agent-sync/"

    print(f"run {s.rid} · {'gated' if s.gated else 'ungated'}\n")

    # 1. the parent, then every submodule: clean, pushed, and pointed at
    p = _repo_state(root, root.name, ignore)
    problems.extend(p)
    if not p:
        ok.append(f"{root.name} clean and pushed")

    for line in (git("submodule", "status") or "").split("\n"):
        if not line.strip():
            continue
        prefix, rest = line[0], line[1:].split()
        sub = rest[1] if len(rest) > 1 else "?"
        if prefix == "+":
            recorded = (git("ls-tree", "HEAD", sub) or "").split()
            problems.append(
                f"{sub} — the parent points at {recorded[2][:8] if len(recorded) > 2 else '?'}, "
                f"the submodule is at {rest[0][:8]}. The bump commit is missing")
        elif prefix == "-":
            problems.append(f"{sub} is not checked out — a gate run here skips everything it owns")
        elif prefix == "U":
            problems.append(f"{sub} has merge conflicts")
        else:
            ok.append(f"{sub} pointer current")
        subp = root / sub
        if subp.is_dir():
            sp = _repo_state(subp, sub, ignore)
            problems.extend(sp)
            if not sp and prefix == " ":
                ok.append(f"{sub} clean and pushed")

    # 2. leases. A run that ends holding one blocks the next agent for the whole TTL, and the
    #    holder is a run id nobody can ask about once its session is gone.
    held = s.held()
    if held:
        problems.append("this run still holds " + ", ".join(held) + " — release before you finish")
    else:
        ok.append("no lease left held")

    #    And the other half of that question, which for six versions nothing asked: what is
    #    left on disk. `held()` answers `none` for a directory full of expired locks, so
    #    "no lease left held" was printed beside a two-day-expired one. Proof of Done
    #    records what remains — it does not license deleting all of it, so only what this
    #    run can prove it owns is a problem to fix here.
    stale = s.stale()
    reapable = [e for e in stale if e["state"] == REAPABLE]
    left_alone = [e for e in stale if e["state"] != REAPABLE]
    if not stale:
        ok.append("no expired lock left behind")
    if reapable:
        problems.append(
            f"{len(reapable)} expired lock(s) this run owns are still on disk ("
            + ", ".join(e["key"] for e in reapable)
            + ") — clear them with `agent_sync.py reap`")

    # 3. the declared gates, on request. They are the project's own commands and can be slow, so
    #    running them is opt-in — but a `finish` that never ran them is a claim, not a check.
    if args.gates:
        for cmd in s.cfg.get("gates", []):
            try:
                r = subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                                   text=True, timeout=600)
            except (OSError, subprocess.SubprocessError) as exc:
                problems.append(f"gate `{cmd}` could not run: {exc}")
                continue
            if r.returncode == 0:
                ok.append(f"gate `{cmd}`")
            else:
                tail = [ln for ln in (r.stdout + r.stderr).split("\n") if ln.strip()][-3:]
                problems.append(f"gate `{cmd}` failed: " + " / ".join(tail))

    for line in ok:
        print(f"  \u2713 {line}")
    for line in problems:
        print(f"  \u2717 {line}")
    if left_alone:
        print("\n  Left alone — residue this run cannot prove it owns:")
        for e in left_alone:
            print(f"    · {e['key']}  [{e['state']}] {e['why']}")
        print("    Reported, not touched: another run's expired lock is that run's to explain.")
    print()
    if problems:
        print(f"{len(problems)} problem(s) — this work is not finished. The usual one is a "
              "submodule commit with no parent bump:")
        print('    git -C <submodule> push && git add <submodule> && '
              'git commit -m "chore: bump <name> submodule — <why>"')
        return 1
    print(f"finished cleanly ({len(ok)} checks passed) — every repository is clean, pushed, "
          "and pointed at.")
    return 0


def check_setup(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Validate the whole setup, end to end, and refuse to call a broken one healthy.

    Every item here failed for real at some point in this tool's own adoption. A glob
    that matches nothing, a register pattern that matches nothing, a gate command that
    does not exist, a snapshot nobody links — each looks like a working install and
    protects nothing.

    Returns `(ok, warn, problems)` rather than printing, because `status` reports the same
    verdict and the two must not be able to disagree. They did: `status` printed "NEXT:
    acquire a lease" and exited 0 on a project `check` called NOT healthy — and `status`
    is the command every session runs, so the defect had a place to hide in plain sight.
    """
    problems: list[str] = []
    warn: list[str] = []
    ok: list[str] = []

    cfg_path = root / CONFIG_PATH
    if not cfg_path.exists():
        raise Fail("not initialised — run `adopt`, then `init`")
    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as exc:
        raise Fail(f"{CONFIG_PATH} is not valid JSON: {exc}") from exc
    ok.append(f"config parses ({CONFIG_PATH})")

    if cfg.get("backend") not in BACKENDS:
        problems.append(f"backend '{cfg.get('backend')}' is not a known adapter")
    for k in sorted(set(cfg) - CONFIG_KEYS):
        problems.append(f"config key '{k}' is not in the schema — it will be ignored")
    if "scheduledIntervalSeconds" in cfg:
        # The schema says integer, minimum 60; `check` is what an operator runs, so it
        # enforces what the schema declares (fix round 2, item k). Below a minute a lease
        # held across one renew interval would already read as "held too long".
        iv = cfg["scheduledIntervalSeconds"]
        if isinstance(iv, bool) or not isinstance(iv, int) or iv < 60:
            problems.append(f"scheduledIntervalSeconds is {iv!r} — the schema requires an "
                            "integer of at least 60 (seconds)")

    # Registers must exist, their allocation pattern must match — and SOMETHING must be
    # able to hand an id out at all. A register no allocator serves is a rule that protects
    # nothing, which is the exact thing `check` promises to refuse: the generated snapshot
    # then instructs every agent to run `reserve <REG>`, and the command cannot succeed.
    #
    # The question is asked through `id_allocator`, the predicate `reserve` itself uses.
    # It used to be asked of the record plane alone, so `backend: fs` + `leaseBackend:
    # git` — ids from refs/agent-sync/ids/* at the remote, race-free — was refused, and
    # every such repository dropped its registers and took numbers by hand.
    regs = cfg.get("idRegisters") or {}
    if regs:
        backend = os.environ.get("AGENT_SYNC_BACKEND") or cfg.get("backend") or "fs"
        probe = make_adapter(cfg, root)
        authority, how = id_allocator(cfg, probe)
        if authority is None:
            problems.append(
                f"{len(regs)} id register(s) declared, but backend '{probe.name}' cannot "
                f"reserve ids (atomicAppend is false){' — the configured backend is ' + backend + ' and it is not reachable, so runs degrade to fs' if backend != probe.name else ''}, "
                "and leaseBackend is not git. `reserve` fails every time here; set "
                "`leaseBackend: \"git\"` (ids then come from refs/agent-sync/ids/* at the "
                "remote, whatever the record plane), configure a backend that can allocate, "
                "or remove the registers and allocate them in the parent repository")
        elif authority == "git" and not git_remote_exists(
                cfg.get("leaseRemote") or "origin", root):
            problems.append(
                f"{len(regs)} id register(s) declared and ids are allocated by "
                f"{how}, but remote '{cfg.get('leaseRemote') or 'origin'}' does not exist "
                "— `reserve` cannot allocate anything until it does")
        else:
            ok.append(f"{len(regs)} id register(s) allocated by {how}"
                      + (" — race-free across machines, whatever the record plane"
                         if authority == "git" else ""))
    for reg, spec in sorted(regs.items()):
        f = root / spec.get("file", "")
        if not f.exists():
            problems.append(f"register {reg}: file '{spec.get('file')}' does not exist")
            continue
        text = f.read_text()
        pat = id_pattern(spec)
        if not pat:
            problems.append(f"register {reg}: no id pattern — set `pattern` (or the legacy "
                            f"`nextFreeIdPattern`). An absent one used to become "
                            f"`re.search(\"\", text)`, which matches at position 0, so this "
                            f"check took the found branch and crashed instead of reporting")
            continue
        try:
            m = re.search(pat, text)
        except re.error as exc:
            problems.append(f"register {reg}: id pattern is not valid regex ({exc})")
            continue
        if not m:
            problems.append(f"register {reg}: nextFreeIdPattern matches nothing in "
                            f"{spec['file']} — ids cannot be reserved, only guessed")
        else:
            ok.append(f"register {reg} allocates from {spec['file']} ({reg}-{m.group(1)})")

    # A guard glob that matches nothing protects nothing. Resolved by the same function
    # the guard itself applies, so the two commands cannot disagree about one pattern —
    # and from one walk of the repository, not one per pattern.
    guard_hits = glob_files_many(root, list(cfg.get("guardedFiles") or []))
    for pattern in (cfg.get("guardedFiles") or []):
        hits = guard_hits.get(pattern) or []
        if not hits:
            problems.append(f"guarded pattern '{pattern}' matches no file — it guards nothing")
    if cfg.get("guardedFiles"):
        ok.append(f"{len(cfg['guardedFiles'])} guarded pattern(s) declared")
    else:
        warn.append("no guarded files — nothing requires a lease in this repository")

    claim_hits = glob_files_many(root, list(cfg.get("claimTags") or {}))
    for pattern, spec in (cfg.get("claimTags") or {}).items():
        files = claim_hits.get(pattern) or []
        if not files:
            problems.append(f"claimTags pattern '{pattern}' matches no file")
            continue
        if spec.get("mode") != "cell":
            problems.append(f"claimTags '{pattern}': mode must be 'cell'")
            continue
        if "cell" not in spec:
            problems.append(f"claimTags '{pattern}': no `cell` index — nothing to write")
        if "{holder}" not in (spec.get("held") or ""):
            problems.append(f"claimTags '{pattern}': `held` must contain {{holder}}, "
                            "or the claim names nobody")
    if cfg.get("claimTags"):
        ok.append(f"{len(cfg['claimTags'])} claim-tag mapping(s) declared")

    mode = cfg.get("leaseBackend") or "local"
    headline, detail = lease_guarantee(mode)
    if mode not in LEASE_GUARANTEE:
        problems.append(f"leaseBackend '{mode}': {headline} — {detail}")
    elif mode == "git":
        remote = cfg.get("leaseRemote") or "origin"
        if not git("remote", "get-url", remote):
            problems.append(f"leaseBackend is 'git' but remote '{remote}' does not exist — "
                            f"the lease cannot be decided at all ({headline} claimed, "
                            "none delivered)")
        else:
            ok.append(f"lease decided by git refs on '{remote}' — {headline}")
    else:
        warn.append(f"lease is a local file lock: {headline}. {detail[0].upper()}{detail[1:]}")

    for cmd in (cfg.get("gates") or []):
        exe = cmd.split()[0]
        target = cmd.split()[1] if len(cmd.split()) > 1 else ""
        if target and not target.startswith("-") and "/" in target and not (root / target).exists():
            problems.append(f"gate '{cmd}': {target} does not exist")
        elif not shutil.which(exe):
            warn.append(f"gate '{cmd}': {exe} is not on PATH here")
    if cfg.get("gates"):
        ok.append(f"{len(cfg['gates'])} gate command(s) declared")

    mirror = cfg.get("mirror") or {}
    if mirror.get("enabled"):
        for src in mirror.get("sources") or []:
            if not (root / src).exists():
                problems.append(f"mirror source '{src}' does not exist")
        if not mirror.get("sources"):
            problems.append("mirror is enabled with no sources — it renders nothing")

    # Identity and reachability. Which file is in force is reported in every mode: it may
    # be the local one, a superproject's, or one named by AGENT_SYNC_ENV, and an operator
    # debugging a degraded run needs to know which of the three answered.
    env = find_env_file(root)
    if env is not None and env.parent != root:
        ok.append(f"credentials file in force: {env} (outside this repository)")
    elif env is not None:
        ok.append(f"credentials file in force: {env}")
    cloud = CLOUD_ADAPTERS.get(cfg.get("backend") or "")
    if cloud is not None:
        if env is None:
            problems.append(f"no {ENV_FILE} found here or in any parent — the backend "
                            "cannot be reached, and every run silently degrades")
        else:
            ok.append(f"credentials file found at {env}")
            missing = [k for k in cloud.REQUIRED_ENV if not os.environ.get(k)]
            if missing:
                problems.append(f"{', '.join(missing)} is empty — runs will degrade to `fs`")
            else:
                try:
                    ok.append(cloud().preflight() or f"{cloud.name}: credentials present")
                except Fail as exc:
                    problems.append(f"knowledge base unreachable: {exc}")

    # Ignore rules — a committed token is the one unrecoverable mistake here.
    gi = (root / ".gitignore").read_text() if (root / ".gitignore").exists() else ""
    for entry in (str(ENV_FILE), f"{STATE_DIR}/"):
        if entry not in gi:
            problems.append(f".gitignore does not cover '{entry}'")
    tracked = git("ls-files", str(ENV_FILE))
    if tracked:
        problems.append(f"{ENV_FILE} IS TRACKED BY GIT — it holds a token; remove it now")

    # The snapshot, and whether anything points at it.
    snap = root / (cfg.get("setupFile") or
                   ("docs/AGENT_SYNC.md" if (root / "docs").is_dir() else "AGENT_SYNC.md"))
    if not snap.exists():
        problems.append(f"no setup snapshot at {snap.relative_to(root)} — run `setup`")
    else:
        head = snap.read_text().splitlines()[0] if snap.read_text() else ""
        if GENERATED_MARKER not in head:
            problems.append(f"{snap.relative_to(root)} lost its generated marker — "
                            "someone hand-edited it; regenerate or keep it out of the way")
        else:
            # Stale means "the configuration moved on". Comparing commits was wrong at
            # both boundaries: a snapshot is generated before the commit that carries it,
            # and the config is often added in that same commit. A content hash is exact.
            stamped = re.search(r"cfg=(\w+)", head)
            actual = hashlib.sha256((root / CONFIG_PATH).read_bytes()).hexdigest()[:12]
            if not stamped:
                warn.append("snapshot predates configuration stamping — regenerate with `setup`")
            elif stamped.group(1) != actual:
                problems.append("the configuration changed since the snapshot was "
                                "generated — regenerate with `setup`")
            else:
                ok.append("setup snapshot present and describes the current configuration")
        linked = [f for f in ("AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md")
                  if (root / f).exists() and snap.name in (root / f).read_text()]
        if linked:
            ok.append(f"snapshot linked from {', '.join(linked)}")
        else:
            problems.append("no agent instruction file links the snapshot — agents will "
                            "not find it, and will infer the pipeline instead")

    # Every log this project keeps must be replayable. A log past the unparseable limit is
    # refused by every reader, so a setup carrying one is broken however well it is wired —
    # and the failure used to be swallowed here by a bare `except Fail: pass`.
    try:
        s = Sync()
    except Fail as exc:
        problems.append(f"cannot open the project: {exc}")
        s = None
    if s is not None:
        for which in sorted(LOGS):
            try:
                s.events(which)
            except Fail as exc:
                problems.append(f"{which} log: {exc}")

        # Baselines: without one, reconcile cannot separate history from new work.
        if regs:
            try:
                ev, _ = s.events("asbuilt")
                based = {e["key"] for e in ev if e["op"] == "baseline"}
                for reg in regs:
                    if reg not in based:
                        warn.append(f"register {reg} has no as-built baseline — "
                                    "run `reconcile --set-baseline` once")
            except Fail:
                pass          # already reported above; one line per defect, not two

    tracked_state = git("ls-files", "--", STATE_DIR)
    if tracked_state:
        problems.append(
            f"{STATE_DIR}/ is tracked by git ({len(tracked_state.split(chr(10)))} file(s)) — it is "
            "generated state, the repository is dirty after every tool call, and a committed "
            "run-id hands this checkout's identity to every clone. "
            f"Run: git rm -r --cached {STATE_DIR} && commit")
    else:
        ok.append(f"{STATE_DIR}/ is not tracked")

    if cfg.get("settleSeconds") is not None:
        warn.append("settleSeconds is set but no shipped adapter reads it — it is retained "
                    "for a backend that must wait for writes to become visible, and does "
                    "nothing here")

    return ok, warn, problems


def cmd_check(_args: argparse.Namespace) -> int:
    root = project_root()
    os.chdir(root)
    load_env_file(root)
    try:
        ok, warn, problems = check_setup(root)
    except Fail as exc:
        print(f"✗ {exc}")
        return 1

    for line in ok:
        print(f"  ✓ {line}")
    for line in warn:
        print(f"  ! {line}")
    for line in problems:
        print(f"  ✗ {line}")
    print()
    if problems:
        print(f"{len(problems)} problem(s) — this setup is NOT healthy. Fix them before "
              "telling anyone the project is coordinated.")
        return 1
    print(f"setup healthy ({len(ok)} checks passed"
          + (f", {len(warn)} warning(s)" if warn else "") + ")")
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    s = Sync()
    path = s.setup_path()
    if path.exists():
        current = path.read_text()
        if current.strip() and not current.lstrip().startswith(GENERATED_MARKER):
            print(f"REFUSED: {path} exists and was not generated by agent-sync — "
                  "reporting instead of overwriting", file=sys.stderr)
            return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s.setup_snapshot())
    print(f"wrote {path.relative_to(s.root)}")
    print("Commit it, and link it from the project's agent instructions so every agent "
          "reads the same description of the pipeline before touching it.")
    return 0


def cmd_whoami(_args: argparse.Namespace) -> int:
    s = Sync()
    for stem, key in s.migrate_legacy_notes():
        print(f"note: {stem} is the name agent-sync 1.21.2 gave lease {key} — re-noted as {key}",
              file=sys.stderr)
    print(f"run {s.rid} · backend {s.adapter.name} · "
          f"{'gated' if s.gated else 'ungated'}")
    print(f"holds: {', '.join(s.held()) or 'nothing'}")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """Land a branch on the integration branch, and leave a record of what landed.

    Every check here runs *before* anything is touched, because a merge that starts and
    then aborts leaves the operator somewhere they did not ask to be. The conflict answer
    comes from `git merge-tree`, which computes it in memory.
    """
    s = Sync()
    branch = current_branch()
    target = args.into or s.integration_branch

    if not branch or branch == "HEAD":
        raise Fail("detached HEAD — check out the branch you want to merge")
    if branch == target:
        raise Fail(f"already on {target} — there is no branch to merge. Work happens on a "
                   f"branch so the integration branch stays somebody else's stable base")
    dirty = [l for l in (git("status", "--porcelain") or "").splitlines()
             if l and STATE_DIR.name not in l]
    if dirty:
        raise Fail(f"working tree is not clean ({len(dirty)} path(s)) — commit or stash first; "
                   "a merge cannot tell your uncommitted work from the branch's")

    # A merge commit is authorship, so it needs a real identity — unlike a lease object,
    # which is plumbing and is written with a synthetic one on purpose. Checked HERE
    # because everything in this command is checked before anything is touched: without
    # it the merge starts, git refuses at the commit, and the abort path runs. That is
    # recoverable and it is still not what this command promises. It is also the ordinary
    # state of a CI runner and a fresh container, which is where it was found.
    ident = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"],
                           capture_output=True, text=True)
    if ident.returncode != 0:
        why = (ident.stderr or "").strip().splitlines()
        raise Fail("git has no committer identity here, so the merge commit cannot be "
                   "written. Set one and run this again — nothing was touched:\n"
                   "  git config user.name '<you>' && git config user.email '<you@example>'"
                   + (f"\n  ({why[-1]})" if why else ""))

    git("fetch", "--quiet", "origin", target)
    upstream = f"origin/{target}" if git("rev-parse", "--verify", "--quiet", f"origin/{target}") else target

    # The conflict preflight, the diff and the merge must all be against the SAME base.
    # They were not: everything was measured against `origin/<target>` and the merge was
    # then made into the LOCAL `<target>`, which nothing advances. So `merge` printed the
    # staleness it had just measured, printed "✓ merged", wrote a merge-log entry and
    # released the lease — and the push was rejected as non-fast-forward. The work had not
    # landed, the log said it had, and the task was free for somebody else to take.
    holder = worktree_holding(target)
    if holder and not args.dry_run and not args.push:
        raise Fail(
            f"{target} is checked out in another worktree ({holder}), so it cannot be "
            f"checked out here and its ref must not move under that worktree. Nothing was "
            f"touched. Run this again with --push: the merge is made in a temporary worktree "
            f"and lands on origin/{target}; or merge from {holder}")
    if upstream != target and not holder:
        local_exists = bool(git("rev-parse", "--verify", "--quiet", f"refs/heads/{target}"))
        behind_local = git("rev-list", "--count", f"{target}..{upstream}") if local_exists else "0"
        ahead_local = git("rev-list", "--count", f"{upstream}..{target}") if local_exists else "0"
        if local_exists and behind_local not in ("", "0") and ahead_local not in ("", "0"):
            raise Fail(
                f"local {target} has diverged from {upstream} — {ahead_local} commit(s) here "
                f"that are not there, {behind_local} there that are not here. Reconcile it "
                f"first; merging into it would produce a branch nobody can push")
        if not local_exists or behind_local not in ("", "0"):
            moved = subprocess.run(
                ["git", "update-ref", f"refs/heads/{target}",
                 git("rev-parse", upstream)], capture_output=True, text=True)
            if moved.returncode != 0:
                raise Fail(f"could not fast-forward {target} to {upstream}: "
                           f"{moved.stderr.strip()[:160]}")
            if local_exists:
                print(f"  {target} fast-forwarded {behind_local} commit(s) to {upstream}")

    behind = git("rev-list", "--count", f"{branch}..{upstream}")
    changed = [l for l in (git("diff", "--name-only", f"{upstream}...{branch}") or "").splitlines() if l]
    stat = git("diff", "--shortstat", f"{upstream}...{branch}") or "no changes"

    print(f"{branch} → {target}")
    print(f"  files changed  : {len(changed)}  ({stat.strip()})")
    print(f"  {target} moved  : {behind or '0'} commit(s) since this branch started")

    conflicts = merge_conflicts(upstream, branch)
    if conflicts:
        print("\n✗ this merge conflicts. Nothing was touched.")
        for c in conflicts[:20]:
            print(f"    · {c}")
        print(f"\nNEXT: rebase or merge {target} into {branch} in your own branch, resolve there,")
        print("  then run this again. The integration branch never carries a resolution nobody reviewed.")
        return 1
    print("  conflicts      : none")

    others = {k: v for k, v in s.all_holders().items() if v != s.rid}
    if others:
        print("\n  other runs hold leases right now:")
        for k, v in sorted(others.items()):
            print(f"    · {v} holds {k}")
        print("  Their work is not in this diff. If it touches the same files, they merge into "
              "what you are about to land.")

    if args.dry_run:
        if holder:
            print(f"\n  {target} is checked out in {holder}: a real run needs --push and lands "
                  f"on origin/{target} through a temporary worktree")
        print("\n(dry run — nothing merged)")
        return 0

    if holder:
        return _merge_in_temporary_worktree(s, args, branch, target, upstream, holder,
                                            changed, stat)

    if git("checkout", target) == "" and current_branch() != target:
        raise Fail(f"could not check out {target}")
    msg = args.message or f"Merge {branch}" + (f" — {args.key}" if args.key else "")
    r = subprocess.run(["git", "merge", "--no-ff", "-m", msg, branch],
                       capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], capture_output=True)
        git("checkout", branch)
        raise Fail(f"merge failed and was aborted; you are back on {branch}\n"
                   f"  {(r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else ''}")
    sha = head_sha()
    print(f"\n✓ merged as {sha}")

    path = s.merge_log_append({
        "ts": now_iso(), "key": args.key or "—", "branch": branch, "target": target,
        "sha": sha, "run": s.rid, "files": f"{len(changed)} ({stat.strip()})",
        "conflicts": "none", "summary": args.summary or "(no summary given)",
    })
    rel_log = path.relative_to(s.root)
    git("add", str(rel_log))
    subprocess.run(["git", "commit", "--quiet", "-m",
                    f"docs(merges): record {branch} → {target}"], capture_output=True)
    print(f"✓ recorded in {rel_log}")

    if args.push:
        out = subprocess.run(["git", "push", "origin", target], capture_output=True, text=True)
        if out.returncode != 0:
            # Released after a failed push, the lease would free work that has not landed.
            raise Fail(f"push of {target} failed — the merge exists only here and every lease "
                       f"is still held: {(out.stderr or '').strip().splitlines()[-1:]}")
        print(f"✓ pushed {target}")
    unreleased = _release_landed(s, args)
    if not args.push:
        print(f"\nNEXT: push {target}, or run `finish` to check every repository first.")
    return 1 if unreleased else 0


def _release_landed(s: "Sync", args: argparse.Namespace) -> list[str]:
    """Release the lease this merge landed; return the keys that could not be released."""
    # Only the lease this merge landed. It used to release every lease the run held, which
    # is a different statement from the one the documentation makes and quietly frees work
    # that has not landed.
    held = s.held()
    if args.key:
        to_release = [args.key] if args.key in held else []
        if not to_release and held:
            print(f"note: this run does not hold {args.key}; leaving "
                  f"{', '.join(held)} held")
    else:
        to_release = held
    unreleased = []
    for key in to_release:
        # The merge has landed; whether the lease is gone is a separate fact, and a "✓"
        # printed over a release that did not happen is #25's false success again.
        if s.release(key):
            print(f"✓ released {key}")
        else:
            print(f"✗ NOT released {key} — the lease is still held; release it once the "
                  "reason above is fixed", file=sys.stderr)
            unreleased.append(key)
    return unreleased


def _merge_in_temporary_worktree(s: "Sync", args: argparse.Namespace, branch: str, target: str,
                                 upstream: str, holder: str, changed: list[str],
                                 stat: str) -> int:
    """Merge `branch` into `upstream` in a detached temporary worktree and push it to origin.

    The worktree that has `target` checked out is never touched — not its files, not its
    ref. The merge, the merge-log entry and the push all happen in a throwaway directory
    that is removed on every path. The leases are released only after the push is accepted.
    """
    tmp = Path(tempfile.mkdtemp(prefix="agent-sync-merge-"))
    added = subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(tmp), upstream],
                           capture_output=True, text=True)
    if added.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise Fail(f"could not create a temporary worktree at {upstream}: "
                   f"{added.stderr.strip()[:160]}")
    try:
        msg = args.message or f"Merge {branch}" + (f" — {args.key}" if args.key else "")
        r = subprocess.run(["git", "merge", "--no-ff", "-m", msg, branch], cwd=str(tmp),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Fail(f"merge failed in the temporary worktree; nothing landed and {branch} "
                       f"is untouched\n  {(r.stderr or r.stdout).strip().splitlines()[-1:]}")
        sha = git("rev-parse", "--short", "HEAD", cwd=tmp) or "unknown"
        print(f"\n✓ merged as {sha} (temporary worktree; {target} is checked out in {holder})")
        root = s.root
        try:
            s.root = tmp  # the log is the integration branch's file, so it is written there
            path = s.merge_log_append({
                "ts": now_iso(), "key": args.key or "—", "branch": branch, "target": target,
                "sha": sha, "run": s.rid, "files": f"{len(changed)} ({stat.strip()})",
                "conflicts": "none", "summary": args.summary or "(no summary given)",
            })
        finally:
            s.root = root
        rel_log = path.relative_to(tmp)
        git("add", str(rel_log), cwd=tmp)
        subprocess.run(["git", "commit", "--quiet", "-m", f"docs(merges): record {branch} → {target}"],
                       cwd=str(tmp), capture_output=True)
        print(f"✓ recorded in {rel_log}")
        out = subprocess.run(["git", "push", "origin", f"HEAD:refs/heads/{target}"], cwd=str(tmp),
                             capture_output=True, text=True)
        if out.returncode != 0:
            raise Fail(f"push to origin/{target} failed — nothing landed and every lease is "
                       f"still held: {(out.stderr or '').strip().splitlines()[-1:]}")
        print(f"✓ pushed origin/{target}")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
    unreleased = _release_landed(s, args)
    print(f"\nNEXT: {holder} still shows the old {target}; whoever works there runs "
          f"`git pull --ff-only` when they are ready.")
    return 1 if unreleased else 0


def cmd_merges(args: argparse.Namespace) -> int:
    """What landed while you were on your branch."""
    s = Sync()
    path, retention = s.merge_log()
    if not path.exists():
        print(f"no merge log yet at {path.relative_to(s.root)} — `merge` writes one")
        return 0
    if args.compact:
        s.merge_log_append()          # same pass the writer runs, without a new entry
        print("compacted anything past the retention window")
    entries, compacted = s._read_merge_log(path)
    print(f"{path.relative_to(s.root)} — {len(entries)} detailed (last {retention} days), "
          f"{len(compacted)} compacted\n")
    for e in (entries if args.all else entries[:args.limit]):
        print(f"{e['ts']} · {e['key']} · {e['branch']} → {e['target']} · {e['sha']}")
        print(f"    {e['summary']}")
    if args.all and compacted:
        print("\nolder:")
        for line in compacted[:args.limit if not args.all else len(compacted)]:
            print(f"  {line[2:]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_sync.py", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="ask where to store, write config and env file")
    i.add_argument("--backend", required=True, choices=list(BACKENDS))
    i.add_argument("--url", help="instance URL (required for outline)")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    sub.add_parser("status", help="inspect, repair, report, one next action").set_defaults(fn=cmd_status)
    sub.add_parser("bootstrap", help="create the cloud container").set_defaults(fn=cmd_bootstrap)
    sub.add_parser("whoami", help="this run and its leases").set_defaults(fn=cmd_whoami)
    sub.add_parser("setup", help="write the generated snapshot of how this project is wired").set_defaults(fn=cmd_setup)
    sub.add_parser("adopt", help="inspect an existing project and propose a config (writes nothing)").set_defaults(fn=cmd_adopt)
    sub.add_parser("check", help="validate the whole setup; non-zero if it is not healthy").set_defaults(fn=cmd_check)
    fi = sub.add_parser("finish", help="is the work finished — every repo clean, pushed and pointed at; no lease held")
    fi.add_argument("--gates", action="store_true", help="also run the project's declared gate commands")
    fi.set_defaults(fn=cmd_finish)
    mg = sub.add_parser("merge", help="land this branch on the integration branch, and record it")
    mg.add_argument("--into", help="integration branch (default: config, else the repo's own)")
    mg.add_argument("--key", help="the task id this branch delivered")
    mg.add_argument("--summary", help="one line for the merge log — what landed")
    mg.add_argument("--message", help="merge commit message")
    mg.add_argument("--push", action="store_true", help="push the integration branch afterwards")
    mg.add_argument("--dry-run", action="store_true", help="check conflicts and stop")
    mg.set_defaults(fn=cmd_merge)
    ml = sub.add_parser("merges", help="what landed while you were on your branch")
    ml.add_argument("--all", action="store_true", help="include the compacted tail")
    ml.add_argument("--limit", type=int, default=10, help="how many detailed entries to print")
    ml.add_argument("--compact", action="store_true", help="run the compaction pass now")
    ml.set_defaults(fn=cmd_merges)
    sc = sub.add_parser("scaffold", help="create the missing documentation architecture (never overwrites)")
    sc.add_argument("--docs-dir", action="store_true", help="put the register under docs/ even if it does not exist yet")
    sc.add_argument("--full", action="store_true",
                    help="also seed OPEN_QUESTIONS, INDEX, DEPENDENCIES, DATA_MODEL and the docs gate")
    sc.set_defaults(fn=cmd_scaffold)
    bd = sub.add_parser("board", help="regenerate the read-only board")
    bd.add_argument("--mirror", action="store_true",
                    help="also render the configured git documents into the plane")
    bd.set_defaults(fn=cmd_board)

    q = sub.add_parser("acquire")
    q.add_argument("key")
    q.set_defaults(fn=cmd_acquire)
    q = sub.add_parser("release", help="release a lease, or --held for all this run holds")
    q.add_argument("key", nargs="?")
    q.add_argument("--held", action="store_true",
                   help="release every lease this run holds, in one process (SessionEnd)")
    q.add_argument("--session-repos", action="store_true", dest="session_repos",
                   help="also in every other repository this session took a lease in (AS-07)")
    q.add_argument("--report-held", action="store_true", dest="report_held",
                   help=argparse.SUPPRESS)
    q.set_defaults(fn=cmd_release)

    r = sub.add_parser("renew")
    r.add_argument("key", nargs="?")
    r.add_argument("--active-only", action="store_true",
                   help="heartbeat: renew only keys active within two renewal intervals")
    r.add_argument("--session-repos", action="store_true", dest="session_repos",
                   help="also renew in every other repository this session took a lease in (AS-07)")
    r.add_argument("--report-held", action="store_true", dest="report_held",
                   help=argparse.SUPPRESS)
    r.set_defaults(fn=cmd_renew)

    rv = sub.add_parser("reserve", help="reserve the next id in a register")
    rv.add_argument("register")
    rv.add_argument("--key", dest="rkey", default=None,
                    help="reservation key: a retry with the same key returns the SAME id")
    rv.add_argument("--offline", action="store_true",
                    help="issue a namespaced offline id (no global authority; map it later)")
    rv.set_defaults(fn=cmd_reserve)
    mo = sub.add_parser("map-offline",
                        help="bind an offline id to a reserved number, append-only")
    mo.add_argument("register")
    mo.add_argument("offline_id")
    mo.add_argument("number")
    mo.set_defaults(fn=cmd_map_offline)

    ri = sub.add_parser("release-id", help="return an id you did not write to git")
    ri.add_argument("register")
    ri.add_argument("value")
    ri.set_defaults(fn=cmd_release_id)

    rec = sub.add_parser("record", help="append what was ACTUALLY built")
    rec.add_argument("text", nargs="+")
    rec.add_argument("--decision", help="the id it implements, e.g. DEC-0216")
    rec.add_argument("--files", help="comma-separated paths actually changed")
    rec.set_defaults(fn=cmd_record)

    rc = sub.add_parser("reconcile", help="intent (git) vs as-built (cloud)")
    rc.add_argument("--set-baseline", action="store_true",
                    help="stamp today's ids as the pre-adoption backlog, once")
    rc.set_defaults(fn=cmd_reconcile)

    j = sub.add_parser("journal")
    j.add_argument("text", nargs="+")
    j.set_defaults(fn=cmd_journal)

    sg = sub.add_parser("signal", help="record dependency state, never transmit an ask",
                        description="Record dependency state only. Where Fabric serves the "
                        "project, send cross-project asks through com.submit and cite its "
                        "board message in the DEP record.")
    sg.add_argument("dep")
    sg.add_argument("state")
    sg.set_defaults(fn=cmd_signal)

    sub.add_parser("residue", help="expired locks left on disk, classified by who can "
                                    "prove they own them").set_defaults(fn=cmd_residue)
    rp = sub.add_parser("reap", help="clear expired locks this run provably owns; foreign "
                                     "and ambiguous ones are reported, never touched")
    rp.add_argument("keys", nargs="*", help="which to clear (default: every reapable one)")
    rp.add_argument("--i-own-this", action="store_true", dest="own",
                    help="clear the NAMED expired locks even though this run cannot prove "
                         "they are its own — an operator's decision, per key, recorded. "
                         "Refuses a live lease and refuses to run with no key.")
    rp.add_argument("--long-expired", action="store_true",
                    help="list locks on both planes expired at least --older-than-days ago "
                         f"(default {LONG_EXPIRED_DAYS}), each with the classifier's verdict; "
                         "with --apply, clear them — a sweep by age alone, so the OPERATOR's "
                         "decision, never an agent's; each removal printed and journaled")
    rp.add_argument("--older-than-days", type=int, dest="older_than_days",
                    help="with --long-expired: the age threshold in days, at least 1")
    rp.add_argument("--apply", action="store_true",
                    help="with --long-expired: clear what it lists (operator only)")
    rp.set_defaults(fn=cmd_reap)

    wt = sub.add_parser("write-targets", help="files a Bash tool call writes (payload on stdin); "
                                              "used by the guard hook — AS-09")
    wt.set_defaults(fn=cmd_write_targets)

    g = sub.add_parser("guard", help="may this run write that path? exit 2 = no")
    g.add_argument("path", nargs="+")
    g.set_defaults(fn=cmd_guard)

    return p


def main(argv: list[str] | None = None) -> int:
    global RECORD_PLANE_COMMAND, INVOKED_ARGV, _GIT_MEMO, TOKEN_SOURCE, VAULT_DEGRADED
    args = build_parser().parse_args(argv)
    # Per-invocation state: `main()` called twice in one process (the tests do) must not
    # see the previous call's git answers or token source.
    _GIT_MEMO, TOKEN_SOURCE, VAULT_DEGRADED = None, None, None
    # The vault re-run repeats exactly what THIS invocation was given — `main(argv)` called
    # in-process is not `sys.argv` — and only for a command that touches the record plane.
    INVOKED_ARGV = list(argv) if argv is not None else sys.argv[1:]
    # `release --held` is SessionEnd's (and its children's) command: see NO_RECORD_PLANE.
    RECORD_PLANE_COMMAND = args.cmd not in NO_RECORD_PLANE and not (
        args.cmd == "release" and getattr(args, "held", False))
    _VAULT_DECIDED.clear()
    try:
        return int(args.fn(args))
    except Fail as exc:
        print(f"agent-sync: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
