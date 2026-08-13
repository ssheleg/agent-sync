# agent-sync — multi-agent coordination over a pluggable knowledge cloud

**Date:** 2026-07-29 · **Status:** approved design, ready to plan
**Deliverable:** a distributable Claude Code plugin + Agent-Skills package,
published at `github.com/appvillis-com/agent-sync`, plus the configuration and the
`DEC-0216` record that adopt it in this repository.

---

## 0. Why

Several agents already work this project in parallel, in four repositories
(`nicegram-business` umbrella + `nicegram-business-app` + `account-session-connect`
+ `account-factory`). The coordination substrate exists and is good — the Doc Loop,
the three registries, the Directions Board, `BUILD_ORDER.md`, `DEPENDENCIES.md`,
per-repo roadmaps — but every one of those is a **git file edited by hand**. That is
enough for humans working in turns and not enough for agents working at once:

| Failure | Why it happens today |
|---|---|
| Two agents mint `DEC-0216` | "Next free ID" is a line in a file; reading it is not reserving it |
| A claim blocks a task forever | `[Backend]` is a role, not a holder, and has no expiry |
| Two agents start the same task | Git shows what was committed, never what is in flight |
| Merge conflicts on every Doc Loop | `DECISIONS.md`, `WORKSTREAMS.md`, `DEPENDENCIES.md` are written by everyone |
| A `DEP-###` row is never seen | Filing one notifies nobody; the protocol itself calls silence "the one unacceptable response" |
| A submodule commit orphans the umbrella | Nobody owns the gitlink bump |

`agent-sync` closes those six, and nothing else. It does **not** replace the
registries, invent a task format, or take over the pipeline.

## 1. Scope and non-goals

**In scope.** A lease/reservation authority, a run journal, a cross-repo signal
feed, a generated read-only board, and a generated mirror — all on a **pluggable**
knowledge backend, bound to `task-pipeline`'s existing stages.

**Non-goals.**
- Not a task tracker. Task status stays in the owning repository's `ROADMAP.md`
  ([DEC-0171](../../DECISIONS.md)).
- Not a decision store. Decisions stay in `docs/DECISIONS.md`.
- Not an MCP server (see §11).
- Not project-specific: the mechanism is generic, the register map is configuration.

## 2. Two planes, and the rule between them

> **Git is the record plane. The cloud is the coordination plane.**
> A fact that must survive is written to git first and referenced from the cloud.
> A fact about *who is doing what right now* lives in the cloud and expires.

Every cloud object therefore carries the git coordinate it describes. No cloud
object is ever the only home of a durable fact. This is what keeps
[`AGENTS.md`](../../../AGENTS.md) §2 (single source of truth) intact while adding a
second system.

## 3. The adapter contract — LOCKED

A backend is an adapter implementing six primitives and declaring three
capabilities. `references/adapter-contract.md` in every skill directory is the
normative copy of this section.

### 3.1 Primitives

| Primitive | Signature | Semantics |
|---|---|---|
| `tree.ensure` | `(path) -> id` | Idempotently create the container/document at `path`; return its id. Never overwrites existing content. |
| `log.append` | `(id, line) -> ok` | Append exactly one `\n`-terminated line to the end of the object's text, **server-side**, without a read-modify-write cycle. |
| `log.read` | `(id) -> text` | Return the object's full text. Line order MUST be identical for every reader at a given revision. |
| `doc.put` | `(id, text) -> ok` | Replace the object's text wholesale. Used only by generators (§6.3). |
| `doc.get` | `(id) -> text` | Return the object's text. |
| `search` | `(query, limit) -> [{id,title,snippet}]` | Full-text search across the workspace. Feeds the stage-0 harvest. |

### 3.2 Capabilities

```json
{ "atomicAppend": true, "totalOrderRead": true, "search": true }
```

- **`atomicAppend`** — `log.append` is a server-side append with no read-modify-write.
- **`totalOrderRead`** — concurrent appends land in one order that every reader sees identically.
- **`search`** — `search` is implemented.

### 3.3 Degradation — non-negotiable

**If `atomicAppend` or `totalOrderRead` is false, the adapter MUST NOT be used as
the lease authority.** In that case `agent-sync`:

1. says so, once, in plain words at session start;
2. falls back to git-file leases (`.agent-sync/leases/<key>.lock`, committed and pushed);
3. marks every run `ungated` on the board.

