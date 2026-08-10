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
| B-006 | operator | Publish 1.5.0 → 1.7.0 | npm carries 1.4.3; four releases are tagged nowhere. Publishing is a human step by design: a `v*` tag arms the release workflow | run of 2026-08-10 |

## Closed by the run of 2026-08-10

REQ-01 … REQ-19, REQ-21 — see `verification.md` for what verifies each one now.
