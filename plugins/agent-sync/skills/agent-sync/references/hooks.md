# Claude Code hooks

**Read this when** installing, debugging or removing the enforcement hooks.

## Contents

- The limit, first
- Contract
- Installed hooks
- Performance
- Debugging
- Removing them

## The limit, first

**This package ships a Claude Code hook adapter.** Other hosts can provide hooks,
but their event names, payloads, registration and refusal semantics vary by version.
A skills-directory install does not register this adapter. Detect the active host
and installed adapter; require a planted refusal before claiming enforcement.
Without that evidence, run `guard` explicitly and disclose runtime enforcement
as unverified separately from the lease mode.

The board's `gated` / `ungated` column describes lease arbitration: configured
gating, a cross-machine Git lease and a reachable backend determine it. It does
not prove that a hook ran or refused an edit. Record adapter registration and a
planted refusal separately; neither the board nor host hook support proves runtime
protection. See `scripts/agent_sync.py`: `Store.capabilities` and `Store.guard`.

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
      { "matcher": "Bash",
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

The `Bash` group has no `if` filter, and that is deliberate. Until v1.20.1 it declared
`"if": "Bash(git commit *)"` beside `matcher` — a key Claude Code does not know at
the group level, so it was never evaluated (and 2.1.270 started saying so at every
session start). Moving it into the handler would make it real, and a real one skips
`git -C <dir> commit`, `env X=1 git commit` and `cd d && git commit` — the forms the
parser in `guard.sh` was written to cover. So `guard.sh` runs on every Bash call, and
the narrowing is its own, in two steps (1.22.0):

1. **A Bash write (AS-09).** Only where the session's project is coordinated, or this
   session holds leases in another repository, and only when the payload carries a write
   shape (`>`, `tee`, `sed`, `cp `, `mv `), it starts `scripts/bash_writes.py` — the small
   parser, not the coordinator — and asks `guard` about each target it prints. It
   tokenises first and segments second, so a quoted `;` or a heredoc's body is never
   read as a command. The shapes it reads and the ones it skips are listed in the
   capability matrix at the top of `guard.sh`; each is a case in
   `test/coordination_test.py`.
2. **A commit.** A payload without the substring `commit` exits 0 there without
   starting another interpreter; otherwise the commit parser decides.

**Which repository decides (since v1.21.2): the one that owns the write.** For
`Edit`/`Write`/`MultiEdit`/`NotebookEdit` that is `git -C <dirname of the path>
rev-parse --show-toplevel` (the nearest existing ancestor, since a `Write` may create
its directory); for a commit, the repository named by `-C` or `cd`. Its own
`.claude/agent-sync.json` absent → exit 0, silently; present → `agent_sync.py guard`
runs from that toplevel, so its `guardedFiles` and its leases apply. A path inside no
git repository is allowed. The session's project is **not** consulted: before 1.21.2 it
was, so a configured session blocked commits in repositories without a config (the
coordinator there exits 2 for "no config", read as "no lease"), judged another
repository's files by the session's globs, and a session rooted in an unconfigured
project guarded nothing anywhere. The guard still fails closed when its parser (python3)
or git cannot run, but only in a session whose own project is configured — elsewhere the
owning repository cannot be named without them, and the hook says so in its matrix.

The lifecycle hooks below act on the session's project **and, since 1.22.0, on every
other repository this session took a lease in (AS-07).** `acquire` lists the repository
under the SESSION — `~/.agent-sync/session-repos/<sha256 of the session key>.json`, the key
being `env:<AGENT_SYNC_RUN_ID>` or `session:<id>` from `CLAUDE_SESSION_ID`,
`CLAUDE_CODE_SESSION_ID` (what a Bash call carries) or the hook payload's `session_id`.
`renew.sh` and `session-end.sh` pass `--session-repos`, which starts one child process per
listed repository, in parallel, from the environment the hook started with — so each
reads its own `.env.agent-sync` and vault slot — inside the hook's budget (2 s at
SessionEnd, for all of them). A repository where this run then holds nothing leaves the
list. Two sessions in one project keep two lists. A file test (`[ -f ]` on that hash)
decides whether a session with no config of its own runs the lifecycle hooks at all, so
a machine where no session ever left its project pays nothing.

| Hook | Job |
|---|---|
| `session-start.sh` | Register the run, print the board summary and the one next action |
| `guard.sh` | Deny an edit to a `guardedFiles[]` path, or a commit staging one, without a live lease — judged by the repository that owns the file |
| `renew.sh` | Renew the lease — moves the timestamp expiry is computed from, throttled to `renewIntervalSeconds`, a no-op most calls; here and in this session's other listed repositories |
| `session-end.sh` | Release every lease this run holds, here and in this session's other listed repositories. That is all it does — it writes no journal entry and closes nothing else |

## Performance

`renew.sh` runs after **every** tool call, asynchronously. It must be a no-op in the
common case: it reads one timestamp file and returns. A session with leases in other
repositories also starts one child process per listed repository, each the same
throttled no-op until a renewal is due. It touches the network at most once per
`renewIntervalSeconds` (default 300 s) per repository. With the `notion` backend and a
vault slot, a renewal that is due re-runs under `use_secret.py run` after a no-op probe
of it — the cost of keeping the token out of a plaintext file. If it ever becomes slower than that, the
throttle is broken — fix the throttle rather than removing the hook.

## Debugging

| Symptom | Cause |
|---|---|
| Guarded edits go through | The guard crashed. Any exit code other than 2 is non-blocking. Run it by hand with a sample stdin payload |
| Everything is denied | No lease in the repository that owns the file. Run `status` from that repository's root, not the session's |
| A commit into a repository without a config is blocked | Fixed in 1.21.2 — update the plugin; earlier versions asked the session's config and ran the check in the target repository |
| Session start is slow | The backend is unreachable. Each hook is capped twice — `run_limited 10` inside the script, and the `timeout` in `hooks.json` (15–20 s) — so it degrades rather than hanging |
| Renew floods the log | The throttle file is not being written — check its path is writable |

Run the guard directly to see what it decides:

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"docs/DECISIONS.md"},"cwd":"'"$PWD"'"}' \
  | "$CLAUDE_PLUGIN_ROOT/hooks/guard.sh"; echo "exit=$?"
```

## Removing them

**Not** by editing `.claude/settings.json` — nothing here ever writes a `hooks`
block there, and Claude Code has no per-hook disable for a hook a plugin ships. A
reader who follows that instruction edits a file with no such block and concludes
the removal worked while every hook keeps firing.

Two levers actually work:

- `enabledPlugins["agent-sync@agent-sync"] = false` in `~/.claude/settings.json` —
  enablement is the only switch a plugin hook has; or
- `claude plugin uninstall agent-sync@agent-sync`.

And one you usually do not need: every hook already self-disables in a project with
no `.claude/agent-sync.json` (`hooks/_lib.sh`), so a repository that never opted in
is not paying for them. The skill keeps working either way — every guard is also
available as a command, and the board simply records runs as `ungated` from then on.
