# Verification ledger

One row per shipped requirement, and how it is verified **now** — not how it was verified once.
A row whose method is "read the code" is a row nobody can re-run; those say so.

`check_*` names are functions in `test/validate.py`, run by `npm test` and by CI on every push.
`self-test` means the validator also plants that exact defect back and confirms the check fires
(`python3 test/validate.py --self-test`).

## v1.5.3 — the tool stops reporting what is not true

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| REQ-01 | Two runs are never handed one id | `check_reserve_is_race_free` + self-test `reserve reads only its own shard` | 2026-08-10 |
| REQ-02 | `renew` moves the timestamp expiry is computed from, in both lease modes | `check_renew_extends_the_lease` + self-test `renew moves no timestamp` | 2026-08-10 |
| REQ-03 | The config `init` writes, and the shipped example, pass `check`; `CONFIG_KEYS` equals the schema | `check_config_round_trip` + self-test `config key list drifts from the schema` | 2026-08-10 |
| REQ-04 | `release-id` fails on a backend that records nothing | `check_no_success_on_failed_publish` | 2026-08-10 |
| REQ-05 | `record`, `signal`, `journal` exit non-zero when the entry did not land | `check_no_success_on_failed_publish` + self-test `record reports success it did not achieve` | 2026-08-10 |
| REQ-06 | A store error is the tool's failure type, never a traceback | `check_no_success_on_failed_publish` (unwritable plane case) | 2026-08-10 |
| REQ-07 | A guard denial names the other run **and** its key | `check_guard_denial_names_only_what_it_knows` + self-test `guard names a holder without a key` | 2026-08-10 |
| REQ-08 | No published surface sells the refuted lease doctrine | `check_doctrine_is_current` + self-test `refuted doctrine on the listing` | 2026-08-10 |
| REQ-09 | Docs describe behaviour that exists | Read against the code, this run. **Not mechanically checked** except where a later REQ covers it | 2026-08-10 |

## v1.6.0 — the guarantees hold

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| REQ-10 | Stealing an expired lease is one critical section; an abandoned section expires | `check_steal_is_atomic` (3 cases) + self-test `steal section is not exclusive` | 2026-08-10 |
| REQ-11 | `merge` shares one base between preflight and merge, or refuses | `check_merge_refuses_stale_target` + self-test `merge into a stale integration branch` | 2026-08-10 |
| REQ-12 | One pattern means one thing to `guard` and to `check` | `check_guard_and_check_agree_on_globs` + self-test `guard matches paths from the right` | 2026-08-10 |
| REQ-13 | A log past 2% unparseable is refused by every reader that replays one | `check_unparseable_log_fails_loudly` + self-test `unparseable logs are replayed anyway` | 2026-08-10 |
| REQ-14 | The stage numbers are stated once and quoted everywhere | `check_stage_binding_agrees` + self-test `stage binding loses its source` | 2026-08-10 |

## v1.7.0 — quiet failures

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| REQ-15 | `status` carries `check`'s verdict | `check_status_reports_the_setup_verdict` + self-test `status hides the setup verdict` | 2026-08-10 |
| REQ-16 | Credentials come from this tree or from `AGENT_SYNC_ENV`, never from an arbitrary parent | `check_env_discovery_is_bounded` + self-test `no way to name the credentials file` | 2026-08-10 |
| REQ-17 | An out-of-order signal is still reported as new | `check_watermark_survives_a_late_entry` + self-test `watermark is positional again` | 2026-08-10 |
| REQ-18 | Nothing is declared that nothing uses | `check_no_orphan_logs`, `check_no_dead_declarations` + self-tests `a log nothing writes`, `posix-only spelling returns` | 2026-08-10 |
| REQ-19 | The coordinator runs wherever python3 does | `check_no_dead_declarations` (POSIX spellings) | 2026-08-10 |
| REQ-20 | `merge` releases the lease it landed, not every lease | Read against the code; covered indirectly by `check_merge_refuses_stale_target`. **Weak** — see the backlog | 2026-08-10 |
| REQ-21 | `acquire`/`release` leaves the file byte-identical | `check_claim_round_trip_is_byte_exact` + self-test `claim round-trip rebuilds the row` | 2026-08-10 |
| REQ-22 | Guard latency stays out of the way | Measured, not gated: ~100 ms and 8 subprocesses per guarded edit with no run id in the environment, ~70 ms and 1 with one. **Never** enforced | 2026-08-10 |

## Standing gaps

- **REQ-09** and **REQ-20** have no check that fails on their own. Both are board rows.
- **REQ-22** is a measurement, not a gate. If guard latency matters later it needs a budget and a
  check that fails when it is exceeded; asserting a timing in CI without one is how a flaky test
  gets deleted.