Silently pretending to hold a lease is worse than having none, because the second
agent trusts it. This rule is stated in each `SKILL.md` **body**, not only in the
reference, because an agent cannot know to open a file about a trap it has not met.

## 4. The append-log protocol — LOCKED

Both leases and ID reservations are decided by replaying one append-only log. There
is no compare-and-swap in any planned backend, so **ordering in the document — not
the timestamp — is authoritative.** Timestamps are used only to expire leases.

### 4.1 Line grammar

One event per line. Exactly this shape, parsed by
`^- \x60(?<ts>[^\x60]+)\x60(?<pairs>(?: \x60[a-z_]+=[^\x60]*\x60)+)$`:

```
- `2026-07-29T10:42:13Z` `op=acquire` `key=ASC-072` `run=r-7f3a91` `agent=claude-opus-5` `ttl=2700` `repo=account-session-connect` `sha=9bba6d2`
```

Required pairs on every line: `op`, `key`, `run`. `op` is one of
`acquire` · `release` · `renew` · `base` · `reserve` · `release_id` ·
`signal` · `journal`.

Unparseable lines are **skipped and reported**, never guessed at. A log whose
unparseable fraction exceeds 2% fails the board gate.

### 4.2 Lease acquisition

```
1. append   op=acquire key=<K> run=<R> ttl=<seconds>
2. wait     250 ms (jitter 0–150 ms)
3. read     log.read → replay from the top
4. resolve  K's holder = the earliest acquire for K that is neither released
            nor expired at that point in the replay
5. if holder == R  → won, proceed
   else            → lost; append op=release key=<K> run=<R>, back off, re-try
                     at most 3 times, then report and stop
```

Replay is a pure function of the log text. Every agent computes the same holder.
Expiry: an `acquire` is expired when `now > ts + ttl` **and** no `renew` for the
same `(key, run)` is later in the log. Default `ttl` = 2700 s (45 min); `renew`
is emitted by the `PostToolUse` hook at most once per 300 s.

**Stealing** an expired lease is legal and is exactly the normal `acquire` path —
the replay simply finds the previous holder expired. The steal is visible in the log.

### 4.3 ID reservation

Positional allocation, so no agent has to trust another's arithmetic.

```
- `…` `op=base` `key=DEC` `value=0216` `run=r-bootstrap`
```

The *n*-th `op=reserve key=DEC` line after that `base` is assigned
`value = base + n - (number of earlier release_id values reclaimed before it)`.
Concretely: maintain a free list from `op=release_id` lines in log order; a
`reserve` takes the free list head if non-empty, else `base + (count of prior
reserves not served from the free list)`.

An agent that abandons a reserved id **must** append `op=release_id`. An id
reserved but not present in git after its run closes is reported by the board
gate as a leak — never silently reclaimed, because a half-written `DEC` in a
branch is not the same thing as an unused number.

### 4.4 What the log is not

The log is **not** the claim. The durable claim is the git tag
(`WORKSTREAMS.md` `[name]`, or `todo (claimed: <role>)` in a service roadmap).
`acquire` writes that tag through in the same run; `release` clears it. One fact,
one durable home, one stated derivation — see §10, C-7.

## 5. Configuration — LOCKED

### 5.1 `.claude/agent-sync.json` (committed; contains no identity)

```json
{
  "$schema": "https://raw.githubusercontent.com/nicegram/agent-sync/main/agent-sync.schema.json",
  "backend": "outline",
  "leaseTtlSeconds": 2700,
  "renewIntervalSeconds": 300,
  "gated": true,
  "idRegisters": {
    "DEC": { "file": "docs/DECISIONS.md", "nextFreeIdPattern": "\\*\\*Next free ID:\\*\\* `DEC-(\\d{4})`" },
    "OQ":  { "file": "docs/OPEN_QUESTIONS.md", "nextFreeIdPattern": "\\*\\*Next free ID:\\*\\* `OQ-(\\d{4})`" },
    "DEP": { "file": "docs/DEPENDENCIES.md", "nextFreeIdPattern": "DEP-(\\d{3})" }
  },
  "guardedFiles": [
    "docs/DECISIONS.md",
    "docs/OPEN_QUESTIONS.md",
    "docs/WORKSTREAMS.md",
    "docs/DEPENDENCIES.md",
    "docs/ROADMAP.md",
    "docs/BUILD_ORDER.md",
    "apps/*/docs/ROADMAP.md"
  ],
  "claimTags": {
    "docs/WORKSTREAMS.md": { "open": "[OPEN]", "held": "[{holder}]", "done": "[done]" },
    "apps/*/docs/ROADMAP.md": { "open": "todo", "held": "todo (claimed: {holder})" }
  },
  "gates": [
    "bash scripts/check-docs.sh",
    "python3 docs/ux/lint.py --strict"
  ],
  "mirror": {
    "enabled": true,
    "sources": ["docs/DECISIONS.md", "docs/ROADMAP.md", "docs/BUILD_ORDER.md",
                "docs/WORKSTREAMS.md", "docs/superpowers/specs", "docs/superpowers/plans"]
  }
}
```

