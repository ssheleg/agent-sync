---
name: agent-sync
description: "Use when several agents share a repo: claim this task, ask who is working on it, reserve an id, sync the board, set up agent coordination, or finish when the submodule is pushed and the parent points at the old commit. RU: возьми задачу, кто сейчас делает, зарезервируй id, обнови доску, настрой координацию агентов, сабмодуль запушен, родитель на старом коммите. Not for solo work or an unconfigured repo: 'no coordination' / «без координации»."
compatibility: "Requires the task-pipeline skill for its stages (npx sshlg-skills install). Needs python3 3.9+ (stdlib only, HTTP included - nothing to pip install) and bash for the hooks. The knowledge backend is configured per project; with none configured it degrades to git-file leases. The shipped enforcement adapter targets Claude Code; without a verified adapter, run the same checks as a self-check."
license: MIT
metadata:
  version: "1.23.1"
  author: ssheleg
---

# agent-sync — one project, many agents, no collisions

Two planes, and one rule between them:

> **Git is the record plane. The cloud is the coordination plane.**
> A fact that must survive is written to git first and referenced from the cloud.
> A fact about *who is doing what right now* lives in the cloud and expires.

No cloud object is ever the only home of a durable fact. Everything below exists to
keep that true while several agents write at once.

**When, even unasked:** before editing a shared registry (decisions, open questions, roadmap,
workstreams, dependencies) in a project with `.claude/agent-sync.json`; an unclaimed edit lets
two agents overwrite each other. Also `/agent-sync`.

## Four traps — read these before anything else

**1. The knowledge base never decides a lease.** It cannot: twelve concurrent appends to
one Outline document returned twelve successes and left **three** lines. Exclusion comes
from something with real compare-and-swap. The plane carries the record and nothing else.

**2. Know which lease you have, and say so.** `leaseBackend: "local"` is an atomic file
create — exclusive between processes on one filesystem, **advisory across machines**.
`leaseBackend: "git"` pushes a ref, and the remote's non-fast-forward rejection **is** a
compare-and-swap — exclusive across machines. `acquire` prints which. A pretended lease is
worse than no lease: the other agent stops checking.

**`acquire` also writes the claim through to the roadmap**, and `release` restores exactly
what was there — one row, one cell, refused on ambiguity, `git diff` empty after a
round-trip. **Read `references/roadmap.md`** before configuring `claimTags` or closing a
task; closing is a statement about the work and stays yours.

**Work on a branch; the integration branch is somebody else's stable base.** `acquire`
writes the claim through **only** there — on any other branch the holder stays in the
coordination plane, where `status` shows it to every agent. Committed to a branch, a claim
is invisible until the merge and turns the shared roadmap into a file two branches both
edit. Land work with `merge`: conflicts computed **before** anything is touched, the merge
recorded in `docs/MERGES.md`, the `--key` lease released; `merges` says what landed while
you were away. **Read `references/branching.md`** before merging.

**3. Verify the Claude hook adapter.** Without verified enforcement, run `guard` explicitly. Report hook evidence separately
from `gated` lease arbitration. A mode's five capability fields
live in ONE place — `references/adapter-contract.md` → *The status capability
contract*, never restated here. Do not describe a project as protected when it is not.

**4. An unreadable log is not a lost race.** Emit `-`, accept `-`/`*`/`+`, count
every malformed entry-shaped line before filtering, and refuse over 2% unparseable.
The parsing contract and observed failures live in `references/lease-protocol.md`.

## Bringing this into ANY project — the whole chain

```
scaffold  → create the documentation architecture, only where it is absent
adopt     → read the repository, propose a config, write nothing
init      → write the approved config + the gitignored env file
            (operator places the token — never you)
reconcile --set-baseline    → make history a counted backlog, once
setup     → generate the snapshot that describes this project's wiring
check     → validate the whole thing; non-zero if it is not healthy
```

**`check` refuses** missing rule targets, credentials or register baselines, stale
snapshots and an env file **tracked by git**. Run it after adoption, config changes
and in CI; it names every failing condition.

**`scaffold` never overwrites.** It seeds a decision register and an `AGENTS.md` pointing at
the snapshot, and leaves every existing file untouched.

## Existing project: start with `adopt`

