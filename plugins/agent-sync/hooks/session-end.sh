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
agent_sync_configured || exit 0
run_limited 2 python3 "$S" release --held >/dev/null 2>&1 || true
exit 0
