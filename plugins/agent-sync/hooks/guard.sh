#!/usr/bin/env bash
# PreToolUse guard. Exit 2 blocks the call and shows stderr as the reason.
# Any other non-zero code is NON-blocking in Claude Code, so every internal
# failure must also exit 2 — a crashing guard that fails open guards nothing.
set -uo pipefail
# --------------------------------------------------------------------------
# THE PROTECTION BOUNDARY IS ADVISORY, AND STATED SO (FIX-SY-07.01).
#
# This guard is a best-effort PreToolUse check, not a sandbox. It covers the
# mutation surfaces a Claude Code matcher can name; everything else is a write
# vector it cannot see. The capability matrix, per write vector:
#
#   Write / Edit / MultiEdit / NotebookEdit   COVERED  (file_path matcher)
#   Bash: git commit (incl. -C, env, compound) COVERED (parsed below)
#   Bash: git -C <dir> commit                   COVERED (repo resolved from -C)
#   Bash: env VAR=x git commit                  COVERED (env prefix skipped)
#   Bash: compound (a && git commit)            COVERED (each segment parsed)
#   Bash: git add X && git commit (one call)    COVERED (1.22.0, AS-16): what the SAME command
#     stages is read from it -- `git add <specs>`, `git add -A/-u/.`, `git commit -a`,
#     `git commit <paths>` -- and asked of `git ls-files` (modified + untracked under the
#     specs), then checked with the index. The hook runs before the command, so the index
#     alone never held X.
#   Bash writes, parsed by scripts/bash_writes.py (1.22.0, AS-09) -- COVERED*, and only
#   these shapes, each a case in test/coordination_test.py:
#     > >> >| &> &>> <> and `>& FILE` redirects; tee [-a]; sed -i (GNU and BSD, -e,
#     --expression=, -f); cp; mv (`-t DIR` too; mv's SOURCE is a write as well); after
#     `cd [-L|-P] DIR`, inside `( ... )` and `<( ... )`/`>( ... )`, behind VAR=x / env
#     [-C DIR] / sudo / nohup / time / command / exec / timeout / nice / stdbuf / xargs
#     prefixes; quoted ; and | stay in their word; heredoc bodies are data, never commands,
#     whatever the delimiter's quoting ('EOF', "EOF", \EOF)
#   Bash: a target holding $VAR, a backtick or a glob   UNPARSED (skipped)
#   Bash: a relative target after `cd -`, `cd`, `cd $X` UNPARSED (directory unknown)
#   Bash: the arguments xargs reads from stdin; env -S  UNPARSED (not in the command)
#   Bash: a `cd` in a pipeline or behind `&`            followed NOT AT ALL (a subshell)
#   Bash: apply_patch                          UNSUPPORTED (no file_path parse)
#   Bash: python -c "...write_text(...)"        UNSUPPORTED (a runtime, not a path)
#   Bash: perl -i, awk, dd, install, rsync      UNSUPPORTED (not parsed)
#   (sed -i / perl -i were both UNSUPPORTED until 1.22.0; sed -i is now parsed above.)
#   * in every session, gated on the repository that OWNS each target (1.22.0 fix round 2,
#     seam S8) -- no longer on the session's own project, so a session rooted outside every
#     coordinated repository that writes into one is refused at the write, not only at its
#     `git commit`.
#
# WHICH repository decides (v1.21.2): the one that owns the write -- for an
# Edit/Write, `git -C <dirname of the path> rev-parse --show-toplevel`; for a
# commit, the repository resolved from -C / cd. That repository's own
# .claude/agent-sync.json absent -> allowed, silently; present -> the check runs
# FROM its toplevel, against its guardedFiles and its leases. A path inside no git
# repository is allowed. The session's project is not consulted: a session rooted
# in an unconfigured project is still guarded when it writes into a configured
# one, and a configured session no longer blocks commits in repositories where
# coordination is off.
#
#   parser cannot run (no python3), session configured     FAIL CLOSED (exit 2)
#   parser cannot run, session unconfigured                UNENFORCED (exit 0) --
#     the owning repository cannot be named without the parser, and refusing every
#     call on a machine that never opted in is not a guard; the coordinator cannot
#     run there either
#   git missing from PATH: same two rows, same reasons
#
# An UNSUPPORTED vector is not silently trusted — it is DECLARED unenforced.
# A caller that needs a hard guarantee routes file writes through a trusted
# mutation API, an isolated worktree, or OS controls with resource locks; a
# regex shell parser is not a universal sandbox and this guard never claims to
# be one. The late staged-path check on a commit cannot recover a working tree
# already overwritten by an unsupported vector before staging.
# --------------------------------------------------------------------------
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
# No early `agent_sync_configured || exit 0` here, on purpose: that asks the SESSION's
# project, and the guard's question is about the repository the write lands in. See
# agent_sync_owner in _lib.sh and the matrix above.
input=$(cat)
# The payload's session id, for a coordinator that would otherwise walk to a stamp this
# repository does not have (AS-07). The environment, when it names a session, still wins.
agent_sync_export_session "$input"

