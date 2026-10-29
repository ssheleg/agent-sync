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
      { "matcher": "startup|resume|clear",
        "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" }] }
    ],
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit|apply_patch",
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

0. **No interpreter for a Bash call that writes nothing and commits nothing.** The hook
   recognises a Bash payload by its `tool_name` without starting python (a Bash call has
   no file path to extract), so `ls` costs a bash start and nothing else.
1. **A Bash write (AS-09).** In EVERY session — gated on the repository that owns each
   target, not on the session's project (fix round 2, seam S8) — and only when the payload
   carries a write shape (a `>` anywhere, or `tee`/`sed`/`cp`/`mv` as a word, so `used` and
   `guarantee` do not match), it starts `scripts/bash_writes.py --guarded` — the small
   parser, not the coordinator. That resolves each target's repository by walking to its
   `.git`, drops every target no `guardedFiles` pattern of that repository matches, and
   prints the rest; the hook then asks `guard` ONCE per owning repository with all of its
   targets. It tokenises first and segments second, so a quoted `;` or a heredoc's body is
   never read as a command. The shapes it reads and the ones it skips are listed in the
   capability matrix at the top of `guard.sh`; each is a case in
   `test/coordination_test.py`.
2. **A commit.** A payload without the substring `commit` exits 0 there without
   starting another interpreter; otherwise `bash_writes.py --commit` decides — with the
   same tokeniser — whether it is a commit, in which repository, and which files the SAME
   command stages before it (`git add <specs>`/`-A`/`-u`/`.`, `git commit -a`,
   `git commit <paths>`, resolved by `git ls-files`). Those files and the index are
   checked together, in one `guard` call (AS-16): the hook runs before the command, so
   `git add X && git commit` used to reach the commit with X not yet in the index.
   When the command cannot be tokenised (an odd-quote heredoc, AS-18), the command text
   is what remains after joining backslash-newline continuations and removing each
   balanced `$(…)` and backtick span, each heredoc body (from the `<<DELIM` line through
   the line that is exactly DELIM) and each comment; an opener that never closes ends it
   there. A `git … commit` at a segment start of that text counts as a commit in the
   repository its `-C` / `cd` names (else the payload's directory; a `cd "$(…)"` is
   unknown, so the payload's directory); only that index is checked, and what the same
   command stages is unknown. Text inside a heredoc body or a quoted argument (a PR body
   saying "git commit") is not a commit.

When `guard` allows a write while this run holds a lease longer than the repository's
`scheduledIntervalSeconds`, the hook prints R19's warning once per lease per run (a marker
under `.agent-sync/warned/`) as ONE JSON object: `systemMessage` for the person and
`hookSpecificOutput.additionalContext` for the model — Claude Code shows a `systemMessage`
to the person only. A Bash write into several repositories merges their notices into that
one object: the host reads two JSON lines as a parse failure and drops both.

