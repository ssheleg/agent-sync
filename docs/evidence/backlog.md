# Board

The work-list between runs. Read at stage 0, written whenever something is deferred out loud.
A row leaves this file when it is done or when it is deliberately dropped — never by going quiet.

Priority is derived, not declared: **what is unverified** outranks **what is untidy**, and
anything that can make the tool report something untrue outranks both.

| ID | Priority | What | Why it is here | Source |
|---|---|---|---|---|
| AS-01a | **can report something untrue** | `residue` does not enumerate the git lease mode's refs | The enumerating read walks `.agent-sync/leases/*.lock`, which BOTH modes write — `_note_local` leaves a note whenever a ref is won here. A ref won on another machine has no local note, so `residue` in git mode can print "nothing on disk" while `refs/agent-sync/leases/<key>` sits expired on the remote. Reaping one needs the same `--force-with-lease` compare-and-swap `_git_release` uses | AS-01, 2026-08-19 |
| AS-01b | **unverified** | The 17 expired locks in the family are reported and still there | This row's mechanism classifies all 17: `foreign` to any fresh session, `ambiguous` to a plain shell, `reapable` only by the session that took them — so no run today can clear one, by design. Whether they are reaped is the orchestrator's decision; 14 of the 17 live in repositories this row must not touch | AS-01, 2026-08-19 |
| AS-03 | *deferred, not this repo's row* | A `local` lock carries no `host`, so residue cannot tell a lock written on another machine from one written here | Named here only so the gap is not read as an oversight in AS-01: adding `host` to the local payload is a change to the lease contract, which is AS-03's ground (the `local` backend double-winning across machines). Residue uses `host` where the git mode writes it | AS-01, 2026-08-19 |

## Closed by AS-01 (2026-08-19) — a run reports what it leaves behind

| ID | What closed it |
|---|---|
| AS-01 | **M-49 and M-50 of the Proof-of-Done manifesto.** `status` and `finish` enumerate expired locks as residue; `residue` prints them in full; `reap` clears only what this run can PROVE it owns and has spent, and reports foreign or ambiguously owned locks untouched. Teardown is verified by re-reading the lease directory and comparing `(run, ts)`, never by the delete's return value. Five checks and five self-test fixtures — 38 → 43 — in `test/validate.py`; doctrine in `references/lease-protocol.md`, and the snapshot generator emits it |

## Closed by the board-clearing run of 2026-08-10

| ID | What closed it |
|---|---|
| B-001 | `check_merge_releases_only_its_key` — two leases held, one landed, the other must still be held |
| B-002 | **Decision: it stays.** `settleSeconds` is the adapter contract's extension point for a store whose writes are not immediately readable. Removing it would fail every config that carries one to buy nothing; `check` already says nothing shipped reads it. Written into `references/adapter-contract.md` so the next reader finds the reasoning, not the silence |
| B-003 | Measured, then fixed: `status` on a 20 000-file repository with five guarded patterns took **3.2 s** because the tree was walked once *per pattern*. One walk, reusing git's index where it exists — **0.5 s**, same output |
| B-004 | **Decision: blockers stay gone.** The concept needs a writer, a reader and a place on the board before the word earns a document. Nothing is worse than a page that reads "no blockers" because nothing ever writes one |
| B-005 | Measured: **130–220 ms** per guarded edit with no run id in the environment, against a `PreToolUse` budget of 20 s. Acceptable; no change. Recorded so the next person does not re-derive it |
| B-007 | `actions/checkout@v5`, `actions/setup-node@v5`, `actions/setup-python@v6` |
| B-008 | `check_commands_work_without_the_family_installed` — runs with `HOME` redirected, and proves the isolation on a healthy project before asserting on a broken one |
| B-009 | `check_two_agents_cannot_share_one_task` and `check_guard_covers_every_write_shape` — nine payload shapes, both with and without the lease |
| B-010 | Self-test runs each fixture as its own process, eight at a time: **6 min → 2.8 min**, and the global-state reset that made parallelism impossible is gone |

## Closed by the earlier runs of 2026-08-10

- REQ-01 … REQ-19, REQ-21 — see `verification.md` for what verifies each one now.
- **B-006 — publish.** Done: `v1.7.0` tagged and released, npm moved 1.4.3 → 1.7.0, GitHub release
  created, npx smoke green from a clean directory, every local channel on this machine updated.
  1.5.0 through 1.6.0 stay unpublished on purpose — they carry the defects 1.7.0 fixes.
- **The release tract itself.** Two defects found while shipping: the CHANGELOG notes extraction
  never matched the `## vX.Y.Z` heading style, which is why `v1.5.0`, `v1.5.1` and `v1.5.2` each
  pushed a tag and published nothing; and `status` reported the task-pipeline gate before the setup
  verdict, hiding project defects on every machine without the dependency. Both fixed, both with a
  check — `check_release_notes_are_extractable` runs the workflow's own awk program.
