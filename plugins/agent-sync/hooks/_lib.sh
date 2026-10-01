#!/usr/bin/env bash
# Shared helpers for the agent-sync hooks. Sourced, never executed.

# Every hook runs python3 against the shipped scripts/ directory, which people read
# as source, not as a build product. Bytecode caching buys nothing at this call rate
# and left scripts/__pycache__ regenerating forever (ASY-01).
export PYTHONDONTWRITEBYTECODE=1

# Run a command under a time limit, portably.
#
# `timeout` is GNU coreutils and is NOT on a stock macOS. Calling it directly made
# three of the four hooks die with "timeout: command not found" on every macOS
# session — which meant leases were never renewed and never released there, the exact
# abandoned-lease failure this tool exists to prevent. Homebrew installs it as
# `gtimeout`, and neither is guaranteed, so fall back to a plain-bash watchdog.
run_limited() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
    return $?
  fi
  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
    return $?
  fi

  # The watchdog POLLS in 0.1 s steps with its stdio on /dev/null, and nothing here
  # depends on a signal reaching it in time. It used to be `( sleep N; kill ) &` with
  # the caller's stdout inherited: `kill "$watchdog"` ended the subshell and not its
  # `sleep`, and the orphan held the pipe, so `$(run_limited 10 …)` or `… | sed` waited
  # the full limit after the command had finished. A trap-based stop still lost the
  # race now and then (the TERM landing inside the fork), so the stop is now "the
  # command is gone", seen within one step. session-end.sh spent 10.4 s releasing
  # three leases the old way, against a SessionEnd budget of 3 s.
  "$@" &
  local pid=$!
  (
    n=$((secs * 10))
    while [ "$n" -gt 0 ]; do
      kill -0 "$pid" 2>/dev/null || exit 0
      sleep 0.1
      n=$((n - 1))
    done
    kill -TERM "$pid" 2>/dev/null
  ) </dev/null >/dev/null 2>&1 &
  local watchdog=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null
  return "$rc"
}

# Every lifecycle hook (SessionStart, renew, SessionEnd) is a no-op in a session whose
# project does not use agent-sync, so installing the plugin globally changes nothing
# elsewhere. These hooks act on the SESSION's project and nothing else, so the session's
# config is the right question for them -- and the wrong one for the guard, which acts on
# whatever repository a write lands in (see agent_sync_owner below).
agent_sync_configured() {
  [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/agent-sync.json" ]
}

# The repository that OWNS a path decides whether a write to it is coordinated -- never the
# session's project. Prints that repository's toplevel when it carries
# .claude/agent-sync.json; prints nothing and returns 1 when the path is inside no git
# repository, or inside one where coordination is off.
#
# Until v1.21.2 guard.sh asked agent_sync_configured and then ran the check somewhere else:
# a commit into a repository with no config was blocked (the coordinator there exits 2 for
# "no config", read as "no lease"), an Edit in another repository was judged against the
# session's guardedFiles, and a session rooted in an unconfigured project guarded nothing
# at all. Accepts a file's directory or a repository directory; a Write may create the
# directory it lands in, so the lookup starts at the nearest ancestor that exists.
agent_sync_owner() {
  local dir="$1" top
  [ -n "$dir" ] || dir="."
  while [ ! -d "$dir" ]; do
    case "$dir" in
      */*) dir="${dir%/*}"; [ -n "$dir" ] || dir="/" ;;
      *) dir="." ;;
    esac
  done
  top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || return 1
  [ -n "$top" ] && [ -f "$top/.claude/agent-sync.json" ] || return 1
  printf '%s\n' "$top"
}

AGENT_SYNC_PY="${CLAUDE_PLUGIN_ROOT:-}/skills/agent-sync/scripts/agent_sync.py"
