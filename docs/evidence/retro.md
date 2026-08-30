# Retrospective — agent-sync

## Run stamps

| Date | Commit | What ran |
|---|---|---|
| 2026-08-10 | `1f1f7b9` | Audit of 1.5.2, then 1.5.3 → 1.6.0 → 1.7.0, released and published |
| 2026-08-10 | `7457c52` | Second audit along the agent-usage axis → 1.7.1 |
| 2026-08-10 | `18b29b8` | Board cleared → 1.8.0 |
| 2026-08-19 | `36a3b38` | AS-01: a run reports what it leaves behind — manifesto M-49, M-50 |
| 2026-08-20 | `77deede` | AS-01a: the git plane is swept, not only disclosed |
| 2026-08-20 | `019867b` | AS-01b: an ambiguous lock became clearable by a person, and 28 were cleared |
| 2026-08-25 | *(this run)* | AS-05: the Notion backend, measured four times before it said anything true — 1.18.0 |
| 2026-08-29 | *(this run)* | ASY-05: the pipe the guard's comment claimed and never consumed; installers settle the Claude channel instead of deleting it blind — 1.18.5 |
| 2026-08-30 | *(this run)* | Wave 2: ASY-07 fail-closed without python3, ASY-08 `clear` in the matcher, ASY-06 remedy, ASY-01 bytecode; ASY-04 found already shipped in 1.18.5 — 1.18.6 |

## Standing instructions

Read in full at stage 0. Each one is retired when it becomes a mechanical check, when the paths it
names are gone, or when it has not fired in five run stamps or sixty days. Hard cap: ten.

