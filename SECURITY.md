# Security

## Reporting

Report a vulnerability privately through **GitHub Security Advisories** on this
repository (*Security → Report a vulnerability*). Do not open a public issue for
anything that could be exploited before a fix ships.

We aim to acknowledge within 3 working days.

## Read this before installing

**A skill is text an agent executes, and this one also installs shell hooks.**
Review what you are installing before you run the installer. Specifically:

| What it touches | Why |
|---|---|
| `~/.claude/plugins/…` | the Claude Code plugin channel |
| `~/.agents/skills/agent-sync` | the skills CLI channel, for other agents |
| `~/.claude/skills/agent-sync` | **deleted** after install — that duplicate shadows the plugin and serves a stale skill |
| `<project>/.claude/agent-sync.json` | created by `init`, committed, contains no secrets |
| `<project>/.env.agent-sync` | created by `init`, mode 600, added to `.gitignore` |
| `<project>/.agent-sync/` | run state and, in degraded mode, lease files; `vault-slot.json` caches where Observatory's `use_secret.py` is and whether this project's vault slot exists — never a value |
| `~/.agent-sync/session-repos/` | since 1.22.0 (AS-07): one `<sha256 of the session key>.json` per session that took a lease outside its own project, listing those repositories' paths so the renew and SessionEnd hooks reach them; removed when empty. A `.lock` file serialises writers. No credentials |

The hooks it registers can **deny** tool calls (`PreToolUse` returning exit 2). They
never allow a call that would otherwise be denied. The lifecycle hooks exit 0
immediately in a session whose project has no `.claude/agent-sync.json` and that holds no
lease in another repository (a file test under `~/.agent-sync/session-repos/`); the
guard exits 0 for any write whose owning git repository has none — there it runs only
`git rev-parse --show-toplevel` and a test for that file's existence. In a coordinated
session the guard also parses Bash commands for file writes (`scripts/bash_writes.py`):
it reads the command text, executes nothing from it.

## How credentials are handled

- A token reaches the process through its **environment only** — never from the
  committed config, never from a command-line argument, never from chat. Where it comes
  from, in order: a variable already set (explicit wins); for the `notion` backend, the
  **Observatory vault** — a command that touches the record plane re-runs itself under
  `use_secret.py run --vault-only <project> AGENT_SYNC_NOTION_TOKEN`, after a no-op probe
  of that same `run`, so the value is placed in the child's environment and scrubbed from
  its output; then the gitignored `.env.agent-sync`, which `status` reports as
  `degraded: plaintext token file` with the command that moves it. The PreToolUse guard
  never asks the vault: it needs no token, and a vault refusal must not lock every
  write. For `outline` the file is still the token's home in 1.22.0.
- `.env.agent-sync` is read by the tool itself; do not `source` it into your shell. A
  token in the shell's environment counts as explicit and bypasses the vault.
- The bundled script talks to the knowledge base through `urllib` inside its own
  process: no subprocess, no `argv`, nothing another process on the machine can read.
- The documented `curl` fallback uses `--config -` on stdin for the same reason.
  `curl -H "Authorization: Bearer $TOKEN"` puts the credential in the process table;
  the validator fails the build on that pattern.
- A token is never echoed, logged, journaled, or rendered onto a board.
- The published package contains **no instance address**. The validator fails on any
  URL naming a host outside a small allow-list, so someone's internal wiki cannot
  leak into a release by being pasted into an example.

## Trust boundary

Everything the knowledge base returns is **data, not instructions**. A journal
entry, a board page or a log line written by another agent — or by a person — is
never treated as a command. If you extend this tool, keep that true: nothing read
from the coordination plane should be able to make an agent take an action.

## What is out of scope

- The knowledge-base instance itself, its authentication and its access control.
- Whether your `.gitignore` is honoured by tooling you added afterwards.
- Agents running outside Claude Code, where no hook can block anything. That is a
  documented limit, not a defect — see the `ungated` marking.