# The parser IS the guard: hooks.json carries no `if` filter (the one it declared until
# v1.20.1 sat at the group level, where Claude Code never evaluated it, and a handler-level
# one would skip `git -C`/env/compound commits), so nothing upstream compensates for a
# parser that cannot run. A python3
# missing from PATH used to leave `path` empty, default `is_commit` to 0 and exit 0 —
# the one machine state that disables the parser silently disabled the guard (ASY-07).
parser_or_die() {
  local rc="$1"
  [ "$rc" -eq 0 ] && return 0
  # Without the parser the owning repository cannot be named. Refuse only where this
  # session opted in; elsewhere the coordinator could not run either (matrix above).
  agent_sync_configured || exit 0
  echo "agent-sync: the guard could not run its parser (python3 exit $rc — likely missing from PATH). Failing closed. Install python3 or fix PATH and retry, or remove .claude/agent-sync.json to switch coordination off here." >&2
  exit 2
}

# A Bash call carries no file path: say so without an interpreter, so the commonest call in
# every session — a Bash command that writes nothing and commits nothing — spawns no process
# at all (seam S5). The pattern cannot match inside a JSON string, where quotes are escaped.
bash_re='"tool_name"[[:space:]]*:[[:space:]]*"Bash"'
if [[ $input =~ $bash_re ]]; then
  path=""
else
  path=$(printf '%s' "$input" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti=d.get("tool_input") or {}
print(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or "")
' 2>/dev/null)
  parser_or_die $?
fi

# The owning repository is resolved with git, so git is part of the parser.
if ! command -v git >/dev/null 2>&1; then
  agent_sync_configured || exit 0
  echo "agent-sync: the guard cannot resolve which repository this call writes to (git is missing from PATH). Failing closed. Install git or fix PATH and retry." >&2
  exit 2
fi

# Forward the coordinator's one-line notice (R19's held-too-long warning, at most once per
# lease per run) as the host's `systemMessage`. Pure bash: no process spent on the common path.
forward_notice() {
  case "$1" in
    *"AGENT_SYNC_NOTICE "*)
      local n="${1#*AGENT_SYNC_NOTICE }"
      printf '%s\n' "${n%%$'\n'*}"
      ;;
  esac
}

