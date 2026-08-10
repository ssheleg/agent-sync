# Board

The work-list between runs. Read at stage 0, written whenever something is deferred out loud.
A row leaves this file when it is done or when it is deliberately dropped — never by going quiet.

Priority is derived, not declared: **what is unverified** outranks **what is untidy**, and
anything that can make the tool report something untrue outranks both.

| ID | Priority | What | Why it is here | Source |
|---|---|---|---|---|
| B-001 | high | Give REQ-20 a check of its own — `merge --key` releases that lease and leaves the others held | Verified by reading the code this run. Every defect the 2026-08-10 audit found was in something verified by reading | audit 2026-08-10 / run of 2026-08-10 |
| B-002 | medium | Decide whether `settleSeconds` stays | It is in the schema, nothing shipped reads it, and `check` now says so. Either an adapter needs it or 2.0 removes it — a knob that only warns is a decision postponed | audit 2026-08-10 (D1) |
| B-003 | medium | `status` now runs the full `check`, which walks the repository for guard globs | On a very large repository that is a `SessionStart` hook doing an `rglob("*")`. Measure it; cache or narrow if it costs more than a second | run of 2026-08-10 (REQ-15) |
| B-004 | low | Blockers were removed as a log nothing wrote | If blockers are wanted they need a real writer, a reader and a place on the board — not a document that exists so the word appears | audit 2026-08-10 (D2) |
| B-005 | low | The run-id resolution walks up to ten `ps` calls when no id is in the environment | ~100 ms per guarded edit today, which is fine. It becomes worth caching if the guard ever runs on more than `Edit`-shaped tools | audit 2026-08-10 (D10) |
| B-007 | low | `actions/checkout@v4` and `actions/setup-node@v4` target Node 20 | Both are being forced onto Node 24 by the runner and annotate every release. They work today; they will stop | release run 31374955487 |
| B-008 | medium | The validator has no way to run as a machine without this family installed | The task-pipeline ordering defect was invisible locally and obvious on CI. A `HOME`-isolated mode would catch that class before the tag, not after | run of 2026-08-10 |

## Closed by the run of 2026-08-10

- REQ-01 … REQ-19, REQ-21 — see `verification.md` for what verifies each one now.
- **B-006 — publish.** Done: `v1.7.0` tagged and released, npm moved 1.4.3 → 1.7.0, GitHub release
  created, npx smoke green from a clean directory, every local channel on this machine updated.
  1.5.0 through 1.6.0 stay unpublished on purpose — they carry the defects 1.7.0 fixes.
- **The release tract itself.** Two defects found while shipping: the CHANGELOG notes extraction
  never matched the `## vX.Y.Z` heading style, which is why `v1.5.0`, `v1.5.1` and `v1.5.2` each
  pushed a tag and published nothing; and `status` reported the task-pipeline gate before the setup
  verdict, hiding project defects on every machine without the dependency. Both fixed, both with a
  check — `check_release_notes_are_extractable` runs the workflow's own awk program.
