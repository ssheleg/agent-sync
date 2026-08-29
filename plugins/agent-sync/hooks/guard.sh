#!/usr/bin/env bash
# PreToolUse guard. Exit 2 blocks the call and shows stderr as the reason.
# Any other non-zero code is NON-blocking in Claude Code, so every internal
# failure must also exit 2 — a crashing guard that fails open guards nothing.
set -uo pipefail
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
agent_sync_configured || exit 0
input=$(cat)

path=$(python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti=d.get("tool_input") or {}
print(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or "")
' <<<"$input" 2>/dev/null)

# git commit: check every staged path instead of a single file argument.
if [ -z "$path" ]; then
  # What repository, and is this even a commit. Both used to be wrong, and the second one is why
  # the first went unnoticed: the old test was `case "$cmd" in *"git commit"*)`, a CONTIGUOUS
  # substring. `git -C <dir> commit` does not contain it, so every commit made that way skipped the
  # guard entirely -- in any repository, submodule or not. The repo was then hardcoded to
  # CLAUDE_PROJECT_DIR, so even a bare `git commit` inside a submodule read the umbrella's empty
  # index and passed. Measured 2026-08-07: a full day of commits to guarded registers, with the
  # Edit-tool half of this hook refusing correctly the whole time, so the protection looked present.
  #
  # Tokenised in python rather than globbed in shell: `git log --grep=commit` must not match, and
  # `git -c user.name=x -C dir commit` must.
  read -r is_commit repo <<<"$(python3 -c '
import json, shlex, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("0 ."); sys.exit(0)
cmd = (d.get("tool_input") or {}).get("command", "")
is_commit, repo = 0, "."
# Each &&/;/||/|&/| segment is its own command; a commit anywhere in the chain counts.
# The single pipe was CLAIMED by this comment and never consumed (ASY-05, fixed 2026-08-29):
# only "||" was replaced, so `echo msg | git commit -F -` stayed one segment whose first
# token is `echo`, and the whole pipeline skipped the guard. Order matters: "||" and "|&"
# must be consumed before the bare "|", or each would be split into a stray half.
for seg in cmd.replace("&&", "\n").replace("||", "\n").replace("|&", "\n").replace(";", "\n").replace("|", "\n").split("\n"):
    try:
        toks = shlex.split(seg)
    except ValueError:
        continue
    if not toks:
        continue
    if toks[0] == "cd" and len(toks) > 1:
        repo = toks[1]
        continue
    if toks[0] != "git":
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
' <<<"$input" 2>/dev/null)"
  [ -n "${is_commit:-}" ] || is_commit=0
  [ -n "${repo:-}" ] || repo="."
  [ -d "$repo" ] || repo="${CLAUDE_PROJECT_DIR:-$PWD}"

  if [ "$is_commit" = "1" ]; then
    # The guard runs FROM that repository, not merely against its file list: agent_sync.py resolves
    # the project from `git rev-parse --show-toplevel` of its cwd, so a submodule gets its own
    # .claude/agent-sync.json and its own guardedFiles -- the only reading under which
    # "docs/ROADMAP.md" means the right file in each repo.
    while IFS= read -r staged; do
      [ -n "$staged" ] || continue
      if ! (cd "$repo" && python3 "$S" guard "$staged") >/dev/null 2>&1; then
        echo "agent-sync: '$staged' is staged in $repo and this run holds no lease on it. Acquire one, or unstage it." >&2
        exit 2
      fi
    done < <(git -C "$repo" diff --cached --name-only 2>/dev/null)
  fi
  exit 0
fi

if out=$(python3 "$S" guard "$path" 2>&1); then
  exit 0
else
  code=$?
  if [ "$code" -eq 2 ]; then
    echo "$out" >&2
    exit 2
  fi
  echo "agent-sync guard failed to run ($code): $out" >&2
  exit 2
fi
