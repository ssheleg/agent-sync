# agent-sync

[![CI](https://github.com/ssheleg/agent-sync/actions/workflows/validate.yml/badge.svg)](https://github.com/ssheleg/agent-sync/actions/workflows/validate.yml)
[![npm](https://img.shields.io/npm/v/%40ssheleg%2Fagent-sync)](https://www.npmjs.com/package/@ssheleg/agent-sync)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![site](https://img.shields.io/badge/docs-skills.sshlg.me-8ab0ff)](https://skills.sshlg.me/skills/agent-sync/)

**Coordinate concurrent coding agents with expiring claims, race-free ids and a durable run journal.**

```bash
npx skills add ssheleg/agent-sync
```

Ask: `Set up leases before three agents edit this repository.`

**[Detailed docs →](https://skills.sshlg.me/skills/agent-sync/)**

**[Docs, and every skill →](https://skills.sshlg.me/)** · [this skill's page](https://skills.sshlg.me/skills/agent-sync/) · [follow @sshlg93 on X](https://x.com/intent/follow?screen_name=sshlg93)

Loads in **DeepSeek Harness** (`dsh`) with **no plugin to write**: it reads the
Agent Skills standard directly, scanning `~/.agents/skills` — where `npx skills
add` puts this pack — at rank 500.

**Several coding agents, one repository, no collisions — and each one can see what the
others are doing.**

`agent-sync` is an agent skill (plus a Claude Code plugin) that gives concurrent
coding agents a coordination plane: leases with a TTL, race-free id reservation, a run
journal, a cross-repo signal feed and a generated board.

- [The problem](#the-problem)
- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Install](#install)
- [Update](#update)
- [Set up a project](#set-up-a-project)
- [Everyday use](#everyday-use)
- [Configuration](#configuration)
- [Backends](#backends)
- [Enforcement hooks](#enforcement-hooks)
- [Where it plugs into task-pipeline](#where-it-plugs-into-task-pipeline)
- [Limits, stated plainly](#limits-stated-plainly)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Develop and verify](#develop-and-verify)

## The problem

When more than one agent works a project at the same time, the coordination substrate
most teams already have — a decisions log, a roadmap, a board, per-repo task files —
stops being enough. Every one of those is a file edited by hand. That works for people
taking turns and fails for agents working at once:

| What goes wrong | Why |
|---|---|
| Two agents mint the same decision id | "Next free id" is a line in a file; reading it is not reserving it |
| A claim blocks a task forever | A role name is not a holder, and it has no expiry |
| Two agents start the same task | Git shows what was committed, never what is in flight |
| An agent is blocked but not informed | Knowing a task is taken is not knowing who has it or what they touch |
| Merge conflicts on every shared register | Everyone writes the same three files |
| A cross-repo dependency is never noticed | Filing one notifies nobody |

`agent-sync` closes exactly those, and nothing else.

## The idea

> **Git is the record plane. The cloud is the coordination plane.**

A fact that must survive is written to git first and referenced from the cloud. A fact
about *who is doing what right now* lives in the cloud and expires. No cloud object is
ever the only home of a durable fact, so your single-source-of-truth rules stay intact.

**The knowledge base never decides a lease.** It cannot: measured against a real
instance, twelve concurrent appends to one document returned twelve successes and left
three lines. Exclusion comes from something that genuinely has compare-and-swap — an
atomic file create on one machine (`leaseBackend: "local"`), or a pushed git ref across
machines (`leaseBackend: "git"`), where the remote's non-fast-forward rejection *is* the
CAS. Id reservation still replays the log, which is safe because allocation is positional
and every reader computes the same answer.

## What you get

- **Leases with a TTL** — claim a task, renew automatically, steal an expired one.
  Exclusive across machines with `leaseBackend: "git"`; the tool always states which
  guarantee you have rather than implying the stronger one.
- **The claim written through to the roadmap** — one row, one cell, refused on ambiguity,
  and restored verbatim on release. Closing a task stays yours.
- **Awareness, not just exclusion** — `status` lists every *other* run's live holdings,
  so an agent learns who holds a task and what they are touching, instead of only that
  it is taken.
- **Race-free id reservation** — positional allocation over the log, so two agents
  cannot be handed one number.
- **A run journal** — what each run did, with commits, gate results and evidence.
- **A cross-repo signal feed** — `filed → accepted → delivered → closed`. `status`
  surfaces what landed since this run last looked, watermarked per run so it stays quiet
  until something actually changes.
- **A generated board** — machine-written, commit-stamped, and it refuses to overwrite a
  page a human took over.
- **Enforcement hooks** for Claude Code that deny an edit to a guarded register file, or
  a commit staging one, without a live lease.

## Requirements

| Requirement | Why | Check |
|---|---|---|
| **python3 ≥ 3.9** | the coordinator is one stdlib-only script — HTTP included, nothing to `pip install` | `python3 --version` |
| **git** | the record plane, and the cross-machine lease store | `git --version` |
| **bash** | the four Claude Code hook scripts | `bash --version` |
| **Node ≥ 18** | only for the `npx @ssheleg/agent-sync` installer | `node --version` |
| **[task-pipeline](https://github.com/ssheleg/task-pipeline)** | `agent-sync` supplies stages, it does not define them — without it `status` prints one line and stops | `npx sshlg-skills install` |
| A knowledge-base instance *(optional)* | the shared record, awareness and board; without one the `fs` backend keeps leases but loses cross-agent visibility | — |

## Install

```bash
npx @ssheleg/agent-sync install
```

Claude Code gets the plugin; every other agent gets the skill through the
[skills CLI](https://github.com/vercel-labs/skills). The duplicate plain copy in
`~/.claude/skills/` is pruned afterwards, because that shadow silently serves a stale
skill over the installed plugin — **one channel per agent** is the rule.

Restart Claude Code after installing, so it picks the plugin up.

<details>
<summary>Other install routes</summary>

Track `main` from GitHub instead of the npm release:

```bash
npx github:ssheleg/agent-sync install
```

Claude Code only, no skills CLI:

```bash
npx @ssheleg/agent-sync install --claude-only
```

Pick which agents the skills CLI installs for:

```bash
npx @ssheleg/agent-sync install --agent cursor,codex
```

Or add the plugin by hand — the full `<name>@<name>` form is required:

```bash
claude plugin marketplace add ssheleg/agent-sync && claude plugin install agent-sync@agent-sync
```

</details>

## Update

**Update the whole family — one package, every agent.** A bundle with one member current and the
rest stale is a combination nobody tested:

```bash
npx sshlg-skills update               # installed but behind — updates everything
npx sshlg-skills install              # nothing installed yet
npx --yes sshlg-skills@latest list    # what the current release of each member is
```

Restart your agent afterwards: skills and hooks load at session start, so the session that updates
is not the session that gets the new ones.

<details><summary>Updating this one member only</summary>

**agent-sync itself** — update every channel you installed, then restart Claude Code:

```bash
claude plugin marketplace update agent-sync && claude plugin update agent-sync@agent-sync && npx --yes skills update agent-sync --global --yes
```

Re-running the installer works too, but pin `@latest` or npx may serve you its cache:

```bash
npx @ssheleg/agent-sync@latest install
```

Check what you are actually running — the plugin and the skill must report the same
version, and a mismatch means one channel is stale:

```bash
claude plugin list | grep agent-sync
python3 ~/.claude/plugins/cache/*/agent-sync/*/skills/agent-sync/scripts/agent_sync.py --version
```

**Its dependencies** — `task-pipeline` (and the rest of the same family) come from one
installer, which also prunes the shadow copies:

```bash
npx sshlg-skills install
```

Nothing else to update: the coordinator is stdlib-only python, and the npm package has
zero runtime dependencies.

</details>

## Set up a project

**Initialisation is the first command, and it asks a question rather than guessing.**

```
/agent-sync init
```

The agent asks where coordination state should live — a knowledge cloud, or local files
— and, for the cloud, the instance URL. Then it writes two files:

| File | Holds | Committed? |
|---|---|---|
| `.claude/agent-sync.json` | **shape** — backend, TTLs, guarded files, registers, gates | yes |
| `.env.agent-sync` | **identity** — instance URL, token, collection id | **no** — mode 600, added to `.gitignore` |

The token line is written **empty**. Creating the API token in your own instance and
pasting it into that line is your step, and it stays yours: the tool never asks for a
token in chat, never echoes one, and never passes one as a command-line argument.

Load the environment before running agents:

```bash
set -a && . ./.env.agent-sync && set +a
```

Then create the container the coordination log lives in, once per project, and paste the
id it prints into `AGENT_SYNC_OUTLINE_COLLECTION`:

```bash
/agent-sync bootstrap
```

Verify the setup — idempotent, repairs what is missing, and names exactly one next
action:

```bash
/agent-sync status
```

## Everyday use

In an agent session you use the slash command (`/agent-sync acquire ASC-072` — the verb is
`acquire`, the same one the CLI takes); the same commands run directly against the
coordinator script, which is what the hooks and CI do:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" <command>
```

| Command | Does |
|---|---|
| `init` | **Run first.** Ask where state lives, write config + gitignored env file, print your step |
| `status` | Inspect, repair, report — other runs' leases, signals new since you last looked, and `check`'s verdict on the setup |
| `bootstrap` | Create the cloud container and print the id to paste into the env file |
| `acquire <KEY>` | Take the lease on a task id. Prints `won`, or `lost <holder>` |
| `renew <KEY>` | Extend the lease — moves the timestamp expiry is computed from. In Claude Code the `PostToolUse` hook does this for you |
| `release <KEY>` | Give the lease back. Always, including on failure |
| `reserve <REG>` | Reserve the next id in a register (`DEC`, `OQ`, `DEP`, …). Prints the id |
| `release-id <REG> <ID>` | Return an id you did not end up writing to git |
| `journal <text>` | Append one line to this run's journal |
| `signal <DEP-ID> <state>` | Move a cross-repo dependency: `filed`/`accepted`/`delivered`/`closed`/`refused` |
| `guard <path>` | May this run write that path? Exit 0 = yes, 2 = no |
| `board` | Regenerate the read-only board and the mirror from git |
| `whoami` | Print this run's id and its held leases |
| `residue` | Expired locks still on disk, classified — this run's spent ones, and the foreign or ambiguously owned ones it reports and leaves alone |
| `reap [KEY…]` | Clear only the locks this run can prove it owns and has spent, then re-read the directory to confirm the teardown |
| `merge` | Land this branch on the integration branch: conflicts checked first, merge log written, lease released |
| `merges` | What landed while you were on your branch |

The shape that matters:

```
acquire → work on a branch → merge
```

Never skip the last step, including when the work failed. An abandoned lease blocks the
task until its TTL expires, and the next agent cannot tell "in progress" from "crashed an
hour ago". A lease is a promise to come back. `merge` releases for you; without a branch,
`release` by hand.

### Work on a branch, land it with `merge`

The integration branch is somebody else's stable base, so nothing about work in flight is
committed there. `acquire` writes the claim through to the roadmap **only** on the
integration branch; on any other branch it says so and keeps the holder in the coordination
plane, where `status` already shows it to every agent — no one has to fetch your branch to
see who has what, and the shared roadmap does not become the file every branch edits.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" merge --key ASC-072 --summary "what landed"
```

The local integration branch is fast-forwarded to `origin/<target>` first, so the
preflight and the merge share a base; one that has genuinely diverged is refused with both
counts. Conflicts are then computed with `git merge-tree` **before anything is touched** —
on conflict it names the files, changes nothing and exits non-zero, so a resolution nobody
reviewed never reaches the integration branch. Then it merges `--no-ff`, records the merge,
and releases the lease named by `--key`. `--dry-run` stops after the checks; `--push`
pushes afterwards.

**The merge log** — `docs/MERGES.md`, configurable via `mergeLog` — answers the question an
agent coming back from a branch cannot answer from `git log` alone: *what landed while I
was away, and was any of it near my work.* Entries inside `retentionDays` (7) keep their
detail; older ones are folded to one line each **on the next write**, so the file stays
readable without a cron job.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" merges          # recent detail
python3 "$SKILL_DIR/scripts/agent_sync.py" merges --all    # plus the compacted tail
```

This is the flow for agents sharing one repository. A project that reviews through pull
requests keeps doing that — `merge` is then the wrong command, and `finish` is still the
right one. Full doctrine:
[`references/branching.md`](plugins/agent-sync/skills/agent-sync/references/branching.md).

**The lease is not the claim.** The lease says who holds the task *now* and expires; the
durable claim is the tag in git — `todo (claimed: r-7f3a91)`, rendered from the
`claimTags.held` template, naming the **run**, not a role. `acquire` writes that tag
through and `release` restores exactly what was there, so one fact keeps one home.

## Configuration

`.claude/agent-sync.json` — committed, validated against
[`agent-sync.schema.json`](agent-sync.schema.json), starting point in
[`agent-sync.example.json`](agent-sync.example.json):

| Key | Meaning |
|---|---|
| `backend` | `outline` or `fs` (required) |
| `leaseTtlSeconds` | how long a lease survives without a renew (default 2700) |
| `renewIntervalSeconds` | how often a live run renews (default 300) |
| `gated` | whether runs may be recorded as enforced at all |
| `idRegisters` | register → the git file that owns it, and its "next free id" pattern |
| `guardedFiles` | registry files no run may edit without a live lease |
| `claimTags` | file → the durable claim tag `acquire`/`release` writes through |
| `gates` | commands the pipeline stages run as gates |
| `mirror` | which git files are rendered into the read-only mirror |
| `integrationBranch` | where work lands and the only branch a claim is written on (default: the repo's own) |
| `mergeLog` | `file` and `retentionDays` for the merge log (default `docs/MERGES.md`, 7) |

`.env.agent-sync` — gitignored, mode 600. It is looked for in this repository, then in
each **superproject** above it (so one credentials file serves a tree of submodules), and
nowhere else. `AGENT_SYNC_ENV=/path/to/file` names one explicitly and wins over both.
Until 1.7.0 the search continued into any parent directory, so a stray file in a home or
work directory silently pointed every project beneath it at one collection — `check`
now prints which file is in force, and says when it comes from outside the repository.

```
AGENT_SYNC_BACKEND=outline
AGENT_SYNC_OUTLINE_URL=https://<your-instance>
AGENT_SYNC_OUTLINE_TOKEN=          # you fill this line, nobody else
AGENT_SYNC_OUTLINE_COLLECTION=     # printed by `bootstrap`
```

Never write a host name or a token into the config, a test, an example or a commit. If
an agent offers to handle a token for you, that is the wrong answer.

**A submodule's config declares only its own registers.** Cross-repository facts belong
to the parent repository; a service repo listing the parent's decision register is a
configuration defect.

## Backends

**Two settings, two jobs.** `backend` chooses the record plane — where the log, the
signals and the board live. `leaseBackend` chooses what actually decides a lease. The
knowledge base is never the second one: measured against a real instance, twelve
concurrent appends to one document returned twelve successes and left three lines.

| `backend` — the record plane | What it gives |
|---|---|
| `outline` | [Outline](https://www.getoutline.com), hosted or self-hosted. Every repository and machine reads one plane: shared awareness, cross-repo signals, the board |
| `fs` | Local files. No credentials, and no visibility to an agent on another machine |

| `leaseBackend` — the lease | Guarantee |
|---|---|
| `git` | **Exclusive across machines.** The remote's non-fast-forward rejection is a real compare-and-swap |
| `local` *(default)* | **Exclusive on this machine, advisory across machines.** An atomic file create |

`status`, `acquire` and `check` all state which of the two you have, in the same words,
because a lease that is not actually exclusive is worse than none: the other agent has
stopped checking. `runs recorded: gated` follows the lease mode — never the record plane.

Adding one: read
[`references/adapter-contract.md`](plugins/agent-sync/skills/agent-sync/references/adapter-contract.md).

## Enforcement hooks

**This is the only part of the plugin that executes code on your machine.** Everything
else — the skill, its references — is text an agent reads. Four bash scripts, bundled in
the plugin and run by Claude Code on the events below, each with a timeout (15–20s) so a
hung script cannot stall a session. Read them before installing: they are short, and
[`SECURITY.md`](SECURITY.md) lists every path the install touches and why.

Every hook exits immediately in projects without `.claude/agent-sync.json`, so installing
globally changes nothing elsewhere. The `PreToolUse` guard can **deny** a tool call and
never grants one that would otherwise be denied.

| Hook | Runs | Effect |
|---|---|---|
| `SessionStart` | startup, resume | `status` — the board summary, other runs, one next action |
| `PreToolUse` | `Edit`/`Write`/`MultiEdit`/`NotebookEdit`, and `git commit` | Denies the edit (exit 2) when the path is guarded and this run holds no lease; a `git commit` is checked against every staged path |
| `PostToolUse` | every tool call | Throttled `renew` — touches the network at most once per `renewIntervalSeconds` |
| `SessionEnd` | session end | Releases every lease this run holds |

Details and removal:
[`references/hooks.md`](plugins/agent-sync/skills/agent-sync/references/hooks.md).

## Where it plugs into task-pipeline

`agent-sync` supplies stages; it does not define them. It binds to task-pipeline's
stages 0, 1, 3, 4, 5, 9 and 10 — lease before the brief is committed, reconcile before
code is written, reserve ids before they reach git, register file ownership for parallel
groups, signal and regenerate the board at docs, release everything at acceptance. The
stage numbers are stated once, in the marker at the top of the binding reference, and a
check fails when a document stops agreeing with it. Wiring:
[`references/pipeline-binding.md`](plugins/agent-sync/skills/agent-sync/references/pipeline-binding.md).

## Limits, stated plainly

- **Hooks are Claude Code only.** On Cursor, Codex and the rest there is no `PreToolUse`,
  so nothing blocks a guarded edit; the same checks run as a self-check and the run is
  recorded `ungated`. Read the board's column rather than assuming.
- **Ordering, not clocks.** Document order decides who holds a lease; timestamps only
  expire one. Agents' clocks differ and the protocol does not depend on them.
- **A reserved id that never reaches git is reported, not reclaimed.** A half-written
  decision on a branch is not an unused number.
- **`fs` is not cross-machine.** As a record plane it is invisible to agents on another
  host, and the default `local` lease is a mutex on this one. Set `leaseBackend: "git"`
  when a fleet spans machines; the tool says which guarantee you have rather than
  implying the stronger one.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `task-pipeline is not installed` and `status` stops | Intentional — there are no stages to bind to. `npx sshlg-skills install` |
| `⚠ this lease is advisory, not enforced` | `gated: false` in the config, or a `leaseBackend` that is neither `local` nor `git`. Fix the mode — an unknown one claims nothing on purpose |
| `lease: local — advisory across machines` | Expected on the default. Set `leaseBackend: "git"` (and a reachable `leaseRemote`) when agents run on more than one machine |
| Every `acquire` reports `lost` | Check the holder in `status`. The lease is decided by a lock file or a git ref, never by the log, so this is a real holder — not a parse failure |
| A command stops with `the … log is N/M unparseable` | Past 2%, every command that *replays* a log refuses it rather than acting on a partial history. Fix or remove the malformed lines; `acquire` is unaffected, because a lease is not decided there |
| Guarded edit blocked in Claude Code | Working as designed: `acquire` the key first, or unstage the file |
| Guarded edit *not* blocked | You are not on Claude Code. Run `guard <path>` yourself; the run is `ungated` |
| `AGENT_SYNC_OUTLINE_COLLECTION is not set` | Run `bootstrap` and paste the printed id into `.env.agent-sync` |
| An HTTP `400`/`403` from the backend | The response body is surfaced verbatim — read it; a bad collection id and a bad token look nothing alike |
| A stale skill after updating | Two channels serving one skill. Delete `~/.claude/skills/agent-sync` and keep the plugin |
| The board refuses to write | A human took the page over (no generated marker on line 1). Reported, never overwritten |

## Uninstall

```bash
claude plugin uninstall agent-sync@agent-sync
npx --yes skills remove agent-sync --global --yes
```

Project files stay where they are; delete `.claude/agent-sync.json`, `.env.agent-sync`
and `.agent-sync/` if you want the project clean too.

## Develop and verify

<!-- commands-run-in: a clone -->
These run **in a clone of this repository**. The published npm package ships no
`test/` directory, so from an install they are names, not commands.

```bash
python3 test/validate.py             # manifests, version sync, no host/credential leaks
python3 test/validate.py --self-test # the validator must still be able to fail
npm test                             # both of the above
```

What ships: one skill (`agent-sync`), `scripts/agent_sync.py` (stdlib only), four hook
scripts, the slash command, `agent-sync.schema.json`, and eleven reference contracts the
agent loads on their own trigger rather than by default:

| Reference | Read it when |
|---|---|
| [`adapter-contract.md`](plugins/agent-sync/skills/agent-sync/references/adapter-contract.md) | adding or auditing a knowledge backend |
| [`lease-protocol.md`](plugins/agent-sync/skills/agent-sync/references/lease-protocol.md) | changing acquisition, expiry, stealing or id allocation |
| [`backend-outline.md`](plugins/agent-sync/skills/agent-sync/references/backend-outline.md) | making any Outline API call, or debugging one |
| [`backend-notion.md`](plugins/agent-sync/skills/agent-sync/references/backend-notion.md) | making any Notion API call, or debugging one |
| [`backend-fs.md`](plugins/agent-sync/skills/agent-sync/references/backend-fs.md) | running without a cloud backend, or explaining degraded mode |
| [`pipeline-binding.md`](plugins/agent-sync/skills/agent-sync/references/pipeline-binding.md) | wiring `pipeline.json`, or adding a stage hook |
| [`hooks.md`](plugins/agent-sync/skills/agent-sync/references/hooks.md) | installing, debugging or removing the Claude Code hooks |
| [`two-sources.md`](plugins/agent-sync/skills/agent-sync/references/two-sources.md) | before the first reconcile, or when deciding where a document belongs |
| [`earned-rules.md`](plugins/agent-sync/skills/agent-sync/references/earned-rules.md) | why identity is resolved the way it is, and why `finish` exists — the two failures that produced both |
| [`roadmap.md`](plugins/agent-sync/skills/agent-sync/references/roadmap.md) | configuring `claimTags`, taking or closing a task, or re-planning a board |
| [`branching.md`](plugins/agent-sync/skills/agent-sync/references/branching.md) | starting work that will produce commits, merging a branch, or asking what landed while you were away |

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md). Everyone
taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Security reports: [SECURITY.md](SECURITY.md).

`agent-sync` also ships in the [sshlg-skills](https://github.com/ssheleg/sshlg-skills)
bundle, which installs the whole family for Claude Code, Cursor, Codex and 70+
other agents with one command.

## Author

Built by ssheleg — [sshlg.me](https://sshlg.me)

- X / Twitter — [@sshlg93](https://x.com/sshlg93)
- Telegram — [@sshlg](https://t.me/sshlg)

## License

MIT © ssheleg
