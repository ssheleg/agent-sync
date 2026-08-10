# Claude Code hooks

**Read this when** installing, debugging or removing the enforcement hooks.

## The limit, first

**Hooks exist only in Claude Code.** On Cursor, Codex and the other agents the
skills CLI serves there is no `PreToolUse`, so nothing blocks a guarded edit. On
those agents the same checks run as a self-check written into the skill body, and
the run is recorded on the board as `ungated`.

Never describe a project as protected when its agents run outside Claude Code. The
board's `gated` / `ungated` column exists precisely so that an operator can tell an
enforced run from a promised one.

## Contract

Verified against the Claude Code hooks reference, 2026-07-29.

A `PreToolUse` hook blocks a call in either of two ways:

- **exit 2**, with the reason on **stderr** (stdout is ignored); or
- **exit 0** with this on stdout:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse",
 "permissionDecision":"deny",
 "permissionDecisionReason":"agent-sync: docs/DECISIONS.md is held by run r-7f3a91"}}
```

Any other exit code is a non-blocking error: execution continues and stderr is shown
in the transcript. So **a crashing guard fails open** — write the guard to exit 2 on
its own internal errors, or it silently stops guarding.

The hook receives JSON on stdin with `session_id`, `prompt_id`, `transcript_path`,
`cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input` and
`tool_use_id`.

## Installed hooks

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" }] }
    ],
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" }] },
      { "matcher": "Bash", "if": "Bash(git commit *)",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" }] }
    ],
    "PostToolUse": [
      { "matcher": "*",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/renew.sh" }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-end.sh" }] }
    ]
  }
}
```

| Hook | Job |
|---|---|
| `session-start.sh` | Register the run, print the board summary and the one next action |
| `guard.sh` | Deny an edit to a `guardedFiles[]` path, or a commit staging one, without a live lease |
| `renew.sh` | Renew the lease — moves the timestamp expiry is computed from, throttled to `renewIntervalSeconds`, a no-op most calls |
| `session-end.sh` | Release every lease this run holds. That is all it does — it writes no journal entry and closes nothing else |

## Performance

`renew.sh` runs after **every** tool call. It must be a no-op in the common case:
it reads one timestamp file and returns. It touches the network at most once per
`renewIntervalSeconds` (default 300 s). If it ever becomes slower than that, the
throttle is broken — fix the throttle rather than removing the hook.

## Debugging

| Symptom | Cause |
|---|---|
| Guarded edits go through | The guard crashed. Any exit code other than 2 is non-blocking. Run it by hand with a sample stdin payload |
| Everything is denied | No config, or no lease. `status` says which |
| Session start is slow | The backend is unreachable. Each hook is capped twice — `run_limited 10` inside the script, and the `timeout` in `hooks.json` (15–20 s) — so it degrades rather than hanging |
| Renew floods the log | The throttle file is not being written — check its path is writable |

Run the guard directly to see what it decides:

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"docs/DECISIONS.md"},"cwd":"'"$PWD"'"}' \
  | "$CLAUDE_PLUGIN_ROOT/hooks/guard.sh"; echo "exit=$?"
```

## Removing them

Delete the `hooks` block from the project's `.claude/settings.json`. The skill keeps
working — every guard is also available as a command, and the board simply records
runs as `ungated` from then on.