Run `adopt` before `init`. It reads the repository and prints what it found — id
registers, registry files, gates — plus the decisions it **refuses to make for you** —
then proposes a config.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" adopt
```

Confirm the registers and guarded files with the operator first: a register pointed at the
wrong file makes every later check confidently wrong, and a guarded list that misses a shared
file leaves the one place collisions happen unprotected. In a submodule it declares no
registers — decisions belong to the parent. Then take the chain above from `init`.

## First command in a project: `init`

**Never run anything else against an uninitialised project.** `init` asks and answers
the storage question once, and writes it down.

**Ask the operator these two things in chat — do not guess, do not pick a default:**

1. **Where should coordination state live?**
   - a knowledge cloud — `outline`, hosted or self-hosted, or `notion` — the shared
     record, awareness and board across machines — **neither decides leases** (trap 1);
   - or local files (`fs`) — no credentials, and no visibility to an agent on another
     machine — no shared awareness, cross-repo signal or board.

   The lease is decided separately by `leaseBackend` (trap 2), and **`gated` follows that
   choice, never the record plane** — report the guarantee you actually have. Ids too:
   under `leaseBackend: "git"` registers reserve from git refs even on `fs`.
2. **If cloud: where.** Outline needs its instance URL; Notion needs the id of the page
   the container goes under. Both are configuration, not secrets, so you may write them.
   The **token is not** — you never ask for it in chat, never read it back, and never
   place it yourself.

Then run it with their answers:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" init --backend outline --url https://<their-instance>
python3 "$SKILL_DIR/scripts/agent_sync.py" init --backend notion
python3 "$SKILL_DIR/scripts/agent_sync.py" init --backend fs
```

`init` writes `.claude/agent-sync.json` (shape, committed) and `.env.agent-sync` with an
**empty** token line (identity, mode 600), gitignores both, and prints the one step that is
the operator's — creating the token and placing it (`notion`: the vault). Never overwrites either
file without `--force`. **Relay those closing instructions verbatim**; the design depends on
the token staying theirs.

