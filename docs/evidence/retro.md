# Retrospective — agent-sync

## Run stamps

| Date | Commit | What ran |
|---|---|---|
| 2026-08-10 | `1f1f7b9` | Audit of 1.5.2, then 1.5.3 → 1.6.0 → 1.7.0, released and published |
| 2026-08-10 | `7457c52` | Second audit along the agent-usage axis → 1.7.1 |
| 2026-08-10 | `18b29b8` | Board cleared → 1.8.0 |
| 2026-08-19 | `36a3b38` | AS-01: a run reports what it leaves behind — manifesto M-49, M-50 |

## Standing instructions

Read in full at stage 0. Each one is retired when it becomes a mechanical check, when the paths it
names are gone, or when it has not fired in five run stamps or sixty days. Hard cap: ten.

1. **A check that has never been watched fail is not evidence.** Every check in `test/validate.py`
   has a self-test fixture that plants its defect back. Adding a check without one is adding a
   green light with no bulb. *(Became partly mechanical in 1.5.3 — the self-test enumerates 43
   fixtures as of 2026-08-19, counted by running it — but "add the fixture with the check" is
   still a habit, not a gate.)*
2. **Test the composition, not only the unit.** Every defect the 2026-08-10 audit found lived in
   the gap between two things already tested separately: the allocator was correct and `reserve`
   never asked it about the whole log; `lease_guarantee` was quoted by every surface while `renew`
   refreshed nothing. When a function is tested, ask what calls it and whether *that* is tested.
3. **Run the commands before writing about them.** The audit that found six shipped defects did it
   by executing `init`, `check`, `reserve`, `renew`, `merge` and reading the output — not by
   reading the source. The validator was green throughout.
4. **Run them somewhere that lacks your conveniences.** This machine has the whole skill family
   installed, so `status` never reached its task-pipeline gate here and a defect behind that gate
   was invisible for a full day of green local runs. CI found it in sixteen seconds. When a check
   passes locally, ask what this box has that a runner does not.
5. **Print success only after it is established.** `record`, `release-id`, `merge` and `board` each
   printed a result before, or without, checking that it had been achieved. An agent quotes stdout.
6. **When one fact lives in two files, one of them is already wrong.** `CONFIG_KEYS` vs the schema,
   the stage numbers in three documents, the guard's glob vs `check`'s. The fix is a single source
   plus a check that the others quote it — `lease_guarantee()` is the pattern to copy.
7. **`SKILL.md` is at its token ceiling.** Every addition must displace something. Move the *why*
   into `references/` and leave the rule in the body; the budget is a real constraint, not a lint.
8. **A doctrine change is not done until the generators emit it.** `setup_snapshot()` and the
   `*_SEED` templates write documents into other people's projects, and those are what agents read
   first. Four releases changed the branch doctrine and none of them touched the templates. When a
   rule changes, grep the generators before closing the task.
9. **A predicate cannot report the condition it folds into its answer.** `held()`,
   `_lease_holder()` and `all_holdings()` each apply the TTL inside the read, so *expired* and
   *absent* were one answer and seventeen expired locks were reported by no command at all. When
   a reader collapses two states into one, ask which command is supposed to tell them apart —
   and then whether any command can.

## Entries

### 2026-08-19 (AS-01) — the logic existed, and nothing could reach it

**Symptom.** Seventeen lock files across the nine repositories of this family, every one expired,
the oldest by three days. `status` printed `leases held: none` in a checkout holding three of them;
`finish` printed `✓ no lease left held` beside a two-day-expired one. Both statements were true.

**Surfaced at.** A cross-repository audit against the Proof-of-Done manifesto (M-49, M-50) —
nothing in this repository was looking.

**Owned by.** The readers, not the reaper. The audit's first reading was "the reaping logic exists
and nothing calls it". `release()` calls it — for the one key an operator names, and an operator
whose session is gone names nothing. What did not exist was an **enumerating** read.

**Root cause.** Three readers, one shape: each folds the TTL into the read and answers `None`.
That is right for exclusion — an expired lease is not held — and it makes *expired* and *absent*
the same answer, so no command could tell a clean directory from one full of corpses. The defect
was in none of the three; it was in the absence of a fourth.

**Fix, by grade.** Mechanical: `classify_lock()` — pure, four verdicts, ten fixtures — plus
`residue` and `reap`, and a residue block in `status` and `finish`; five checks with five planted
defects (38 → 43 fixtures). Teardown is verified by re-reading the directory and comparing
`(run, ts)`, because `unlink` returns nothing and raises nothing where the entry survives the call.
Doctrinal: the reapable / foreign / ambiguous split, and the clause that carries it — **a matching
run id is not proof while this run's identity is the shared fallback**, because `run_id()` hands
that entry to every session in the checkout. Measured on the real data: all seventeen locks bear a
run id equal to their checkout's recorded id, and all seventeen are reported rather than cleared.

**The check that catches it next time.** `check_status_reports_expired_locks_as_residue`,
`check_residue_ownership_must_be_provable`, `check_reap_verifies_teardown_by_re_reading`,
`check_finish_reports_what_the_run_leaves_behind` — and standing instruction 9.

**What it cost to find.** Nothing a session ending in `finish` would not have shown, if `finish`
had been able to see it.

### 2026-08-10 (board clearing) — standing instruction 4 fired, twice, in one day

**Symptom.** Two checks written this run passed locally and failed on CI, both because this
machine has something a runner does not. `check_status_reports_the_setup_verdict` passed because
the skill family is installed here. `check_merge_releases_only_its_key` passed because
`~/.gitconfig` supplies a git identity here, so anything the tool itself commits has an author.

