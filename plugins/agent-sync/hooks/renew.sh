#!/usr/bin/env bash
# Runs after every tool call, so it must be a no-op in the common case:
# the script itself throttles on a timestamp file and touches the network at
# most once per renewIntervalSeconds.
set -uo pipefail
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
# The payload names the session (AS-07); a terminal invocation has none to read.
if [ -t 0 ]; then payload=""; else payload=$(cat 2>/dev/null || true); fi
agent_sync_configured || agent_sync_has_session_repos "$payload" || exit 0
agent_sync_export_session "$payload"
run_limited 10 python3 "$S" renew --session-repos >/dev/null 2>&1 || true
exit 0
