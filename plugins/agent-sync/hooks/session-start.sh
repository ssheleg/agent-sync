#!/usr/bin/env bash
# Register the run and print the board summary plus one next action.
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
sid="${CLAUDE_SESSION_ID:-}"
if [ -z "$sid" ] && [ -n "$payload" ]; then
  sid=$(printf '%s' "$payload" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(d.get("session_id") or "")
' 2>/dev/null)
fi
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

run_limited 10 python3 "$S" status 2>&1 || true
exit 0
