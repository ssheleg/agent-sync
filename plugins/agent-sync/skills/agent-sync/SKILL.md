---
name: agent-sync
description: "Use when several coding agents work one repository at the same time and must not collide - claiming a task, reserving the next decision/question/ticket id, journaling a run, filing or answering a cross-repo dependency, or regenerating the shared board. Triggers - 'claim this task' / 'возьми задачу', 'who is working on X' / 'кто сейчас делает X', 'reserve an id' / 'зарезервируй id', 'sync the board' / 'обнови доску', 'set up agent coordination' / 'настрой координацию агентов', /agent-sync. Use it BEFORE editing any shared registry file (decisions, open questions, roadmap, workstreams, dependencies) in a project that has .claude/agent-sync.json, even when the user never mentions coordination - an unclaimed edit to those files is how two agents overwrite each other."
compatibility: "Requires the task-pipeline skill for its stages (npx sshlg-skills install). Needs python3 3.9+ (stdlib only, HTTP included - nothing to pip install) and bash for the hooks. The knowledge backend is configured per project; with none configured it degrades to git-file leases. Enforcement hooks are Claude Code only - on other agents the same checks run as a self-check."
license: MIT
metadata:
  version: "1.6.0"
  author: ssheleg
---

# agent-sync — one project, many agents, no collisions

Two planes, and one rule between them:

> **Git is the record plane. The cloud is the coordination plane.**
> A fact that must survive is written to git first and referenced from the cloud.
> A fact about *who is doing what right now* lives in the cloud and expires.

No cloud object is ever the only home of a durable fact. Everything below exists to
keep that true while several agents write at once.

## Four traps — read these before anything else

**1. The knowledge base never decides a lease.** It cannot: twelve concurrent appends to
one Outline document returned twelve successes and left **three** lines. Exclusion comes
from something with real compare-and-swap. The plane carries the record and nothing else.
Full measurements in `references/lease-protocol.md`.

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
writes the claim through **only** there; on any other branch the holder stays in the
coordination plane, where `status` shows it to every agent without anyone fetching your
branch. Committed to a branch, a claim is invisible until the merge and turns the shared
roadmap into a file two branches both edit. Land work with `merge`: conflicts computed by
`git merge-tree` **before anything is touched**, named and refused if any, the merge
recorded in `docs/MERGES.md` (recent days in full, older compacted on write), the lease
released. `merges` tells the next agent what landed while it was away. **Read
`references/branching.md`** before merging.

**3. Hooks exist only in Claude Code.** Elsewhere nothing blocks a guarded edit: run
`guard` yourself and record the run as `ungated`. Do not describe a project as protected
when it is not.

**4. Parse liberally, and never call an unreadable log a lost race.** The store rewrites
what you wrote — Outline turns a `- ` bullet into `* `. Emit `- `, accept `-`/`*`/`+`,
count anything entry-shaped that fails, and **fail loudly** past 2% unparseable. Reporting
`lost` when the truth is *unreadable* names a holder who does not exist. Watch for a
silent pre-filter: a `continue` before the regex hides bad lines from the counter built to
expose them.

## Bringing this into ANY project — the whole chain

```
scaffold  → create the documentation architecture, only where it is absent
adopt     → read the repository, propose a config, write nothing
init      → write the approved config + the gitignored env file
            (operator pastes the token — never you)
reconcile --set-baseline    → make history a counted backlog, once
setup     → generate the snapshot that describes this project's wiring
check     → validate the whole thing; non-zero if it is not healthy
```

**`check` is what makes the skill self-sufficient.** It refuses to call a setup healthy on
a rule that protects nothing (a register, guard glob, claim pattern, gate or mirror source
pointing at what is not there), on missing credentials, on an env file **tracked by git** —
the one unrecoverable mistake here — on a stale or unlinked snapshot, and on a register
with no baseline. It names each one; every one failed for real during this tool's own
adoption.

Run `check` after adopting, after changing the config, and in CI.

**`scaffold` never overwrites.** It seeds a decision register with an allocation line and
an `AGENTS.md` that points at the snapshot, and leaves every existing file untouched — a
tool that rewrites a project's own conventions on adoption is worse than one that does
nothing.

## Existing project: start with `adopt`