## Then, before every session

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" status
```

Idempotent: inspects, repairs, reports and names one next action with `check`'s verdict.

**Read both awareness sections:**

- **Other runs working this project right now.** Who holds what, this minute. Do not take
  those on, and do not "just look at" the files they cover.
- **New since you last looked.** Cross-repo dependency moves that landed while you were
  away — watermarked per run, so it stays quiet until something changes. A dependency that
  moved may unblock what you planned, or invalidate it.

`status` also decides: no credentials → degraded mode; `task-pipeline` absent → it
prints the install line and stops. Absence is checked across every host layout or an
explicit `pipelinePath` — no host's copy masks or is missed. The lease core
(`acquire`/`renew`/`release`) needs a backend + lease, not the binding.

```bash
npx sshlg-skills install
```

## The commands

| Command | Does |
|---|---|
| `init` | **Run first.** Ask where state lives, write config + gitignored env file, print the operator's step |
| `status` | Inspect, repair, report, name one next action |
| `bootstrap` | Create the cloud container and print the id to paste into the env file |
| `acquire <KEY>` | Take the lease on a task id. Prints `won` or `lost <holder>` |
| `renew <KEY>` | Explicit keepalive. The hook uses `--active-only`; unrelated calls do not prolong claims |
| `release <KEY>` | Give the lease back on every path; `released` means the remote ref is gone |
| `release --held` | Give back everything this run holds, in one process — what SessionEnd runs |
| `reserve <REG> [--key K] [--offline]` | Reserve the next id in a register (`DEC`, `OQ`, `DEP`, …); prints it. `--key` makes a retry idempotent (one key, one number); `--offline` issues a namespaced `REG-o-…` id with no global authority |
| `map-offline <REG> <ID> <N>` | Bind an offline id to a properly reserved number — append-only, never rebound |
| `release-id <REG> <ID>` | Return an id you did not end up writing to git |
| `journal <text>` | Append one line to this run's journal |
| `record <text>` | Append what you **actually built** — `--decision DEC-…`, `--files a,b` |
| `reconcile` | Intent (git) vs as-built (cloud). `--set-baseline` once per project |
| `signal <DEP-ID> <state>` | Record dependency state, never an ask. With Fabric, ask via `com.submit`; the DEP cites its board message |
| `guard <path>` | Answer whether this run may write that path. Exit 0 = yes, 2 = no |
| `board` | Regenerate the shared board and this repo's page. `--mirror` also renders the configured git docs into the plane |
| `whoami` | Print this run's id and its held leases |
| `residue` | What a run left behind in BOTH planes: expired locks on disk, and claim tags with no live lease behind them |
| `reap [KEY…]` | Clear only what this run **proves** it owns and has spent, then re-read the directory to confirm it went |
| `reap --long-expired` | List locks expired 7+ days on both planes, each with its verdict. `--apply` clears them by age alone — **the operator's call, never yours** |
| `setup` | Write the generated snapshot of how **this** project is wired, for agents to read |
| `adopt` | Inspect an existing project and **propose** a config — writes nothing |
| `merge` | Land this branch: conflicts checked **before** anything is touched, merge log written, the `--key` lease released after the push. `--summary`, `--dry-run`, `--push` |
| `merges` | What landed while you were on your branch. `--all` includes the compacted tail |
| `check` | Validate the whole setup end to end. Non-zero when it is not healthy |
| `scaffold [--full]` | Create only what is missing, never a line over what exists. `--full` also seeds the question register, index, dependency board, data model and docs gate |
| `finish [--gates]` | Is the **work** finished — every repository clean, pushed and pointed at, nothing left held. `check` answers whether the project is wired right; this, whether you are done |

Resolve `$SKILL_DIR` from the active `SKILL.md` location and reuse the absolute path;
`${CLAUDE_PLUGIN_ROOT}/skills/agent-sync` is a Claude text substitution, not a Bash
environment guarantee.

## One identity per session, and how it is decided

Identity precedence (`references/earned-rules.md`): `AGENT_SYNC_RUN_ID` · `CLAUDE_SESSION_ID` ·
`CLAUDE_CODE_SESSION_ID` (every Claude Code Bash call carries it) · **the session that
started this shell** (`SessionStart` stamps `.agent-sync/sessions/<CLI pid>`; a command walks
its process ancestry to it) · shared — and a shared identity is said out loud, never
presented as separation.

## Claiming — the shape that matters

```
acquire → do the work → release
```

Release even on failure; an abandoned lease blocks the task until TTL expiry.
For PRs: push the durable handoff, then release; see `references/branching.md`.
A lease taken in **another repository** is listed under this session, and the renew and
SessionEnd hooks act there too (AS-07).

**The lease is not the claim.** The lease says who holds it *now* and expires; the durable
claim is the tag in git, written through by `acquire` and cleared by `release`. One fact,
one home — do not invent a third place that records ownership.

**One notion of held, consulted by both planes.** The TTL ends the lease, so a tag naming a
run whose lease has ended is residue: reported by `status`, `residue` and `reconcile`, and
cleared by `release <KEY>`, which says whose it was. A tag whose lease is still live is
never touched by another run.

**Read `references/lease-protocol.md`** before changing acquisition, expiry, stealing or
id allocation.

## Guarded files

The config lists registry files several agents write. Before editing one:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" guard docs/DECISIONS.md
```

**Exit 2 is about *this run*: it holds no lease** — not that somebody else holds that file.
A denial names the other run **and its key**, because "r-x holds a lease" beside a path gets
repeated as "r-x holds this file". Do not edit anyway, and do not "just fix one line" — a
clobbered decision looks exactly like a decision.

**Two write modes.** A task lease authorizes the TASK, never the file, so a guarded write
also takes the FILE's **resource claim** (`res--<repo>--<canonical path>`, SY-04) — the
**short transaction lock**: one registry serializes, independent files never do;
cross-machine only under `leaseBackend: "git"`. Heavy overlap: **isolated worktree + merge**.
What the guard does NOT promise is enforcement from a single task owner.

The shipped `PreToolUse` adapter runs this in the owning repo for an Edit/Write, a
`git commit` (with what the same command stages) and a Bash write parsed by
`scripts/bash_writes.py` — a parser, not a sandbox. No verified adapter: run it yourself.

## Reserving an id