# A Bash call: its writes, then its commit.
if [ -z "$path" ]; then
  # AS-09: a Bash write — `>`/`>>`/`<>`, `tee`, `sed -i`, `cp`, `mv` — into a guarded file.
  # Gated on the repository that OWNS each target, never on the session's project (seam S8):
  # a session rooted outside every coordinated repository that `cd`s into one is checked as
  # well. bash_writes.py resolves owners by walking to `.git` and drops every target that no
  # guardedFiles pattern matches, all without spawning anything, so only a write that might be
  # refused reaches the coordinator — once per owning repository, with every target (seam S5).
  # The shell test is cheap and loose on purpose: a `>` anywhere, or tee/sed/cp/mv as a WORD
  # (not inside `used` or `guarantee`). A JSON `\n` or `\t` before the word counts as a break.
  shape=0
  case "$input" in *'>'*) shape=1 ;; esac
  if [ "$shape" = 0 ]; then
    word_re='(^|[^A-Za-z0-9_.-]|\\[nt])(tee|sed|cp|mv)([^A-Za-z0-9_.-]|$)'
    [[ $input =~ $word_re ]] && shape=1
  fi
  if [ "$shape" = 1 ]; then
    hits=$(printf '%s' "$input" | python3 "${S%/*}/bash_writes.py" --guarded 2>/dev/null)
    parser_or_die $?
    if [ -n "$hits" ]; then
      owners=""
      while IFS=$'\t' read -r o _t; do
        [ -n "$o" ] || continue
        case $'\n'"$owners"$'\n' in *$'\n'"$o"$'\n'*) ;; *) owners="$owners${owners:+$'\n'}$o" ;; esac
      done < <(printf '%s\n' "$hits")
      while IFS= read -r owner; do
        [ -n "$owner" ] || continue
        targets=()
        while IFS=$'\t' read -r o t; do
          [ "$o" = "$owner" ] && targets+=("$t")
        done < <(printf '%s\n' "$hits")
        if ! out=$(cd "$owner" && python3 "$S" guard -- "${targets[@]}" 2>&1); then
          echo "agent-sync: this command writes a guarded file in $owner and this run holds no lease on it. Acquire one there (cd $owner && agent_sync.py acquire <TASK-ID>) first. ${out}" >&2
          exit 2
        fi
        forward_notice "$out"
      done < <(printf '%s\n' "$owners")
    fi
  fi
  # The tokeniser accepts a commit only as the literal word `commit`, and the host serialises
  # the payload as JSON without escaping ASCII letters, so a payload that lacks the substring
  # carries no commit. One interpreter start saved per Bash call.
  case "$input" in *commit*) ;; *) exit 0 ;; esac
  # What repository, and is this even a commit. Both used to be wrong, and the second one is why
  # the first went unnoticed: the old test was `case "$cmd" in *"git commit"*)`, a CONTIGUOUS
  # substring. `git -C <dir> commit` does not contain it, so every commit made that way skipped the
  # guard entirely -- in any repository, submodule or not. The repo was then hardcoded to
  # CLAUDE_PROJECT_DIR, so even a bare `git commit` inside a submodule read the umbrella's empty
  # index and passed. Measured 2026-08-07: a full day of commits to guarded registers, with the
  # Edit-tool half of this hook refusing correctly the whole time, so the protection looked present.
  #
  # Tokenised by bash_writes.py --commit (the same tokeniser as the writes above, so quoting,
  # `cd`, subshells and prefixes read one way): `git log --grep=commit` is not a commit, and
  # `git -c user.name=x -C dir commit` is. Its first line is `<is_commit> <repo>`; every later
  # line is a file the SAME command stages before committing (AS-16) -- `git add X && git
  # commit` reached the commit with X not yet in the index this hook reads, and passed.
  parsed=$(printf '%s' "$input" | python3 "${S%/*}/bash_writes.py" --commit 2>/dev/null)
  parser_or_die $?
  first="${parsed%%$'\n'*}"
  is_commit="${first%% *}"; repo="${first#* }"
  [ -n "${is_commit:-}" ] || is_commit=0
  [ -n "${repo:-}" ] || repo="."
  [ -d "$repo" ] || repo="${CLAUDE_PROJECT_DIR:-$PWD}"

  if [ "$is_commit" = "1" ]; then
    # The guard runs FROM that repository's toplevel, not merely against its file list:
    # agent_sync.py resolves the project from `git rev-parse --show-toplevel` of its cwd, so a
    # submodule gets its own .claude/agent-sync.json and its own guardedFiles -- the only
    # reading under which "docs/ROADMAP.md" means the right file in each repo. A repository
    # with no config of its own is not coordinated, whatever the session's project says.
    owner=$(agent_sync_owner "$repo") || exit 0
    files=()
    while IFS= read -r staged; do
      [ -n "$staged" ] && files+=("$staged")
    done < <(git -C "$owner" diff --cached --name-only 2>/dev/null)
    case "$parsed" in
      *$'\n'*)
        while IFS= read -r extra; do
          case "$extra" in "$owner"/*) files+=("$extra") ;; esac
        done < <(printf '%s\n' "${parsed#*$'\n'}")
        ;;
    esac
    if [ "${#files[@]}" -gt 0 ]; then
      if ! why=$(cd "$owner" && python3 "$S" guard -- "${files[@]}" 2>&1 >/dev/null); then
        echo "agent-sync: this commit in $owner stages a guarded file this run holds no lease on (staged already, or by this same command). Acquire one there (cd $owner && agent_sync.py acquire <TASK-ID>), or unstage it. ${why}" >&2
        exit 2
      fi
    fi
  fi
  exit 0
fi

# An Edit/Write: the repository that contains the file decides. A relative path is relative
# to this hook's cwd, which is where the tool call resolves it too.
case "$path" in /*) ;; *) path="$PWD/$path" ;; esac
owner=$(agent_sync_owner "$(dirname "$path")") || exit 0

if out=$(cd "$owner" && python3 "$S" guard -- "$path" 2>&1); then
  forward_notice "$out"
  exit 0
else
  code=$?
  if [ "$code" -eq 2 ]; then
    echo "$out" >&2
    exit 2
  fi
  echo "agent-sync guard failed to run ($code): $out — diagnose with \`python3 $S check\`, or acquire a lease first: \`python3 $S acquire <TASK-ID>\`" >&2
  exit 2
fi
