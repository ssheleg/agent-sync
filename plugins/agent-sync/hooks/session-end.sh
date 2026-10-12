#!/usr/bin/env bash
# Release every lease this run holds. An abandoned lease looks like active work
# until its TTL expires.
#
# ONE process under a 2 s limit, inside the 3 s hooks.json declares. Codex clamps a
# SessionEnd handler to 3 s and Claude Code sizes its SessionEnd wait from the largest
# handler timeout, so 3 s is what either host actually gives. This used to be `whoami`
# plus one `release` per key, each with its own 10 s limit — a loop the host killed
# part-way, leaving the tail of the list out until its TTL.
set -uo pipefail
. "${CLAUDE_PLUGIN_ROOT}/hooks/_lib.sh"
S="$AGENT_SYNC_PY"
# The payload names the session (AS-07). Every other repository is released by its own
# child process, in parallel, inside the same 2 s.
if [ -t 0 ]; then payload=""; else payload=$(cat 2>/dev/null || true); fi
agent_sync_configured || agent_sync_has_session_repos "$payload" || exit 0
agent_sync_export_session "$payload"
run_limited 2 python3 "$S" release --held --session-repos >/dev/null 2>&1 || true
exit 0