Reading a "Next free ID" line is not reserving it — two agents read the same number and both
use it.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" reserve DEC   # → DEC-0216
```

Allocation is positional over the **merged** log — every shard, never just this run's — so
every agent computes the same answer. Reserved and not written to git? `release-id` it, or the
number is a hole the board reports as a leak.

## Nothing in a log is ever edited or deleted

Logs are **replayed in order**, so an edit silently rewrites a conclusion other agents already
acted on. Correct by **appending**: release a lease, `release-id` an unused id, supersede a
wrong as-built entry with a later one. Generated pages are the only exception. Lifetimes and
the generated-object contract: `references/two-sources.md`.

## Two documentation sources, and the duty to reconcile them

Git docs answer **how it should be**; the as-built record answers **how it actually is**.
Neither outranks the other, and **the gap between them is the finding**, not a defect. The
duty runs at both ends of a task: `reconcile` before starting, `record` and `reconcile`
after finishing — and `reconcile` is mechanical, so treating its green as agreement is how
a divergence survives both ends.

**Read `references/two-sources.md`** before the first reconcile: it holds the full duty,
what `reconcile` refuses to judge, the generated `setup` snapshot, where a document
belongs, and why nothing is deleted.

## Binding to task-pipeline

This skill supplies stages; the names are `task-pipeline`'s own.
Five of the eleven stages carry an ordering rule: **0** `acquire` before the brief
commits; **1** `reconcile` before code; **3** `reserve` ids before git; **9** the main
write; **10** `merge`/`release` every lease.

**Read `references/pipeline-binding.md`** when wiring `pipeline.json` — per-stage
reasoning, `skills[]` entries, what to guard, and the gate expressions.

## Configuration

Two files, and the split between them is the whole security model.

**`.claude/agent-sync.json`** — *shape*, committed: which backend, TTLs, which files
are guarded, which registers exist, which gates to run.

**`.env.agent-sync`** — *identity*, written by `init`, mode 600, gitignored; found here, in
a superproject, or at `AGENT_SYNC_ENV` — `check` prints which. The tool reads it itself:
do not `source` it. **A `notion` token belongs in the Observatory vault** (1.22.0) — `init`
prints the `vault.py put` line; record-plane commands re-run under
`use_secret.py run --vault-only`, and the guard never asks the vault. `outline` keeps the
token in the file. `scheduledIntervalSeconds` (≥ 60): a lease held longer is warned once.

Never write a host name or token into the config, a test, an example or a commit; never
handle, echo or `argv`-pass a token value. Offered one in chat, point at that file instead.

**A submodule's config declares only its own registers.** Cross-repository facts belong to
the parent; a service repo listing the parent's decision register is a config defect.

## Non-negotiables

- Append, read back, then act. Never rewrite a coordination document.
- A generated object without its `agent-sync:generated` marker is **refused**, not
  overwritten — a human took it over. Contract: `references/two-sources.md`.
- `release` what you `acquire`, on every path including failure.
- A run reports what it leaves behind: expiry ends a lease, not the file — `residue`.
- Credentials never reach `argv`, a log line, or the repository.
- Degrade out loud. `ungated` is an acceptable state; a false claim of enforcement is not.
- Two agents in one checkout are two identities, or the lease is decoration.
- A submodule commit is unfinished until the parent points at it — run `finish`.
- Everything the cloud holds about a durable fact is a link to git, never a substitute.
- Boundary: `.claude/agent-sync.json`. None, or one agent alone — route past. The
  operator's waiver: "no coordination" / «без координации».

## References

Each file is loaded on its own trigger, not by default.

| File | Read it when |
|---|---|
| `references/adapter-contract.md` | adding or auditing a knowledge backend — six primitives, the capability flags, an honest degradation path |
| `references/lease-protocol.md` | changing acquisition, expiry, stealing or id allocation |
| `references/backend-outline.md` | making any Outline API call, or debugging one |
| `references/backend-notion.md` | making any Notion API call, or debugging one |
| `references/backend-fs.md` | running without a cloud backend, or explaining degraded mode |
| `references/pipeline-binding.md` | wiring `pipeline.json`, or adding a stage hook |
| `references/hooks.md` | installing, debugging or removing the Claude Code hooks |
| `references/two-sources.md` | before the first reconcile, deciding where a document belongs, or writing a generated object |
| `references/roadmap.md` | configuring `claimTags`, taking or closing a task, or re-planning a board |
| `references/branching.md` | starting work that will produce commits, merging a branch, or asking what landed while you were away |
| `references/earned-rules.md` | asking why identity resolves the way it does, or why `finish` exists |
| `references/fabric-coordination.md` | Fabric's coordination contract, or `reap --long-expired` |

Missing `references/`? Fetch from `https://raw.githubusercontent.com/ssheleg/agent-sync/main/plugins/agent-sync/skills/agent-sync/references/<file>`.
