#!/usr/bin/env bash
# Runs after every tool call, so it must be a no-op in the common case:
# the script itself throttles on a timestamp file and touches the network at
# most once per renewIntervalSeconds.
set -uo pipefail
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
# The payload names the session (AS-07) -- but a PostToolUse payload carries the tool's whole
# RESPONSE, and reading a 2 MB Read result to find one id cost 64-149 ms per tool call in every
# session (seam S6). So: decide without stdin wherever possible, and when the id must come from
# the payload, read only its head -- the host writes `session_id` first.
if ! agent_sync_configured; then
  [ -d "$HOME/.agent-sync/session-repos" ] || exit 0       # no session ever left its project
fi
payload=""
if [ -z "${CLAUDE_SESSION_ID:-}${CLAUDE_CODE_SESSION_ID:-}${AGENT_SYNC_RUN_ID:-}" ] && [ ! -t 0 ]; then
  payload=$(head -c 4096 2>/dev/null || true)
fi
agent_sync_configured || agent_sync_has_session_repos "$payload" || exit 0
agent_sync_export_session "$payload"
run_limited 10 python3 "$S" renew --session-repos >/dev/null 2>&1 || true
exit 0