**A submodule's own config declares only its own registers.** Cross-repository facts
stay in the umbrella — the same boundary [DEC-0171](../../DECISIONS.md) and
[DEC-0172](../../DECISIONS.md) already draw. A service repo that lists
`docs/DECISIONS.md` in its config is a configuration defect and the validator says so.

### 5.2 Environment (never committed, never in the published package)

```
AGENT_SYNC_BACKEND=outline
AGENT_SYNC_OUTLINE_URL=
AGENT_SYNC_OUTLINE_TOKEN=
AGENT_SYNC_OUTLINE_COLLECTION=
```

The published repository contains **no host name and no token**, in any file,
including examples and tests. The validator enforces it (§9).

## 6. Cloud layout

Created by `tree.ensure` on first run, under one container named by
`AGENT_SYNC_OUTLINE_COLLECTION` (or the backend's equivalent).

| Object | Mode | Writer |
|---|---|---|
| `00 Protocol` | manual | human |
| `10 Board` | `doc.put` | generator only |
| `20 Runs/<runId>` | `log.append` | that run |
| `30 Claims` | `log.append` | every agent |
| `40 Reservations` | `log.append` | every agent |
| `50 Signals` | `log.append` | every agent |
| `60 Blockers` | `log.append` | every agent |
| `90 Mirror/…` | `doc.put` | generator only |

### 6.3 Generator safety

A generated object carries, as its **first line**:

```
<!-- agent-sync:generated source=<repo>@<sha> at=<iso8601> — edit in git, not here -->
```

`doc.put` refuses to write an object whose current first line is missing that
marker. So a document a human created or took over is never overwritten; it is
reported instead. This is the make-skill rule *never overwrite user data* applied
to the cloud.

### 6.4 The mirror is a rendering, not a source

`90 Mirror/*` is one-way, generated, and stamped with the source commit SHA. The
board gate compares each mirror stamp with `git rev-parse HEAD` for its source and
fails on drift. Because the mirror has no authority and no independent editor, it
is not a second source of truth — it is a build artifact that happens to live in a
wiki.

## 7. Binding to task-pipeline — LOCKED

`agent-sync` supplies stages; it does not define them. Stage names below are
`task-pipeline` 1.1.1 `references/stages.md`, verbatim.

| Stage | What agent-sync does |
|---|---|
| **0 Intake grill** | Contribute the cloud KB + `10 Board` to the knowledge harvest's source ledger. **Acquire the lease before the brief is committed.** |
| **1 Docs study** | — |
| **2 Brainstorm + decompose** | Journal. Warn if another live run holds an overlapping key. |
| **3 Spec** | Reserve `DEC`/`OQ` ids before they are written to git. Mirror the spec. |
| **4 Plan** | Mirror the plan. Register per-task file ownership for the plan's parallel groups. |
| **5 Dev** | Renew the lease. Journal. Own the submodule-commit → umbrella-gitlink-bump step. |
| **6 Tests** | Journal the suite result. |
| **7 Lint + deploy** | Run `gates[]`; journal each result. |
| **8 Post-deploy** | Journal. |
| **9 Docs + wiki** | **Primary write point.** Flip `DEP-###` states, regenerate `10 Board` and `90 Mirror`. |
| **10 Acceptance** | Close the run, release every lease, write the durable claim tag through to `[done]` / `done`. |

Wired through `pipeline.json` → `skills[]` on stages 0, 3, 4, 5, 9, 10. The
`pipeline.schema.json` contract already permits this; nothing is forked.

**Preflight.** `agent-sync` requires `task-pipeline`. When it is absent, the skill
prints one line and stops — it does not improvise a substitute flow:

```bash
npx sshlg-skills install
```

That installer brings `task-pipeline` (the stages), `super-ux` (this repository has
a linted UX chain, and `task-pipeline` stage 3 requires it for user-facing work),
and `make-skill` (for maintaining `agent-sync` itself), and it prunes the duplicate
plain-copy shadow in `~/.claude/skills/` that otherwise serves a stale skill.

## 8. Hooks — Claude Code only, and said out loud

Contract verified against the current Claude Code hooks reference on 2026-07-29:
`PreToolUse` blocks on **exit 2 with the reason on stderr**, or on exit 0 with

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse",
 "permissionDecision":"deny","permissionDecisionReason":"…"}}
