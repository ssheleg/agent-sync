# Verification ledger

One row per shipped requirement, and how it is verified **now** — not how it was verified once.
A row whose method is "read the code" is a row nobody can re-run; those say so.

`check_*` names are functions in `test/validate.py`, run by `npm test` and by CI on every push.
`self-test` means the validator also plants that exact defect back and confirms the check fires
(`python3 test/validate.py --self-test`).


## v1.18.4 — the badge and the homepage reach npm

**Release candidate v1.18.4.** This section was written before the tag.

| REQ | What ships | How it was confirmed | Confirmed |
|---|---|---|---|
| REQ-P06 | The `skills.sh` badge and the canonical `homepage` reach the package page, not only `main` | npm serves README and metadata from the last **publish**; both landed on `main` in the previous cycle and the published package still showed a badge-less README and a GitHub homepage. Re-read from the registry after this release | **observed** |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.18.4 — all checks green`, exit 0 | 2026-08-27 |


## v1.18.3 — the shared seam is explicit

**Release candidate v1.18.3.** This section was written before the tag.

| REQ | What ships | How it was confirmed | Confirmed |
|---|---|---|---|
| REQ-P05 | Both shared validator headers explicitly declare that this repository has no divergence from the umbrella mechanism | the umbrella structural validator requires `# diverges:` beside every `# shared-mechanism:` declaration | **observed** |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.18.3 — all checks green`, exit 0 | 2026-08-26 |


## v1.18.2 — the shared guards name their canon

**Release candidate v1.18.2.** This section was written before the tag.

| REQ | What ships | How it was confirmed | Confirmed |
|---|---|---|---|
| REQ-P04 | Both copied public-contract validators declare an umbrella-owned shared mechanism | the umbrella structural validator reads every member and refuses a repeated mechanism with no `# shared-mechanism:` header | **observed** |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.18.2 — all checks green`, exit 0 | 2026-08-26 |


## v1.18.1 — the public contract is visible before installation

**Release candidate v1.18.1.** This section was written before the tag.

| REQ | What ships | How it was confirmed | Confirmed |
|---|---|---|---|
| REQ-P01 | A root skill card states the boundary, trust model, distribution paths, and known limits | `SKILL-CARD.md`; `python3 test/validate.py` resolves the repository contract | **test-only** |
| REQ-P02 | Trigger and scenario eval fixtures are machine-readable without claiming a model score | `python3 test/evals_validate.py` and `python3 test/evals_validate.py --self-test` | **planted** |
| REQ-P03 | The committed social card has the public 1200×630 contract | `python3 test/social_preview.py` | **test-only** |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.18.1 — all checks green`, exit 0 | 2026-08-26 |


## v1.18.0 — a third backend, and the rows it cannot close yet

**Shipped in v1.18.0.** Written before the tag, the only order that works.

The capability flags were measured against a live workspace on 2026-08-25, and the
measurement cost two defects and one redesign before it said anything true. That history
is the evidence, so it is kept:

1. **First run: PASS.** 200 appends from two processes left 200 lines.
2. **Second run: red** — 171 lines and a writer dead. The retry path had not executed in
   the first run; the second hit the workspace rate limit and found that `TimeoutError`
   is an `OSError`, not a `URLError`, so a read that timed out **after** the connection
   was established fell past both retry branches into the catch-all and failed
   permanently. Fixed, and covered in both directions.
3. **Third run: red differently** — 100 lines, all from writer A, both writers exit 0.
   Not lost appends: the two writers each called `tree.ensure` on the same title, the
   check-then-create raced, and each wrote to its own page of that name. The finding is
   about the MEASUREMENT, not the adapter — `Sync.log_id()` says *this run's OWN shard,
   one writer per document, always*, so the protocol never creates that scenario. The
   test now passes the page id for the contention case and adds the case that matters
   instead: two fresh shards, both enumerated.
4. **Fourth run: PASS**, and it is quoted in the gate row below.

A measurement that had stopped at step 1 would have shipped two defects behind a green
line.

