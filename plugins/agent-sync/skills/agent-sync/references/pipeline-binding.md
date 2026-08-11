# Binding to task-pipeline

**Read this when** wiring `pipeline.json`, or adding a stage hook.

## Contents

- [The numbers, once](#the-numbers-once)
- [Where it plugs in](#where-it-plugs-in)
- [pipeline.json](#pipelinejson)
- [What must be guarded](#what-must-be-guarded)
- [Preflight](#preflight)
- [Gate expressions](#gate-expressions)


`agent-sync` supplies stages; it does not define them. The stage names below are
`task-pipeline`'s own — do not rename, renumber or fork them.

## The numbers, once

<!-- agent-sync:stages rules=0,1,3,9,10 wired=0,1,3,4,5,9,10 -->

Two different questions, and answering them in one list is why three documents once gave
three answers:

- **Stages carrying a rule — 0, 1, 3, 9, 10.** Something must happen there or the run is
  wrong, and each rule is about ordering. Quoted in `SKILL.md`.
- **Stages wired into `pipeline.json` — 0, 1, 3, 4, 5, 9, 10.** The list above plus the two
  where the journal has teeth (file ownership, submodule pointers). Quoted in the README.

`SKILL.md` said "four of the eleven stages" and then listed five; the README named a third
set; and this file called stage 1 *"nothing shared to coordinate"* — the stage `reconcile`
belongs to. The marker above is the source, and the validator fails when a surface stops
agreeing with it.

## Where it plugs in

| Stage | Calls | Why there and not elsewhere |
|---|---|---|
| **0 Intake grill** | `status`, then `acquire <KEY>` | The cloud KB and the board join the harvest's source ledger. The lease is taken **before the brief is committed**, or two agents write two briefs for one task |
| **1 Docs study** | `reconcile`, then resolve every divergence | The git documents say how it *should* be, the as-built record how it *is*. Building on an unresolved divergence is writing code against a system that does not exist — and this is the last stage where that costs nothing |
| **2 Brainstorm + decompose** | `journal` | Also warns when a live run holds an overlapping key — cheapest moment to find the overlap |
| **3 Spec** | `reserve <REG>` per id | Ids must be reserved *before* they are written to git. Reading "next free id" is not reserving it |
| **4 Plan** | `journal` with the plan's file ownership | Parallel groups that write one file are a merge conflict scheduled for later |
| **5 Dev** | `renew` (automatic), `journal` | Also the owner of the submodule-commit → parent-gitlink bump. Nobody else has both repos in hand |
| **6 Tests** | `journal` | The suite result is evidence, and evidence belongs in the run |
| **7 Lint + deploy** | `journal` per gate | — |
| **8 Post-deploy** | `journal` | — |
| **9 Docs + wiki** | `signal` per dependency flip, then `board` | The main write point. The pipeline already updates docs here; the board is regenerated from what it wrote |
| **10 Acceptance** | `merge` when the work is on a branch — it records the merge and releases; otherwise `release` every lease and write the claim tag through | A run that ends without releasing looks alive until its TTL expires |

## pipeline.json

`task-pipeline`'s `pipeline.schema.json` already permits this; nothing is forked.
Add `agent-sync` to `skills[]` on the six stages that call it, and append its
clause to each of those stages' existing `gate.check`:

```json
{
  "stages": [
    { "id": 0,  "state": "intake",      "name": "Intake grill",
      "skills": ["task-pipeline:grill", "agent-sync"],
      "gate": { "type": "manual", "check": "<the stage's own criteria> AND the lease for this task is held before the brief is committed" } },
    { "id": 1,  "state": "docs-study",  "name": "Docs study",
      "skills": ["task-pipeline:knowledge", "agent-sync"],
      "gate": { "type": "manual", "check": "<the stage's own criteria> AND `reconcile` ran and every divergence it named is resolved or recorded as standing" } },
    { "id": 3,  "state": "spec",        "name": "Spec",
      "skills": ["task-pipeline:spec", "agent-sync"],
      "gate": { "type": "manual", "check": "<the stage's own criteria> AND every id the spec writes was reserved first" } },
    { "id": 4,  "state": "plan",        "name": "Plan",
      "skills": ["task-pipeline:planning", "agent-sync"],
      "gate": { "type": "auto",   "check": "<the stage's own criteria> AND no two parallel tasks write one file" } },
    { "id": 5,  "state": "dev",         "name": "Dev",
      "skills": ["task-pipeline:build", "agent-sync"],
      "gate": { "type": "auto",   "check": "<the stage's own criteria> AND the lease is live and submodule pointers are current" } },
    { "id": 9,  "state": "docs-wiki",   "name": "Docs + wiki",
      "skills": ["task-pipeline:documentation", "task-pipeline:gates", "agent-sync"],
      "gate": { "type": "auto",   "check": "<the stage's own criteria — the propagation sweep and a green documentation gate with its ratchets printed> AND the board is regenerated with no mirror drift" } },
    { "id": 10, "state": "acceptance",  "name": "Acceptance",
      "skills": ["task-pipeline:acceptance", "agent-sync"],
      "gate": { "type": "manual", "check": "<the stage's own criteria> AND every lease is released and every claim tag written through" } }
  ]
}
```

**Three things in that JSON are contract, not style.** `state` is **required** by
`pipeline.schema.json`, `id` is an **integer**, and the human label is `name` — not
`title`. This example carried `"id": "0"` with a `title` and no `state` until
2026-08-03, while claiming the schema permitted it; anyone who copied it got a config
`task-pipeline` rejects.

**And the gate text EXTENDS, never replaces.** Each `check` above is
*`<the stage's own criteria>` **AND** agent-sync's clause* — written out that way on
purpose. An earlier version of this file stated only agent-sync's half, so a host
that copied it silently dropped the stage's real gate: stage 9 lost the propagation
sweep and the documentation gate, stage 10 lost the ladder walk and the evidence
rule.

Stages 2, 6, 7 and 8 keep their own `skills[]`; `agent-sync` only journals there,
which needs no wiring.

## What must be guarded

`guardedFiles` is every shared file two agents could write in the same minute — and
since the pipeline's documentation track it is longer than the registers:

| File | Why it is shared state |
|---|---|
| the decision register (`docs/DECISIONS.md` or `docs/adr/`) | append-only; a concurrent write loses an entry |
| `docs/OPEN_QUESTIONS.md`, `docs/ROADMAP.md` | the same |
| **`docs/DOCMAP.md`** | seeded into every project the pipeline touches; holds the registers, the propagation matrix and the ratchet floors. Losing it loses the map |
| **`docs/superpowers/retro.md`** | capped at ten standing instructions, so a concurrent write silently **drops a lesson** instead of conflicting visibly |

The schema keeps `agent-sync.json` to known keys, which is why this reasoning lives
here and not as a comment in the config.

## Preflight

`task-pipeline` is required. When it is absent, print the install line and **stop** —
do not improvise a substitute flow, because without those stages there is nothing to
bind to and the result is ad-hoc work wearing a pipeline's vocabulary.

```bash
npx sshlg-skills install
```

That installer also brings `super-ux` (its stage-3 UX track is required for
user-facing work) and `make-skill`, and it prunes the duplicate plain-copy shadow in
`~/.claude/skills/` that otherwise serves a stale skill over the installed plugin.

## Gate expressions

Each `check` above is verified by the coordinator, not by prose — and since 1.3.0 the repository
half of that verification is a command rather than a promise: **`agent_sync.py finish`** runs the
pointer, cleanliness, pushed-ness and lease checks, and `finish --gates` adds the project's own
declared gates. Until then this table described work nothing performed.

| Check | How it is decided |
|---|---|
| lease held | replay the log; holder == this run |
| every id reserved | every `DEC-`/`OQ-`/`DEP-`-shaped token new in the diff has a `reserve` line in this run |
| no two parallel tasks write one file | intersect the file lists journaled at stage 4 |
| submodule pointers current | `git submodule status` reports no `+` prefix — `finish` |
| every repository pushed | no commits ahead of upstream anywhere, parent included — `finish` |
| board regenerated, no drift | each mirror stamp equals `git rev-parse HEAD` for its source |
| every lease released | replay the log; this run holds nothing |