**Surfaced at.** The release run, both times — after the tag was pushed.

**Owned by.** The fixtures. Neither defect was in the product: `merge` refused correctly and
restored the branch, with an accurate message. The tests were the things that only worked here.

**Root cause.** A fixture that inherits the developer's environment is testing the developer's
environment. The first fix was to read `HOME`; the second needed `GIT_CONFIG_GLOBAL` and
`GIT_CONFIG_SYSTEM` too, because unsetting the *repository* keys still left the global file
answering. Standing instruction 4 named this class after the first one and did not prevent the
second — because it said *ask what this box has*, and the answer the second time was a different
thing entirely.

**Fix, by grade.** Mechanical: `check_commands_work_without_the_family_installed` proves its own
isolation before asserting; `check_merge_refuses_without_an_identity` isolates all three config
scopes; `_git_project` gives every fixture a repository identity the way a real repository has one.
Product: `merge` now checks for a committer identity in its preflight, where the rest of its checks
already live.

**The check that catches it next time.** The two above — and the honest note that they catch two
*specific* absences, not the class. The class is caught by CI, which is why a release that fails
there is information rather than an obstacle.

**A finding worth keeping.** Planting "a live lease can be taken by a second run" needed two
mutations, not one: breaking `acquire`'s expiry check alone still refuses the steal, because
`_steal_expired` re-reads the expiry inside the critical section. The single-point fixture was
MISSED, and that miss is the evidence that the exclusion has two independent layers.

### 2026-08-10 (second pass) — the code was right and the instructions were two versions old

**Symptom.** A repeat audit along a different axis — *can an agent follow this skill and get a
correct result* — found the coordination core clean and the agent-facing surface wrong in four
places. The worst: `setup` and `scaffold`, the two commands that write documents **into a user's
project**, generated a workflow with no mention of branches or `merge`, and stated the claim tag is
written through to git unconditionally — false on any branch since 1.4.0, which is where the
doctrine says the work belongs. An agent doing exactly what the skill says — read the generated
snapshot first, it describes how this project is wired — got the 1.3 workflow.

**Surfaced at.** Stage 0 again, by running the scenarios instead of reading them: cold start, the
adoption chain, the per-task cycle, two agents on one task, every hook with a real payload.

**Owned by.** The generators. Every one of the four fixes shipped in 1.4.0–1.7.0 updated
`SKILL.md` and the references — the documents the *author* reads — and none of them updated the
templates the *tool emits*. The doctrine and its generator drifted apart with nothing between them.

**Root cause.** The same shape as the first audit, one layer out. There, a guarantee was described
in prose and not implemented in code; here, a doctrine was implemented in code and not carried into
the artifacts the code writes. Both are "one fact, two homes, and only one of them was maintained" —
and in both cases the unmaintained copy was the one the reader actually trusts.

**Fix, by grade.** Mechanical: `check_generated_docs_carry_current_doctrine` fails when the
generated snapshot or the seeded `AGENTS.md` stops naming `merge`;
`check_every_advertised_verb_exists` runs the argument-hint against the real argparse choices;
`check_registers_need_a_backend_that_can_reserve`; `check_skill_gives_a_resolvable_script_path`.
Structural: `AGENTS.md` no longer restates the cycle at all — it is seeded once and never
overwritten, so any protocol copied there is frozen forever; it now points at the snapshot, which
regenerates and which `check` fails on when stale.

**The check that catches it next time.** The four above. Standing instruction 8.

**What it cost to find.** Nothing that a first-time user would not have hit on their first task.

### 2026-08-10 — the validator was green while six shipped guarantees were false

**Symptom.** `python3 test/validate.py` printed `PASS: agent-sync v1.5.2 — all checks green` on a
release in which `reserve` handed three concurrent runs `DEC-0007`, `renew` refreshed no lease,
`check` rejected the config `init` had just written, `release-id` reported success while doing
nothing, the guard denial named an unrelated holder, and the marketplace listing sold the lease
doctrine the skill's own first trap forbids.

**Surfaced at.** Stage 0 of an audit that had no brief beyond "look with fresh eyes" — by running
the commands in a throwaway repository.

**Owned by.** The test suite. Not one of the six was invisible; every one reproduced in under a
minute once a command was actually executed.

**Root cause.** The validator tested *units* and *wording*. `check_reserve_respects_the_register`
exercised `resolve_reservations`, which was correct; the defect was in `reserve`, which never
passed it the whole log. `check_lease_report_agrees` asserted that three commands quote one
sentence about what a lease is worth; nothing asserted the lease behaves that way. Both are good
checks. Neither could see the gap between them, and a suite made only of such checks reports green
with confidence proportional to its coverage of the wrong thing.

**Fix, by grade.** Mechanical: eighteen new checks that drive the real commands from more than one
identity (17 → 35 in `main()`), plus fourteen new self-test fixtures planting each defect back
(14 → 28). Standing instructions 1–4 above, for the habits the checks cannot encode.

**The check that catches it next time.** `check_reserve_is_race_free`,
`check_renew_extends_the_lease`, `check_config_round_trip`, `check_no_success_on_failed_publish`,
`check_steal_is_atomic`, `check_merge_refuses_stale_target`, `check_unparseable_log_fails_loudly`,
`check_status_reports_the_setup_verdict`, `check_watermark_survives_a_late_entry`,
`check_claim_round_trip_is_byte_exact` — all in `test/validate.py`, all with a planted-defect
fixture.

**What it cost to find.** Nothing was wrong with the code that could not be seen by running it. The
release had shipped through CI four times.