Run `adopt` before `init`. It reads the repository and prints what it found — id
registers, registry files, gates — plus the decisions it **refuses to make for you**,
then proposes a config. It writes nothing.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" adopt
```

Confirm the registers and guarded files with the operator first: a register pointed at the
wrong file makes every later check confidently wrong, and a guarded list that misses a
shared file leaves the one place collisions happen unprotected. In a submodule it declares
no registers: decisions belong to the parent repository. Then take the chain above from
`init`.

## First command in a project: `init`

**Never run anything else against an uninitialised project.** `init` is where the
storage question gets asked and answered, once, and written down.

**Ask the operator these two things in chat — do not guess, do not pick a default:**

1. **Where should coordination state live?**
   - a knowledge cloud (`outline`) — the shared record, awareness and board across
     machines. **It does not decide leases**; nothing in it can (trap 1);
   - or local files (`fs`) — no credentials, and no visibility to an agent on another
     machine: no shared awareness, no cross-repo signals, no shared board.

   The lease is decided separately by `leaseBackend` — `git` for cross-machine exclusion,
   `local` otherwise — and **`gated` follows that choice, never the record plane**. `fs`
   with a local lock is still real exclusion between the agents on this machine; `outline`
   with a local lock is *not* exclusion across them. Report the one you actually have.
2. **If cloud: the instance URL.** The URL is configuration, not a secret, so you
   may write it. The **token is not** — you never ask for it in chat, never read it
   back, and never place it yourself.

Then run it with their answers:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" init --backend outline --url https://<their-instance>
python3 "$SKILL_DIR/scripts/agent_sync.py" init --backend fs
```

`init` writes `.claude/agent-sync.json` (shape, committed), writes
`.env.agent-sync` with the keys and an **empty** token line (identity, mode 600),
adds `.env.agent-sync` and `.agent-sync/` to `.gitignore`, and then prints exactly
what the operator must do themselves — create the token in their own instance and
paste it into that one line. It never overwrites an existing config or env file
without `--force`.

Relay those closing instructions to the operator verbatim. Getting the token into
the file is their step, and the design depends on it staying theirs.

## Then, before every session

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" status
```

Idempotent. Inspects, repairs what is missing, prints a status block, names exactly
ONE next action.

**Read the two awareness sections it prints — they are the point, not decoration.**

- **Other runs working this project right now.** Who holds what, this minute. Do not
  take those on, and do not "just look at" the files they cover. A lease you cannot
  see makes you blocked; a lease you can see makes you coordinated.
- **New since you last looked.** Cross-repo dependency moves that landed while you
  were away. A dependency that moved may unblock what you planned — or invalidate it.
  This list is watermarked per run, so it stays quiet until something actually
  changes; when it speaks, it matters.

An agent that skips this block will re-derive work someone else is doing and act on a
dependency state that changed an hour ago.

What else `status` decides: no credentials → degraded mode, reported, and it continues (a
smaller mode, not an error); `task-pipeline` absent → it prints the install line and stops.
Do not improvise a substitute flow — without those stages there is nothing to bind to.

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
| `renew <KEY>` | Extend the lease. The `PostToolUse` hook does this for you |
| `release <KEY>` | Give the lease back. Always do this, including on failure |
| `reserve <REG>` | Reserve the next id in a register (`DEC`, `OQ`, `DEP`, …). Prints the id |
| `release-id <REG> <ID>` | Return an id you did not end up writing to git |
| `journal <text>` | Append one line to this run's journal |
| `record <text>` | Append what you **actually built** — `--decision DEC-…`, `--files a,b` |
| `reconcile` | Intent (git) vs as-built (cloud). `--set-baseline` once per project |
| `signal <DEP-ID> <state>` | Move a cross-repo dependency: `filed`/`accepted`/`delivered`/`closed`/`refused` |
| `guard <path>` | Answer whether this run may write that path. Exit 0 = yes, 2 = no |
| `board` | Regenerate the shared board and this repo's page. `--mirror` also renders the configured git docs into the plane |
| `whoami` | Print this run's id and its held leases |
| `setup` | Write the generated snapshot of how **this** project is wired, for agents to read |
| `adopt` | Inspect an existing project and **propose** a config — writes nothing |
| `merge` | Land this branch: conflicts checked **before** anything is touched, merge log written, lease released. `--key`, `--summary`, `--dry-run`, `--push` |
| `merges` | What landed while you were on your branch. `--all` includes the compacted tail |
| `check` | Validate the whole setup end to end. Non-zero when it is not healthy |
| `scaffold [--full]` | Create only what is missing, never a line over anything that exists. `--full` also seeds the question register, the index, the dependency board, the data model with its entity register, and the docs gate |
| `finish [--gates]` | Is the **work** finished — every repository clean, pushed and pointed at, no lease left held. `check` answers whether the project is wired correctly; this answers whether you are done |

`$SKILL_DIR` is this skill's own directory. Every command reads
`.claude/agent-sync.json` from the project root and needs no arguments beyond those
listed.

## One identity per session, and how it is decided

A lease is only a lease if two agents get two identities. Ordering matters here and both ends have
bitten: deriving the id from `CLAUDE_SESSION_ID` alone gave **one session two identities** — it
acquired as one and was denied by its own guard as the other — and keeping one id per checkout gave
**two sessions one identity**, which is worse. The second is silent: both sessions acquire, both are
guarded, and `release` takes a lease the caller never had.

The order is: `AGENT_SYNC_RUN_ID` · `CLAUDE_SESSION_ID` · **the session that started this shell** ·
shared.

The third matters because a plain shell command has no session id and a hook does. So
`SessionStart` stamps `.agent-sync/sessions/<CLI pid>` with the session it knows, and a later
command finds itself by walking its own process ancestry to a stamped pid. Why that and not
command-line parsing: `references/earned-rules.md`.

When none of the four can be established the run says so — *"this identity is shared with any other
session in this checkout"* — rather than presenting a shared entry as separation.

## Claiming — the shape that matters

```
acquire → do the work → release
```

Never skip `release`, including on failure: an abandoned lease blocks the task until its
TTL expires, and the next agent cannot tell "in progress" from "crashed an hour ago".

**The lease is not the claim.** The lease says who holds it *now* and expires; the durable
claim is the tag in git, written through by `acquire` and cleared by `release`. One fact,
one home — do not invent a third place that records ownership.

**Read `references/lease-protocol.md`** before changing acquisition, expiry, stealing or
id allocation.

## Guarded files

The config lists registry files several agents write. Before editing one:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" guard docs/DECISIONS.md
```