**Codex's `apply_patch` (1.23.3, hub B-211).** Codex edits files with one tool,
`apply_patch`, whose payload carries no `file_path`: the patch text is in
`tool_input.command` (or `tool_input.input`). Until 1.23.3 the matcher did not name it,
and an empty path would have sent the payload into the Bash branch, where the patch was
parsed as a shell command. Now the matcher names it, and `guard.sh` reads every
`*** Add File:`, `*** Update File:`, `*** Delete File:` and `*** Move to:` path out of the
patch (relative paths against the payload's `cwd`) and checks each one exactly like an
`Edit`; the patch body is data, never a command. A payload that does not parse names no
path and passes silently. Cases: the `apply_patch` rows of `GUARD_SHAPES` in
`test/validate.py`.

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
or git cannot run — and when `bash_writes.py` itself fails — but only in a session whose
own project is configured: elsewhere the owning repository cannot be named without them,
and the hook says so in its matrix.

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
| `session-start.sh` | Stamp the session (startup, resume, clear). On startup only, print `status --brief`: one line of what this run must act on, or nothing; `AGENT_SYNC_SESSION_VERBOSE=1` prints the full `status` |
| `guard.sh` | Deny an edit to a `guardedFiles[]` path, or a commit staging one, without a live lease — judged by the repository that owns the file |
| `renew.sh` | Run `renew --active-only --session-repos`: refresh only keys with recent guarded activity or explicit acquisition/renewal, throttled per key; unrelated calls never reset activity |
| `session-end.sh` | Release every lease this run holds, here and in this session's other listed repositories. That is all it does — it writes no journal entry and closes nothing else |

## Performance

`renew.sh` runs after **every** tool call, asynchronously. It must be a no-op in the
common case: it reads per-key activity/throttle markers and returns. Its payload carries the tool's whole
response, so it decides WITHOUT reading stdin wherever it can — an unconfigured project
with no `~/.agent-sync/session-repos` exits at once — and otherwise reads only the first
4 KB, where the host writes `session_id` (fix round 2, seam S6: a 2 MB Read result cost
89 ms per tool call before, 8 ms after). A session with leases in other repositories also
starts one child process per listed repository, each the same throttled no-op until a
renewal is due and activity is recent. Activity expires after two renewal intervals;
the heartbeat never stamps activity itself. Acquisition and explicit manual renewal
stamp that key; an allowed guarded write stamps its resource and its unambiguous
task. Multiple unbound tasks are not all kept alive: release stale tasks or use
explicit named renewal. Missing/corrupt legacy activity is unknown and is not
automatically renewed; a fresh guard or explicit renewal establishes it. The
existing lease TTL and arbitration still decide ownership, so inactivity neither
deletes claims nor authorizes a write after expiry. See `lease-protocol.md`.
Its 10 s limit is armed inside `agent_sync.py` (1.23.5, hub B-311): the hook sets
`AGENT_SYNC_TIME_LIMIT=10`, and the script starts a real-time timer whose SIGALRM keeps its
default action, so a renew that hangs still ends at 10 s. It used to run under
`run_limited 10`, whose fallback on a stock macOS starts `sleep 0.1` every 100 ms while
Python runs; per tool call in a coordinated project that is one bash and one python3 now
(`test/hooks_session_test.py`: "renew starts one python3 and no watchdog", "a stalled renew
ends within its 10 s bound").
It touches the network at most once per `renewIntervalSeconds` (default
300 s) per repository. `renew` and SessionEnd's `release --held` never ask the Observatory
vault: the lease they act on is a local lock or a git ref, and the token would add only a
visibility line in the Notion claims log (published whenever the token is in the file).
If it ever becomes slower than that, the throttle is broken — fix the throttle rather than
removing the hook.

`session-start.sh` (1.23.0, AS-25) reads the session id and the payload's `source` with
bash, so a `resume` or a `/clear` stamps the session and starts no interpreter. On
`startup` it runs `status --brief`, which reads local state only: the lock directory,
this run's lease notes and the claim-tag files. It never reads the record plane, the
remote or the vault, and it leaves the signal watermark alone, so the next `status`
still shows what is new. It prints one line, or nothing when nothing is this run's to
act on; its stderr is dropped.

The guard (1.23.0, AS-26) answers inside a budget. The first write of a guarded file
takes that file's claim, and where the claim needs a remote (a git lease, or a cloud
record plane) it runs in a forked, detached process; the guard waits for it until
`AGENT_SYNC_GUARD_BUDGET` seconds from the coordinator's start (default 1.5; `0` waits
for the claim, as before). Past the budget the write proceeds under the task lease. The
claim process then leaves this run's local note (the next write is decided locally) or a
record of the run holding the file under `.agent-sync/claiming/` (the next write is
refused from it, without a round-trip, for 60 s). A second write of the same file while
the claim is in flight waits for that claim rather than racing it. Measured 2026-10-20
against github.com: 8.6 to 10.9 s per first write before, 4.0 to 5.2 s after on a machine
at load average ~500, where the rest was local file I/O. On a host without `fork`, and
for a local lease on a local record plane, the claim stays synchronous.

Hooks cache Python bytecode under `${XDG_CACHE_HOME:-~/.cache}/agent-sync/pycache`
(`PYTHONPYCACHEPREFIX`), never beside the shipped scripts (ASY-01); with no HOME they
write none.

## Debugging

| Symptom | Cause |
|---|---|
| Guarded edits go through | The guard crashed. Any exit code other than 2 is non-blocking. Run it by hand with a sample stdin payload |
| Everything is denied | No lease in the repository that owns the file. Run `status` from that repository's root, not the session's |
| A commit into a repository without a config is blocked | Fixed in 1.21.2 — update the plugin; earlier versions asked the session's config and ran the check in the target repository |
| Session start is slow | Since 1.23.0 the start reads local state only; a slow start is the machine (python start, disk). `AGENT_SYNC_SESSION_VERBOSE=1` runs the full `status`, which reads the record plane and is capped by `run_limited 10` and the `hooks.json` timeout |
| Session start prints nothing | Nothing is this run's to act on. `agent_sync.py status` prints the full block |
| A guarded write went through before its file claim landed | The claim was past `AGENT_SYNC_GUARD_BUDGET`. If another run holds the file, the next write is refused. Set the budget to `0` to wait for every claim |
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