1. **A check that has never been watched fail is not evidence** — and one that has never been
   watched SUCCEED is half a check. Every check in `test/validate.py` has a self-test fixture
   that plants its defect back. Adding a check without one is adding a green light with no bulb.
   The other half was found on 2026-08-25: every assertion about the Notion retry loop drove a
   transport that always failed, so nothing measured whether a retry can succeed. A retry branch
   with no success-after-retry case is a branch that only fails more slowly, and a live run is
   where you discover it. *(Became partly mechanical in 1.5.3 — the self-test enumerates the
   fixtures, counted by running it — but "add the fixture with the check, in both directions" is
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

10. **A fixture asserting a LIMITATION dies with the limitation.** Before implementing a
   change that removes a documented gap, grep the test corpus for the gap's own wording —
   `INCOMPLETE IN THIS MODE`, `not yet`, `still open` — because that string is the index of
   every check that will go red. Three repositories hit this in one day (2026-08-20:
   `sshlg-skills` ownership, `sheleg-design` release plants, `agent-sync` AS-01a), each
   time discovering it from a red suite rather than from the plan.
   *Retires when a check enforces it, or when three consecutive runs remove a limitation
   with no fixture found by the grep.*


## Entries

### 2026-08-25 (AS-05) — the measurement had to fail three times before it measured anything

**Symptom.** The Notion adapter's capability flags passed their live measurement on the
first run — 200 appends from two processes, 200 lines — and that PASS was worth nothing.
Run two returned 171 lines with a dead writer. Run three returned 100, all from one
writer, with both writers exiting 0. Only run four was true, and by then two defects and
one design error had come out of it.

**Where it surfaced.** Stage 5, against a live workspace. **Where it belongs.** Stage 2:
the decomposition treated "measure the flags" as one task with one outcome, when the
measurement is a piece of software with its own defects and its own untested paths.

**What run two found.** `TimeoutError` is an `OSError`, not a `URLError`. A read that
times out **after** the connection is established therefore fell past both retry branches
into the catch-all and failed permanently — while the contract calls a transport error
retryable. It did not appear in run one because run one never hit the workspace rate
limit; run two did, on a budget run one had already spent. **The path that handles
pressure is only exercised under pressure**, and a first green run against a fresh quota
proves the happy path and nothing else.

**What run three found, and it is about the test.** Both writers called `tree.ensure` on
one title, the check-then-create raced, and each wrote 100 lines to its own page of that
name. Real — and unreachable in this protocol, because `Sync.log_id()` gives every run its
own shard, *one writer per document, always*, precisely because Outline's append was not
atomic. The measurement had spent its evidence on a scenario the product cannot create,
and reported `atomicAppend is FALSE` for a cause that was neither. It now hands the
contention case a page id, and measures instead the thing the sharded design does depend
on: two fresh shards, both enumerated. That is the Outline defect — eight processes, eight
winners, because its search index did not return a new document — and Notion's structural
listing passes it.

**Root cause, one shape under all three.** Every assertion in the suite drove a transport
that always failed. Attempt counts were checked; **a retry succeeding was not**, because
nothing in the corpus could express it. The success path existed only in production, and
production is where each of these was found.

**The fix, by grade.** Mechanical: retry `TimeoutError` twice with backoff; a check that
drives a planted failure followed by a real answer, for a rate limit and for a timeout,
with `notion gives up on a read timeout` planting it back. Structural: the live test hands
the page id for contention and adds the shard-enumeration case, reports a dead writer as
INCONCLUSIVE rather than as a false flag, and prints the last 2000 characters of a
writer's stderr — the first version truncated at 400 and cut the traceback off at the
frame header, so the one run that did crash reported a stack with no exception in it.
Doctrinal: standing instruction 1 now covers both directions.

**What it cost to find.** Four live runs and about six minutes of Notion's rate limit.
What it would have cost not to: `atomicAppend: true` shipped on the strength of one green
run against a fresh quota, and a first user hitting a slow response would have watched a
writer die and a lease go unrenewed with no idea why.


### 2026-08-20 (AS-01b) — the override's two defects came from real state, not from fixtures

**Symptom.** Five fixtures passed and the command still could not do the job it exists for.
Run against the 28 locks this machine had actually accumulated, `reap --i-own-this` hit a key
called `-claude-plugin-marketplace-json` — a guarded-file path slugified by this very tool —
and argparse read the leading dash as a flag. The one command able to clear that lock could
not name it.

**Where it surfaced.** After the gate was green, applying the mechanism to the state that
filed the row. **Where it belongs.** Stage 5: the fixtures were written from the row's
description, and the row described the count rather than the key shapes.

**Root cause.** Every fixture key was `B-nn`. The tool writes two other shapes — a slugified
guarded-file path, and a task id an operator typed — and neither was in the corpus. A fixture
set drawn from the prose covers what the prose noticed.

**The second one is mine and smaller.** The shell loop I wrote around the command was wrong
where the command was right: it reported "no lock by that name" for locks `residue` was
listing one line above. The command run directly cleared them. A wrapper that disagrees with
the thing it wraps is worth one minute of checking which of the two is lying — and it was the
wrapper.

**The fix, by grade.** Mechanical: a fixture for a key that begins with a dash, and `--` named
in the command's own help. Standing instruction 10 already covers the wider lesson from the
other direction, so this is an entry rather than an eleventh rule.

### 2026-08-20 (AS-01a) — the fixture asserted the half-closed state, for the third time in one day

**Symptom.** Closing the sweep made a passing fixture fail:
`residue states what it cannot see in git mode (AS-01a)` asserted the string
`⚠ INCOMPLETE IN THIS MODE`, which is exactly what the fix removes. The check was right when
it was written — the sweep did not exist — and became a check that a working feature is
still missing.

**Where it surfaced.** Stage 5, on the first run of the suite after the implementation.
**Where it belongs.** Stage 2: a change that closes a disclosure owns the fixtures asserting
that disclosure, and nothing in the decomposition asked which ones those were.

**Root cause.** A fixture may assert either a CONTRACT or a STATE. `INCOMPLETE IN THIS MODE`
is a state — the state of a half-closed row — and a fixture that pins one turns the row's
own progress into a red suite. The contract underneath it survives the fix and is what the
rewritten fixture asserts: *the report names the plane it read, so an empty result reads as
swept rather than as unread.*

**Why it is an entry and not a shrug: this is the third occurrence today.** The umbrella's
`a commit that stages nothing HERE is not this project's commit` encoded the bypass it was
supposed to prevent; `sheleg-design`'s two release plants moved an `## Unreleased` section a
release had legitimately absorbed; and this one. Three repositories, one shape.

**The fix, by grade.** Mechanical where it can be: before implementing a change that removes
a documented limitation, grep the test corpus for the limitation's own wording — the string
is the index. Recorded as standing instruction 9 rather than as a paragraph, because a
paragraph is what the two earlier occurrences already had.

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