**Exit 2 is about *this run*: it holds no lease** — not that somebody else holds that
file. One lease covers every guarded file; hold one or write none. A denial names the
other run **and its key**, because "r-x holds a lease" beside a path gets repeated as
"r-x holds this file". Do not edit anyway, and do not "just fix one line" — a clobbered
decision looks exactly like a decision.

Claude Code's `PreToolUse` hook runs this for you. Elsewhere nothing does.

## Reserving an id

Reading a "Next free ID" line is not reserving it — two agents read the same number and
both use it.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" reserve DEC   # → DEC-0216
```

Allocation is positional over the **merged** log — every shard, never just this run's —
so every agent computes the same answer. Reserved and not written to git? `release-id`
it, or the number is a hole the board reports as a leak.

## Nothing in a log is ever edited or deleted

Logs are **replayed in order**, so an edit silently rewrites a conclusion other agents
already acted on. Correct by **appending**: release a lease, `release-id` an unused id,
supersede a wrong as-built entry with a later one. Generated pages are the only
exception, and one that lost its `agent-sync:generated` marker is **refused**, not
overwritten. Lifetimes: `references/two-sources.md`.

## Two documentation sources, and the duty to reconcile them

Git docs answer **how it should be** — written before the code, often without it.
The as-built record answers **how it actually is** — derived from what agents really
wrote. Neither is a copy of the other, and neither outranks the other, because they
answer different questions. **The gap between them is the finding**, not a defect.

The duty runs at both ends of every task:

- **Before starting** (docs-study stage) — `reconcile`, then read both sides for the area
  you are about to touch, and resolve each divergence: the git doc is stale, the as-built
  record is wrong, or they genuinely disagree and that is a decision. Building on an
  unresolved divergence is writing code against a system that does not exist.
- **After finishing** (docs stage) — `record` what you built, update the git documents
  that state intent, then `reconcile` again. A task that updated one side leaves the next
  agent a divergence to find the hard way.

`reconcile` is mechanical and says so: it compares ids, commits and presence, and refuses
to judge whether the built thing matches the document. That reading is yours.

Every project also carries a **generated snapshot** of its own wiring (`setup`) — commit
it and link it from the agent instructions, so agents read the pipeline instead of
inferring it.

**Read `references/two-sources.md`** before the first reconcile, and whenever deciding
which side a document belongs on.

## Binding to task-pipeline

This skill supplies stages; it does not define them. Stage names are
`task-pipeline`'s own.

Five of the eleven stages carry a rule the others do not, and each is about ordering:
**0** `acquire` before the brief is committed; **1** `reconcile` and resolve every
divergence before writing code; **3** `reserve` every id before it reaches git; **9**
`record`, `signal`, `reconcile`, `board` — the main write point. **10** ends the run:
`merge` if the work is on a branch, otherwise `release` every lease by hand. Full table
with the reasoning per stage: `references/pipeline-binding.md`.

**Read `references/pipeline-binding.md` when wiring `pipeline.json`** — it holds the
`skills[]` entries and the gate expressions.

## Configuration

Two files, and the split between them is the whole security model.

**`.claude/agent-sync.json`** — *shape*, committed: which backend, TTLs, which files
are guarded, which registers exist, which gates to run.

**`.env.agent-sync`** — *identity*, created by `init`, mode 600, gitignored:

```
AGENT_SYNC_BACKEND=outline
AGENT_SYNC_OUTLINE_URL=https://<instance>
AGENT_SYNC_OUTLINE_TOKEN=          # the operator fills this line, nobody else
AGENT_SYNC_OUTLINE_COLLECTION=     # printed by `bootstrap`
```

Load it before running agents:

```bash
set -a && . ./.env.agent-sync && set +a
```

Never write a host name or token into the config, a test, an example or a commit. Do not
handle a token value, echo it, or pass it in `argv`. If the operator offers one in chat,
tell them to put it in that file instead.

**A submodule's config declares only its own registers.** Cross-repository facts belong to
the parent; a service repo listing the parent's decision register is a config defect.

## Backends

| Backend | Read when |
|---|---|
| `outline` | `references/backend-outline.md` — before any Outline call |
| `fs` | `references/backend-fs.md` — local files, no shared awareness |

**Read `references/adapter-contract.md` before adding a backend** — six primitives, the
capability flags, and a degradation path that must be honest.

## Generated objects

The board and the mirror are machine-written. Their first line is

```
<!-- agent-sync:generated source=<repo>@<sha> at=<iso8601> — edit in git, not here -->
```

A write to an object missing that marker is refused, not forced. If a human took over a
generated page, report it and stop.

The mirror is a **rendering** of git, stamped with the source commit. It has no
authority. When its stamp and `HEAD` disagree, the board gate fails — that is
drift, not a formatting problem.

## Two rules, and the failures that taught them

Identity comes before coordination: a lease is only a lease if two agents get two identities. A
submodule commit is unfinished until its parent points at it. Both:
[`references/earned-rules.md`](references/earned-rules.md).

## Non-negotiables

- Append, read back, then act. Never rewrite a coordination document.
- `release` what you `acquire`, on every path including failure.
- Credentials never reach `argv`, a log line, or the repository.
- Degrade out loud. `ungated` is an acceptable state; a false claim of enforcement is not.
- Two agents in one checkout are two identities, or the lease is decoration.
- A submodule commit is unfinished until the parent points at it — `finish` before you call it done.
- Everything the cloud holds about a durable fact is a link to git, never a substitute.

## References

Each file is loaded on its own trigger, not by default.

| File | Read it when |
|---|---|
| `references/adapter-contract.md` | adding or auditing a knowledge backend |
| `references/lease-protocol.md` | changing acquisition, expiry, stealing or id allocation |
| `references/backend-outline.md` | making any Outline API call, or debugging one |
| `references/backend-fs.md` | running without a cloud backend, or explaining degraded mode |
| `references/pipeline-binding.md` | wiring `pipeline.json`, or adding a stage hook |
| `references/hooks.md` | installing, debugging or removing the Claude Code hooks |
| `references/two-sources.md` | before the first reconcile, or when deciding where a document belongs |
| `references/roadmap.md` | configuring `claimTags`, taking or closing a task, or re-planning a board |
| `references/branching.md` | starting work that will produce commits, merging a branch, or asking what landed while you were away |

If this copy arrived without `references/`, fetch them from
`https://raw.githubusercontent.com/ssheleg/agent-sync/main/plugins/agent-sync/skills/agent-sync/references/<file>`.
