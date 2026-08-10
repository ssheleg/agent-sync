# Retrospective — agent-sync

## Run stamps

| Date | Commit | What ran |
|---|---|---|
| 2026-08-10 | `1ac6f14` | Audit of 1.5.2, then 1.5.3 → 1.6.0 → 1.7.0 |

## Standing instructions

Read in full at stage 0. Each one is retired when it becomes a mechanical check, when the paths it
names are gone, or when it has not fired in five run stamps or sixty days. Hard cap: ten.

1. **A check that has never been watched fail is not evidence.** Every check in `test/validate.py`
   has a self-test fixture that plants its defect back. Adding a check without one is adding a
   green light with no bulb. *(Became partly mechanical this run — the self-test enumerates 27
   fixtures — but "add the fixture with the check" is still a habit, not a gate.)*
2. **Test the composition, not only the unit.** Every defect the 2026-08-10 audit found lived in
   the gap between two things already tested separately: the allocator was correct and `reserve`
   never asked it about the whole log; `lease_guarantee` was quoted by every surface while `renew`
   refreshed nothing. When a function is tested, ask what calls it and whether *that* is tested.
3. **Run the commands before writing about them.** The audit that found six shipped defects did it
   by executing `init`, `check`, `reserve`, `renew`, `merge` and reading the output — not by
   reading the source. The validator was green throughout.
4. **Print success only after it is established.** `record`, `release-id`, `merge` and `board` each
   printed a result before, or without, checking that it had been achieved. An agent quotes stdout.
5. **When one fact lives in two files, one of them is already wrong.** `CONFIG_KEYS` vs the schema,
   the stage numbers in three documents, the guard's glob vs `check`'s. The fix is a single source
   plus a check that the others quote it — `lease_guarantee()` is the pattern to copy.
6. **`SKILL.md` is at its token ceiling.** Every addition must displace something. Move the *why*
   into `references/` and leave the rule in the body; the budget is a real constraint, not a lint.

## Entries

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

**Fix, by grade.** Mechanical: seventeen new checks that drive the real commands from more than one
identity, plus seventeen self-test fixtures planting each defect back. Standing instructions 1–3
above, for the habits the checks cannot encode.

**The check that catches it next time.** `check_reserve_is_race_free`,
`check_renew_extends_the_lease`, `check_config_round_trip`, `check_no_success_on_failed_publish`,
`check_steal_is_atomic`, `check_merge_refuses_stale_target`, `check_unparseable_log_fails_loudly`,
`check_status_reports_the_setup_verdict`, `check_watermark_survives_a_late_entry`,
`check_claim_round_trip_is_byte_exact` — all in `test/validate.py`, all with a planted-defect
fixture.

**What it cost to find.** Nothing was wrong with the code that could not be seen by running it. The
release had shipped through CI four times.
