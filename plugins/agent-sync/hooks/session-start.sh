#!/usr/bin/env bash
# Stamp the session, then say what THIS run must act on: one line, or nothing (AS-25).
#
# Runs on startup, resume and clear, because each of them can start a new session in a
# process whose stamp is stale (ASY-08); it PRINTS on startup only, since a resumed or
# cleared session already has the line in its earlier context. The full `status` block
# (834 characters in the hub checkout, measured 2026-10-20, ending in a "NEXT: acquire"
# the routing block already says) is behind AGENT_SYNC_SESSION_VERBOSE=1 and the
# `status` command.
set -uo pipefail
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
agent_sync_configured || exit 0

# Stamp who this session is, keyed by the process every command in it descends from.
# Without this a second session in the same checkout adopts the first one's identity: both
# acquire and release as one run, and the lease stops separating the exact case it exists for.
# $PPID here is the CLI process, which is the one ancestor every later command shares.
#
# The id arrives on STDIN as JSON, the way every other hook here reads its payload -- see
# guard.sh. This block used to require CLAUDE_SESSION_ID in the ENVIRONMENT and nothing else,
# so on this machine it never ran once: measured 2026-08-25, `.agent-sync/sessions` had never
# been created and the run-id map held a single `shared` key, which is the weak identity that
# makes an expired lease unattributable and `reap` refuse it forever. A fallback that is always
# taken is not a fallback.
if [ -t 0 ]; then
  payload=""                       # no stdin (invoked by hand) -- do not block on `cat`
else
  payload=$(cat 2>/dev/null || true)
fi
# Read with bash, not an interpreter: a resume or a /clear only stamps, and pays no python.
sid="${CLAUDE_SESSION_ID:-}"
[ -n "$sid" ] || sid=$(agent_sync_payload_session "$payload")
source=""
src_re='"source"[[:space:]]*:[[:space:]]*"([^"]*)"'
[[ $payload =~ $src_re ]] && source="${BASH_REMATCH[1]}"
if [ -n "$sid" ]; then
  d="$(git rev-parse --show-toplevel 2>/dev/null)/.agent-sync/sessions"
  if mkdir -p "$d" 2>/dev/null; then
    printf '%s' "$sid" > "$d/$PPID" 2>/dev/null || true
    # forget the stamps of processes that are gone, so the directory cannot grow without bound
    for f in "$d"/*; do
      b="$(basename "$f")"
      case "$b" in *[!0-9]*) continue ;; esac
      kill -0 "$b" 2>/dev/null || rm -f "$f"
    done
  fi
fi

case "$source" in resume|clear|compact) exit 0 ;; esac
if [ "${AGENT_SYNC_SESSION_VERBOSE:-}" = "1" ]; then
  run_limited 10 python3 "$S" status 2>&1 || true
else
  # Fail silent: a traceback is not an action, and stderr is not the brief's channel.
  run_limited 5 python3 "$S" status --brief 2>/dev/null || true
fi
exit 0