| REQ | What shipped | How it was confirmed | Confirmed |
|---|---|---|---|
| REQ-N01 | Every Notion primitive raises the tool's own failure type, never a bare `HTTPError` | `check_notion_retries_only_what_can_succeed` drives `_call` through a stubbed transport for 401, 429 and 409 and fails the run if anything but `Fail` escapes | **planted** |
| REQ-N02a | No adapter claims a lease it cannot decide | `check_no_adapter_claims_a_lease_it_cannot_decide` reads `exclusiveLease` off every shipped adapter; fixture `an adapter claims an exclusive lease` sets it true and is caught | **planted** |
| REQ-N02b | `atomicAppend` and `totalOrderRead` are true of Notion | `test/notion_live_test.py` against a live workspace, 2026-08-25: `200 appends from two processes, 200 lines on one page (78s)` and `two reads return one order — totalOrderRead holds` | **observed** |
| REQ-N03 | The token reaches a header and nothing else | `check_no_credentials` over every published file, plus fixture `init writes the notion token itself`, which plants a value into the env line `init` writes | **planted** |
| REQ-N04 | 401/403 are never retried; 409, 429 and 529 are, honouring `Retry-After`, five attempts then a loud failure | the same stubbed-transport check asserts the attempt COUNT per status: 1 for a rejected token, 5 for a rate limit and for a write conflict. The fixture needs two edits to plant the defect, and that is the finding: dropping 401 from the auth branch alone still fails on the first attempt — the shipped defect is 401 reaching the retry set | **planted** |
| REQ-N05 | `tree.ensure` is idempotent against a live workspace | same run: `tree.ensure is idempotent — one page for two calls`, with the memo cleared between the two calls so the second is a real lookup | **observed** |
| REQ-N06 | Two processes appending 100 lines each leave 200 lines in one agreed order | same run, and the case the sharded design actually depends on came with it: `both fresh shards enumerated, 200 lines across them` — Outline's search index did not return a fresh document and cost eight processes eight winners; Notion's structural listing does | **observed** |
| REQ-N07 | With `atomicAppend` false the coordinator refuses lease authority | `check_no_adapter_claims_a_lease_it_cannot_decide` forces the flag false on every cloud adapter and requires `is_lease_authority` to go false with it — no network needed, which is why it is a suite check and not a live one | **planted** |
| REQ-N08 | `init --backend notion` writes the three env keys and no value | `check_init_notion_writes_its_env_keys` — asserts each key is present, that the token line is EMPTY, and that the output names `bootstrap` | **planted** |
| REQ-N09 | `bootstrap` follows the configured backend | `check_bootstrap_follows_the_configured_backend` — on `fs` it must say the backend has no container; on `notion` it must name the Notion credential and never an Outline one | **planted** |
| REQ-N10 | One list of backends across `init`, `check` and `bootstrap` | `check_check_accepts_every_shipped_backend` loops `BACKENDS`, runs `init` then `check` for each; fixture `check keeps its own backend list` restores the hardcoded pair and is caught | **planted** |
| REQ-N13 | `--set-baseline` stamps the highest id actually written, under either pattern key | `check_baseline_is_not_poisoned_by_the_next_free_line` builds a register holding DEC-0001…0003 with a `Next free ID` of DEC-0004 under the modern `pattern` key and requires DEC-0003. Reproduced first in `fabric` on 2026-08-25, where the baseline came back `ADR-0011`/`CO-0049` against a tree holding 0010 and 048 | **observed** + **planted** |
| REQ-N14 | `FsAdapter`'s docstring agrees with `check` about tracking `.agent-sync/` | the line now states the same thing `check` enforces; no fixture, because a comment cannot be asserted against itself | **read** |
| REQ-N15 | No check hands its environment or working directory to the next one | found by this run: `check_bootstrap_follows_the_configured_backend` passed alone and failed in the suite, because two earlier checks construct `Sync()` in-process and `load_env_file` left `AGENT_SYNC_BACKEND=fs` in `os.environ`, where it OVERRIDES the configured backend. Every check now runs through `_guarded` | **observed** |
| REQ-N16 | A read that times out is retried, and a retry can SUCCEED | `check_notion_retries_only_what_can_succeed` now drives both halves: attempt counts for 401/429/409, and a planted failure followed by a real answer for a rate limit and a read timeout. The success half had never existed — a retry loop that cannot succeed only fails more slowly. Fixture `notion gives up on a read timeout` plants it back | **observed** + **planted** |
| REQ-N17 | The measurement measures the protocol's own shape | the contention case is given the page id rather than the title, because two `tree.ensure` calls on one title race into two pages — real, and not a thing this protocol does. Watched failing: run 3 returned `100` lines, `A: 100  B: 0`, both writers exit 0 | **observed** |
| Gate | The live measurement | `python3 test/notion_live_test.py` → `PASS: notion live measurement — the capability flags are earned`, exit 0, five cases and three pages trashed on the way out | 2026-08-25 |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.18.0 — all checks green`, exit 0. `--self-test` → `SELF-TEST PASS: every injected defect was caught (57 fixtures, 8 at a time)`. `python3 test/claim_cell_test.py` → `PASS: claim cell, id registers, lease reaping and orphaned claim tags — 24 cases`. `python3 test/hooks_session_test.py` → `PASS: SessionStart identity — 5 cases` | 2026-08-25 |


## v1.17.0 — the fallback that was the only path

**Shipped in v1.17.0.** Written before the tag, the only order that works.

| REQ | What shipped | How it was confirmed | Confirmed |
|---|---|---|---|
| R-43 | The SessionStart hook establishes a session identity from the payload it is actually given | measured 2026-08-25 in a checkout that had been running the tool all day: `.agent-sync/sessions` did not exist and `.agent-sync/run-id` held one key, `shared`. The stamping block required `CLAUDE_SESSION_ID` in the hook's ENVIRONMENT while the id arrives on stdin as JSON, so it had never run once since it was written | **observed** |
| R-44 | The four ways the hook must behave are asserted against the hook as a PROCESS | `test/hooks_session_test.py` — an id from stdin is stamped, an environment id still wins, a payload with no id stamps nothing, and a payload that is not JSON leaves the hook at exit 0 | **planted** |
| R-45 | The chain from stamp to verdict is walked end to end | the fifth case resolves the descendant's key, reads the run-id map, and requires `classify_lock` to answer `reapable` where it answered `ambiguous`; run against the PREVIOUS hook it is red — 2 of 5 — and green against this one | **planted** + **observed** |
| R-46 | The README stops claiming commands the published package cannot run | it ships no `test/` directory, so `npm test` and `python3 test/validate.py` resolved in a clone and nowhere else; the umbrella's validator now refuses a member whose README presents such a command without naming where it runs | **planted** |
| Gate | The whole suite, on this tree | `python3 test/validate.py` → `PASS: agent-sync v1.17.0 — all checks green`, exit 0. `python3 test/claim_cell_test.py` → `PASS: … — 24 cases`, exit 0. `python3 test/hooks_session_test.py` → `PASS: SessionStart identity — 5 cases`, exit 0 — and 2 of those 5 red against the previous hook | 2026-08-25 |

## v1.16.0 — the release that closes one version string over two trees

**Shipped in v1.16.0.** Written before the tag, the only order that works.

| REQ | What shipped | How it was confirmed | Confirmed |
|---|---|---|---|
| R-40 | 281 lines of `AS-01a`/`AS-01b` behaviour, pinned by the umbrella and never tagged, reach the registry | measured 2026-08-23 by fetching the npm tarball and counting all three channels rather than trusting any: npm `@ssheleg/agent-sync@1.15.0` served `agent_sync.py` at **4344** lines, the plugin marketplace and the skills CLI at **4575**, and all three reported `1.15.0`. `check_pins.py` was green throughout — correctly, because it compares the version STRING | **observed** |
| R-41 | The release workflow refuses a tag whose commit no clone can reach | the guard is in `.github/workflows/release.yml` for this range; a tag on an unreachable commit fails every clone with `upload-pack: not our ref` while `git submodule status` shows no `+` | **planted** |
| R-42 | Six version surfaces move together, not five | the gate refused this very release until `agent_sync.py`'s `VERSION` and `SKILL.md`'s front-matter version followed the three manifests and the CHANGELOG — and refused again until this ledger named v1.16.0 and quoted the output the suite actually prints | **planted** + **observed** |

**Counts at ship: 3 rows — 1 observed · 1 planted · 1 planted+observed.**


## AS-04 — a claim tag stops outliving its lease (shipped in v1.15.0)

Closes **[ssheleg/agent-sync#5](https://github.com/ssheleg/agent-sync/issues/5)**, open since
2026-08-17. Not a missing function either: the reaping and restoring code already existed, and
two early returns put it out of reach. `write_claim`'s `if saved is None: continue` made
`release` a no-op that still printed `released <KEY>` and exited 0 — the state file is one
run's memory of what it overwrote, and a tag outlives it routinely — while
`claim_divergence`'s `if not held: return out` gated divergence reporting on holding a lease,
so a tag with nothing behind it could be reported to nobody. Same shape as #4, one plane over:
**expiry ends a lease, and every reader folded that away.**

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| AS-04a | A claim tag whose lease the TTL has ended is cleared by `release <KEY>`, and the run it named is printed | `check_a_claim_tag_cannot_outlive_every_command` + fixtures `an orphaned claim tag is cleared (#5)`, `an orphaned tag behind an expired lease is cleared (#5)` + self-test plant `an orphaned claim tag is unreachable` → **exit 1**, `release: left the orphaned claim tag in place ('open (claimed: r-ghost1234)') and exited 0` | 2026-08-20 |
| AS-04b | A tag with a **live** lease behind it is never cleared by another run, whatever the state file says | same check (the `B-88` half) + fixture `a tag with a live lease is not cleared by another run (#5)` + plant `a claim whose lease is live is edited anyway` → **exit 1**. Two refusals stand in front of it: `release`'s ownership check, and `write_claim`'s own `live is not None and live != self.rid` for the case where the state file is gone | 2026-08-20 |
| AS-04c | A tag with no lease behind it is REPORTED, not merely clearable — by `status`, `residue` and `reconcile` | same check (three commands asserted) + fixture `an orphaned tag is reported by status/residue/reconcile (#5)` + plant `orphan claim tags are reported to nobody` → **exit 1**, `status: a claim tag with no lease behind it is never named` | 2026-08-20 |
| AS-04d | One notion of held, and only one reader of it | `_lease_holder` is the single reader; `_holder_of` memoises it per command, so a report over a tagged board costs one `ls-remote` per **tagged row** in git mode and never one per board row. Counted rather than asserted: `grep -n '_lease_holder' plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py` → **7** lines — one definition (`:1464`), exactly **two** callers (`release()` at `:1495`, `_holder_of()` at `:1678`), and four mentions in prose. This row first said *"4 call sites"*, which no command produces; the grep was run because the row claimed a number, and the number was wrong | 2026-08-20 |
| AS-01b | An `ambiguous` lock is clearable by a PERSON, per key, with the deletion attributable  · **Exercised on live residue, 2026-08-20:** a real foreign expired lock in `~/DATA/0xDEV` (`BLOG-SITEMAP`, run `r-blog-1429e`, expired 4d 22h ago) — `reap` named it, said *left alone*, and deleted nothing; both lock files byte-identical after. On the git plane both branches ran: a different run left the ref standing, the run that took it removed it and said *confirmed gone by re-reading the remote*| `a foreign lock still refuses a plain reap (AS-01b)`, `an operator can clear state no run can prove (AS-01b)`, `the override refuses a blanket sweep (AS-01b)`, `the override refuses a live lease (AS-01b)`, `a key that starts with a dash can still be named (AS-01b)` — and the mechanism was then run against the state that filed the row: **28 locks on this machine, 0 left in the family**, each named and each printing the run, timestamp, machine and expiry it held | 2026-08-20 |
| AS-01a | The git plane is SWEPT, and an unreachable remote reads as `could not look` rather than as an empty sweep  · **Exercised outside the fixtures, 2026-08-20:** a git-mode checkout with ZERO local notes — the state a ref won on another machine leaves — against a local bare remote. `residue` printed *nothing in the lock directory* and then enumerated the git plane: `refs/agent-sync/leases/DEMO-KEY @ 495370743e`, run `r-rverifya`, expired 18s ago, `foreign`, closing *1 ref(s) on the remote, 0 this run can prove it owns and has spent*| `residue names the git plane it read (AS-01a)`, `residue sweeps the git plane's refs (AS-01a)`, `a reapable git lease is cleared and proved gone (AS-01a)`, `an unreachable remote reads as \\`could not look\\`, never as an empty sweep (AS-01a)` — four fixtures against a REAL bare remote carrying refs won by another run on another machine, which is the state the local directory walk cannot see. `git_residue()` classifies each ref with the SAME `classify_lock` the local plane uses, and `git_reap()` deletes with the same `--force-with-lease=<ref>:<sha>` compare-and-swap `_git_release` uses, proving the ref went by re-reading `ls-remote` rather than by trusting the push's exit code | 2026-08-20 |
| AS-03 | A `local` lock records the machine that wrote it, and two machines are separated in that mode | `check_local_locks_record_their_host` (through the CLI) + fixtures `a local lock records its host (AS-03)`, `two machines are separated in local mode (AS-03)` + plant `a local lock records no host` → **exit 1** | 2026-08-20 |
| AS-05 | The newest ledger section names what `git describe --tags` prints | `check_ledger_names_the_shipped_version` — `git describe --tags --abbrev=0` where there is a git directory, `package.json` where there is not (the self-test's copy), refusing when the two disagree; plus an "unreleased" claim about a version the CHANGELOG already carries. Plants `the ledger names a version that did not ship` and `the ledger calls a shipped artifact unreleased` → **exit 1** each | 2026-08-20 |
| AS-06 | One divisor for the token budget, and the body under it | `test/validate.py` divides by **3.9**, matching `CHARS_PER_TOKEN` in make-skill's `audit_skill.py`. Body: **18265 chars / ~4683 tokens**, `0 GAP, 14 PASS` from `audit_skill.py --house`, inside the 5000 budget and the 4750 working limit. Plant `skill body over the token budget` → **exit 1**, `body is ~5360 tokens (20906 chars / 3.9)` | 2026-08-20 |
| Gate | The whole suite, and every check watched failing | `python3 test/validate.py` → `PASS: agent-sync v1.15.0 — all checks green`, exit 0. `python3 test/validate.py --self-test` → `SELF-TEST PASS: every injected defect was caught (51 fixtures, 8 at a time)`, exit 0 — up from 43. `python3 test/claim_cell_test.py` → `PASS: … — 16 cases`, exit 0 — up from 9 | 2026-08-20 |
| Reproduced | The defect was real at the tagged version, not only in a description | Driven by hand against a fixture board at v1.14.0 before the fix: `release B-77` printed `released B-77`, exit **0**, `git diff --stat` empty; `residue` → `nothing on disk`; `reconcile` → `no mechanical divergence found`; `status` → `leases held: none` / `expired locks: none` | 2026-08-20 |

**What this row does NOT prove.** The sweep reads and clears the refs the configured remote
answers with; it says nothing about a remote that answers *partially*, and nothing about two
runs sweeping the same remote at the same moment — the compare-and-swap refuses the second
one, which is correct, and no fixture drives that race. `disputed` (a tag
naming one run while the live lease belongs to another) is reported and never cleared; that is
the contract, not a gap, and `a tag with a live lease is not cleared by another run` covers it.
Locks written before this change carry no `host`, and `classify_lock` still treats an absent
`host` as legal — so AS-03's separation applies only to locks taken after this change.

## AS-01 — a run reports what it leaves behind (shipped in v1.14.0, 2026-08-19)

Closes **M-49** and **M-50** of the Proof-of-Done manifesto. The defect was not a missing
function: the reaping code already existed in `release()`, and nothing could ever reach it
for a key nobody names. Every reader of lease state folds the TTL into the read, so
*expired* and *absent* were one answer.

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| M-49a | `status` and `finish` enumerate expired locks as residue rather than reporting `none` | `check_status_reports_expired_locks_as_residue`, `check_finish_reports_what_the_run_leaves_behind` + self-tests `expired locks are not enumerated`, `finish says nothing about what the run leaves behind` | 2026-08-19 |
| M-49b | Only state this run PROVABLY owns and has spent is reapable; foreign and ambiguously owned state is reported and left alone, including when named on the command line | `check_residue_ownership_must_be_provable` — 10 classifier verdicts plus 5 locks on disk (own, foreign, owner-less, unreadable, live) + self-tests `a foreign expired lock is called this run's`, `unprovable ownership defaults to reapable` | 2026-08-19 |
| M-49c | The reason a lock was left alone is printed, never just the verdict | same check — every non-live verdict must carry a `why` | 2026-08-19 |
| M-50 | Teardown is verified by re-reading the state, not by the delete's return value | `check_reap_verifies_teardown_by_re_reading` — `Path.unlink` replaced with a no-op, so the delete *succeeds* and the state does not change; `reap` must report `remaining`, not `reaped` + self-test `teardown trusts the delete instead of re-reading`. Also driven by hand against a `chmod 500` lease directory: exit 1, `MINE is STILL PRESENT after the delete` | 2026-08-19 |
| Gate | The whole suite, and every check watched failing | `python3 test/validate.py` → `PASS: agent-sync v1.14.0 — all checks green`, exit 0. `python3 test/validate.py --self-test` → `SELF-TEST PASS: every injected defect was caught (43 fixtures, 8 at a time)`, exit 0 — up from 38 | 2026-08-19 |
| Measured | The family's residue is counted, not estimated | the classifier run read-only over every lock in the nine checkouts of `sshlg-skills`: **24 locks, 7 live, 17 expired** — `foreign` to any fresh session, `ambiguous` to a plain shell, `reapable` only by the session that took them. **0 reapable by any run today** | 2026-08-19 |
| Measured | The run that reported the residue deleted none of it | the same sweep after the commit: 17/17 still present | 2026-08-19 |

**Decided, not changed.** `release <KEY>` still reaps an expired lock in another run's
name and `reap` refuses to — the #4 incident (a lease at 604× its TTL that nothing could
clear) is why the first exists, and a person naming one key is what makes it safe. A sweep
has no such person. Both contracts are stated in `references/lease-protocol.md` so the two
are not "aligned" by widening the sweep; the existing behaviour is covered by
`test/claim_cell_test.py` (`an expired foreign lease can be reaped`).

**Not verified by this row.** The git lease mode's refs are not enumerated — the read walks
the lock directory, which both modes write, so a ref won on another machine (no local note)
is invisible to `residue`. Board row AS-01a. A `local` lock carries no `host`, so residue
cannot separate machines in that mode; that is the lease contract, deferred to AS-03 —
named on the board so the gap is not read as an oversight here. The `--self-test` fixture set is a defect suite, not coverage: it proves each
check fires, not that the classifier has no unreached verdict.

## v1.12.0 — the tarball stops carrying someone else's bytecode

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| The published tarball contains no compiled Python | `npm pack --dry-run` after adding the `files[]` negations | 0 `.pyc` entries; **231.5 kB / 28 files → 125.8 kB / 27** | 2026-08-16 |
| The defect was real in the published artifact, not just locally | `npm pack @ssheleg/agent-sync@1.11.1 && tar tzf` | `…/scripts/__pycache__/agent_sync.cpython-312.pyc` — a different interpreter from this machine's 3.14, so it came off the publisher's | 2026-08-16 |
| The guard asks npm rather than walking the filesystem | removed the negations, ran `test/validate.py` | exit 1, naming the file and the remedy | 2026-08-16 |
| The guard is in the self-test | `python3 test/validate.py --self-test` | `SELF-TEST PASS … (38 fixtures, 8 at a time)`, up from 37 | 2026-08-16 |
| The release shipped | `npm view @ssheleg/agent-sync version`; CI on the tag | `1.12.0`; `validate` and `release` both `completed success` | 2026-08-16 |

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

## v1.7.1 — how the skill reads to the agent using it

| REQ | What must hold | Verified by | Last run |
|---|---|---|---|
| REQ-23 | Every verb a surface advertises is a verb the CLI has | `check_every_advertised_verb_exists` + self-test `the slash command offers a verb the CLI lacks` | 2026-08-10 |
| REQ-24 | The documents the tool generates carry the doctrine the skill teaches | `check_generated_docs_carry_current_doctrine` + self-test `generated project docs lag the doctrine` | 2026-08-10 |
| REQ-25 | `check` refuses a register the configured backend can never allocate | `check_registers_need_a_backend_that_can_reserve` + self-test `check blesses a register nobody can reserve` | 2026-08-10 |
| REQ-26 | `$SKILL_DIR` has a resolvable value in the skill body | `check_skill_gives_a_resolvable_script_path` + self-test `the script path is prose only` | 2026-08-10 |
| REQ-27 | Two agents contending for one task behave correctly end to end | Executed, not checked: second loses, sees the holder and repo, is denied the guarded file, cannot release what it does not hold. **Not gated** | 2026-08-10 |
| REQ-28 | The guard covers every tool shape and commit form | Executed, not checked: `Edit`/`Write`/`NotebookEdit`/absolute paths denied; `git commit`, `git -C <dir> commit`, `cd <dir> && git commit` blocked with a guarded file staged; `git log --grep=commit` and malformed JSON pass. **Partly gated** by `check_hooks_noop_without_config` | 2026-08-10 |

## Standing gaps

- **REQ-09**, **REQ-20**, **REQ-27** and **REQ-28** have no check that fails on their own. The last
  two are the multi-agent and hook scenarios: they were driven by hand this run and behaved
  correctly, which is evidence about today, not a guarantee about tomorrow. Board rows B-001 and
  B-009.
- **REQ-22** is a measurement, not a gate. If guard latency matters later it needs a budget and a
  check that fails when it is exceeded; asserting a timing in CI without one is how a flaky test
  gets deleted.