```

The hook receives `session_id`, `cwd`, `tool_name`, `tool_input`, `tool_use_id` on
stdin.

| Event | Matcher | Action |
|---|---|---|
| `SessionStart` | `startup\|resume` | Orient, register the run, print the board summary |
| `PreToolUse` | `Edit\|Write\|MultiEdit` | Deny when the target matches `guardedFiles[]` and no live lease of this run covers it |
| `PreToolUse` | `Bash`, `if: Bash(git commit *)` | Deny when a guarded file is staged without a lease |
| `PostToolUse` | `*` | Renew the lease, throttled to `renewIntervalSeconds` |
| `SessionEnd` | — | Release leases, flush the journal, close the run |

**Hooks exist only in Claude Code.** On Cursor, Codex and the other agents the
skills CLI serves, there is no `PreToolUse` and therefore no gate. `agent-sync`
does not pretend otherwise: on those agents the guard runs as a mandatory
self-check written into the skill body, and the run is recorded on the board as
`ungated`. A reader can tell an enforced run from a promised one.

## 9. Distribution and the validator

Layout per make-skill *Create (distributable)*:

```
agent-sync/
├── .claude-plugin/marketplace.json
├── agent-sync.schema.json
├── plugins/agent-sync/
│   ├── .claude-plugin/plugin.json
│   ├── commands/agent-sync.md
│   ├── hooks/{guard.sh,session-start.sh,renew.sh,session-end.sh}
│   └── skills/agent-sync/
│       ├── SKILL.md
│       ├── references/{adapter-contract.md,lease-protocol.md,backend-outline.md,
│       │               backend-fs.md,pipeline-binding.md,hooks.md}
│       └── scripts/agent_sync.py
├── bin/agent-sync.js + package.json
├── test/validate.py
├── .github/workflows/validate.yml
├── README.md · CHANGELOG.md · LICENSE · CONTRIBUTING.md · SECURITY.md
└── docs/superpowers/{specs,plans}/
```

**One skill**, `agent-sync`, with the entry command **`/agent-sync`** (= plugin
name, per canon). The job is single — *coordinate concurrent agents through a
shared knowledge cloud* — and claiming, journaling and generating the board are
facets of it, driven by the same config, the same log and the same script. An
earlier draft split it three ways; that would have forced the one script and the
two contracts to be triplicated, which is a cost with no matching trigger, since
the pipeline drives every facet through the same entry.

Because there is one skill, every contract and script already sits **inside** the
skill directory. No sibling `references/` exists to arrive broken on agents outside
Claude Code, and no byte-identity rule is needed. The raw-URL fallback is still
stated in the body for copies that arrive without `references/`.

**Validator rules** (`test/validate.py`, stdlib only,
`from __future__ import annotations` — system python3 is 3.9):

1. Spec floor: `name` charset/length/== dir; `description` ≤ 1024 and starts `Use when`;
   `compatibility` ≤ 500; `metadata` all strings; `allowed-tools` a string; no unknown keys;
   body < 500 lines.
2. House: EN **and** RU trigger phrases in every `description`.
3. Version sync ×5: `marketplace.json`, `plugin.json`, `package.json`, top `CHANGELOG` entry,
   `metadata.version` in each `SKILL.md`.
4. No `SKILL.md` outside `plugins/*/skills/*/`.
5. Every `references/` and `scripts/` file sits inside the skill directory, one level deep.
6. No relative link escapes a skill directory; all relative links resolve.
7. **No host identity in `plugins/**`, `bin/**`, `test/**` or any example**: fail on any
   URL that is not `github.com`, `raw.githubusercontent.com`, `code.claude.com`,
   `agentskills.io`, `getoutline.com` or `localhost`.
8. **No credential in argv**: fail on any `-H` / `--header` adjacent to `Bearer`,
   and on any `curl` invocation carrying a token-shaped argument.
9. `agent-sync.example.json` validates against `agent-sync.schema.json`.
10. Negative self-test: corrupt a copy of the tree, expect a non-zero exit. A validator
    that cannot fail is decoration.

CI runs the validator plus `skills-ref validate` for each skill directory as the
upstream tie-breaker.

## 10. The eight contradictions, and what each resolved to

| # | Contradiction | Resolution |
|---|---|---|
| C-1 | Token in `argv` via `curl -H` | All calls go through `curl --config -` fed by heredoc; payloads via `--data-binary @<file>` with mode 600. Validator rule 8. |
| C-2 | Shared `references/` as a sibling directory | Dissolved by collapsing to one skill (§9): every contract and script is already inside the skill dir. Raw-URL fallback still stated in the body. |
| C-3 | Gotchas hidden in references | Three traps live in every `SKILL.md` body: no CAS in the backend; `atomicAppend:false` ⇒ not the lease authority; hooks are Claude-Code-only. |
| C-4 | Generator overwrites human edits | `agent-sync:generated` marker required on line 1 or `doc.put` refuses (§6.3). |
| C-5 | Hooks do not exist outside Claude Code | Declared in `compatibility`; self-check fallback; runs marked `gated` / `ungated` (§8). |
| C-6 | A rollup board contradicts [DEC-0171](../../DECISIONS.md)'s "no status rollup, deliberately" | DEC-0171's objection is to a **hand-maintained** aggregate. `10 Board` is machine-generated, SHA-stamped, drift-gated and refuses hand edits. Recorded as `DEC-0216`, which **refines** DEC-0171 rather than overruling it. |
| C-7 | A third claim vocabulary | Lease = who holds it *now* (ephemeral, TTL). Git tag = who owns the task (durable). `acquire` writes the tag through; `release` clears it. §4.4. |
| C-8 | A submodule config deciding cross-repo facts | A service repo's config declares only its own registers; validator enforces (§5.1). |

## 11. Skill, not MCP server — and the trigger to revisit

make-skill routes "new capability against a live system" to an MCP server. Here the
capability — HTTP with a bearer token — already exists via `Bash`, so this is the
"teach the agent **how**" row: the skill supplies the procedure, the argv-safe
invocation and the adapter contract.

Revisit and extract the adapter into an MCP server when **either** of these lands:
a second backend requiring interactive OAuth, or a requirement that non-Claude-Code
agents get identical enforcement rather than the honest degradation of §8.

## 12. Adoption in this repository

1. `DEC-0216` in [`docs/DECISIONS.md`](../../DECISIONS.md) — the coordination protocol,
   the machine-generated rollup refining [DEC-0171](../../DECISIONS.md), and the
   lease-vs-claim-tag derivation rule.
2. Propagate per the matrix: [`AGENTS.md`](../../../AGENTS.md) §0.1 and §3,
   [`docs/WORKSTREAMS.md`](../../WORKSTREAMS.md), [`CLAUDE.md`](../../../CLAUDE.md).
3. `.env.example` gains the four `AGENT_SYNC_*` keys with empty values
   ([DEC-0055](../../DECISIONS.md) — the env surface is single-sourced there).
4. `.claude/agent-sync.json` and `.claude/settings.json` hooks committed.
5. Both gates green: `bash scripts/check-docs.sh` and
   `python3 docs/ux/lint.py --strict`.

## 13. Definition of done

- `github.com/appvillis-com/agent-sync` public, CI green.
- `test/validate.py` exits 0, and its negative self-test exits non-zero.
- `npx skills add nicegram/agent-sync --list` lists exactly one skill, `agent-sync`.
- `/agent-sync` resolves in this repository and reports status without a token,
  and with a token performs a full acquire → renew → release cycle whose log
  replays to the same holder from a second process.
- A guarded edit without a lease is denied by the hook; with a lease it passes.
- `DEC-0216` recorded and propagated; both repository gates green.

## 14. Human steps

1. Create an API token in the Outline instance's own settings and place it in
   `.env` — the value is never handled here, and `.env` / `.env.*` are already
   ignored by [`.gitignore`](../../../.gitignore).
2. Approve `npm publish` if the package is to own the short name; GitHub +
   `npx github:nicegram/agent-sync` works without it.
