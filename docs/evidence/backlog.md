# Board

The work-list between runs. Read at stage 0, written whenever something is deferred out loud.
A row leaves this file when it is done or when it is deliberately dropped — never by going quiet.

Priority is derived, not declared: **what is unverified** outranks **what is untidy**, and
anything that can make the tool report something untrue outranks both.

| ID | Priority | What | Why it is here | Source |
|---|---|---|---|---|
| **AS-06** | unverified | **The guard never sees a Bash write.** `hooks.json` matches `Edit\|Write\|MultiEdit\|NotebookEdit` and `Bash(git commit *)`, so a guarded file written with `cat >`, `sed -i` or `tee` reaches disk unchecked — and `GUARD_SHAPES` has no such case, so the suite agrees with the gap. | A project can be described as protected while its most convenient write shape is not. Closing it means matching ALL Bash and parsing a shell command for write targets, which is a decision, not a patch — hence a row rather than a fix. | this run, 2026-08-25; filed in `fabric` as CO-050 |

## Closed by the verification run of 2026-08-20 — both halves exercised on a real remote

| ID | What closed it |
|---|---|
| AS-01a | **Verified, not asserted.** A git-mode checkout with **zero** local notes — the state a ref won on another machine leaves behind — was built against a local bare remote, a lease taken at `ttl 2`, and the note deleted. `residue` then printed *nothing in the lock directory* AND enumerated the git plane: `refs/agent-sync/leases/DEMO-KEY @ 495370743e`, run `r-rverifya`, **expired 18s ago**, `foreign`, closing with *1 ref(s) on the remote, 0 this run can prove it owns and has spent*. That is the report the row said was missing, on a remote rather than in a fixture. |
| AS-01b | **Verified on live residue and on both reap branches.** In `~/DATA/0xDEV`, a real expired foreign lock (`BLOG-SITEMAP`, run `r-blog-1429e`, **expired 4d 22h ago**): `reap` named it, said *left alone — it belongs to run r-blog-1429e, not to this one*, and **deleted nothing** — both lock files byte-identical after. On the git plane the positive branch was exercised too: reaping as a different run left the ref standing (1 on the remote), reaping as the run that took it removed it and said *confirmed gone by re-reading the remote* (0). The classifier's narrow proof holds in both directions. |

## Closed by AS-04 (2026-08-20) — the claim tag stops outliving its lease

| ID | What closed it |
|---|---|
| **AS-04** | **[ssheleg/agent-sync#5](https://github.com/ssheleg/agent-sync/issues/5)** — open since 2026-08-17, reproduced verbatim at v1.14.0: `release <KEY>` against a board carrying `(claimed: r-…)` with no lock behind it printed `released <KEY>`, exited 0 and left `git diff --stat` empty; `residue` said "nothing on disk"; `reconcile` never mentioned it. Two lines: `write_claim`'s `if saved is None: continue` and `claim_divergence`'s `if not held: return out` — the second gated divergence reporting on holding a lease, so the one shape that needs reporting most was structurally invisible. Closed the way #4 was: **one notion of held, consulted by both planes.** `_lease_holder` is the single reader; `orphan_claims()` classifies a tag as `orphan` (no live lease) or `disputed` (the lease is live under another run, so it is reported and never touched); `release` clears an orphan and names the run it named; `status`, `residue` and `reconcile` all report one. Seven fixtures in `test/claim_cell_test.py`, `check_a_claim_tag_cannot_outlive_every_command` and three self-test plants in `test/validate.py` |
| AS-03 | **`host` is written in both lease modes.** `classify_lock` consumes `host` at `:921` and only `_git_acquire` wrote it, so in `local` mode the classifier had one fewer way to refuse — on 25 of the 25 locks this family had on disk (`find ~/DATA/sshlg-skills -path '*/.agent-sync/leases/*.lock' \| wc -l` → 25, none carrying `host`, 2026-08-20). The `local` lease is machine-local by construction; the FILE is not, and a checkout on a synced directory is read by two machines. `check_local_locks_record_their_host` asserts the payload through the CLI, `two machines are separated in local mode (AS-03)` asserts the classifier verdict, and the plant `a local lock records no host` is watched failing. Locks written before this change carry none, and absent stays legal |
| AS-05 | **The ledger described an unshipped artifact.** `verification.md:11` headed its newest section "(in tree, unreleased)" and `:22` cited `PASS: agent-sync v1.13.0` while v1.14.0 was tagged, in `package.json` and in the CHANGELOG. `check_ledger_names_the_shipped_version` now compares the newest section against `git describe --tags` (falling back to `package.json`, which the self-test's `.git`-less copy needs, and refusing when the two disagree) and refuses an "unreleased" claim about a version the CHANGELOG already carries. Two plants |
| AS-06 | **Two divisors for one token budget.** make-skill's shipped auditor measures 3.9 chars/token and refused this body at ~5084 tokens; `test/validate.py` divided by 4 and passed it at 4957. The family's auditor is the authority, so 3.9 is adopted here, and the body is **18258 chars / ~4681 tokens** — inside the 5000 budget *and* the 4750 house working limit, `0 GAP, 14 PASS` from `audit_skill.py --house`. Brought under by a split, not only a trim: the generated-object contract moved to `references/two-sources.md` and the duplicated `## Backends` table was dropped for the References table that already carried the same three triggers |

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
