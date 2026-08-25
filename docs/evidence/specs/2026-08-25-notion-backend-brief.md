# Brief — a Notion knowledge backend (AS-05)

Run `2026-08-25-notion-backend`. Stage 0 of task-pipeline, Proof of Done.

## Why this exists

Two people on two machines need one coordination plane, cheaply. The lease is already
cross-machine wherever `leaseBackend: "git"` is set — a pushed ref's non-fast-forward
rejection is a real compare-and-swap — so what is missing is **awareness**, not safety:
who holds what right now, the board, cross-repo signals. `fs` cannot carry it (local
files, `references/backend-fs.md`), and the only shipped alternative is Outline, whose
Cloud tier is $10/month for 1–10 members (getoutline.com/pricing, fetched 2026-08-25).
The operator chose a Notion backend over paying for that, on 2026-08-25.

## Source ledger

| Source | What it gave |
|---|---|
| `docs/evidence/retro.md` (read in full) | ten standing instructions; 1, 3, 7, 8 and 10 bind this run directly |
| `docs/evidence/backlog.md` | no open rows — every section is `Closed by …` |
| `docs/evidence/verification.md` | REQ-25 already pins "check refuses a register the configured backend can never allocate" |
| `references/adapter-contract.md` | the six primitives, three capabilities, and the **add-a-backend checklist** this REQ table is built from |
| `references/backend-outline.md`, `backend-fs.md` | the voice and shape the new reference must match |
| `OutlineAdapter` (`agent_sync.py:525-699`) | the implementation model, including the lesson at `log_shards`: enumerate by structure, never by the search index |
| `test/validate.py` | 56 checks; grep for the two-backend limitation's wording found **no fixture pinning it** (standing instruction 10 satisfied before implementing) |
| Notion API docs, fetched 2026-08-25 | see below |
| `CLAUDE.md` / `CONTEXT.md` in this repo | none found |
| code graph (`graphify-out/`) | absent — not built for this repository |
| knowledge wiki | `projects/agent-sync/agent-sync.md` present in the vault |

## What the Notion docs actually say

Fetched 2026-08-25, each claim from the page named:

- **Append is server-side.** `PATCH /v1/blocks/{block_id}/children` — "Creates and appends
  new children blocks to the parent block_id specified", and "by default, blocks are
  appended to the end of the parent block's children". No read-modify-write, so
  `atomicAppend` *may* be true — subject to measurement, never to this paragraph.
- **100 children per request** is the hard cap on one append call.
- **`conflict_error`, HTTP 409** is in the endpoint's own error list. Concurrent appends to
  one page can collide, which is precisely why the flags are measured rather than derived.
- **Rate limits** (`/reference/request-limits`): an average of three requests per second
  per connection, plus a per-workspace limit scaled to the plan. Either returns
  `rate_limited` / 429 with `additional_data.rate_limit_reason`; 529 is `service_overload`.
  Both carry `Retry-After`, in integer seconds.
- **Reading** is `GET /v1/blocks/{block_id}/children`, cursor-paginated
  (`start_cursor`, `page_size`, `has_more`, `next_cursor`).
- **Auth** is a personal access token (`ntn_…`) from the developer portal — no integration
  and no page-sharing dance. The current API version is **2026-03-11**, and it is a
  required header.

## Decisions taken in the grill

| # | Decision | Why |
|---|---|---|
| 1 | The capability flags are set **from a live measurement**, not from the docs above | `references/adapter-contract.md`: declaring `exclusiveLease` without compare-and-swap is "the most damaging lie an adapter can tell". Trap 1 of `SKILL.md` is itself a measurement — twelve concurrent appends to one Outline document returned twelve successes and left three lines |
| 2 | `exclusiveLease` stays **false** whatever the measurement shows | Notion has no compare-and-swap. The lease stays with `leaseBackend` |
| 3 | Env keys mirror Outline's shape: `AGENT_SYNC_NOTION_TOKEN`, `_PARENT`, `_COLLECTION` | one fact, one home; a reader who knows one backend can read the other |
| 4 | The token is the operator's alone — created, copied and pasted by them | contract's credential rule, and the ops rule in the machine's own instructions |
| 5 | The run is authorized through release: branch → tests → merge → push → tag → CI read → npm publish → local copies updated | operator, 2026-08-25 |

## REQ table

Frozen. Adding is free; removing needs the operator.

| REQ | What must be true | Verified by |
|---|---|---|
| REQ-N01 | Every primitive raises the tool's own `Fail`, never a bare `HTTPError` or `OSError` | `check_notion_failures_are_typed` + self-test |
| REQ-N02 | The three capabilities are declared from the measurement; `exclusiveLease` false | the measurement recorded in `verification.md`, and `check_no_adapter_claims_exclusive_lease` + self-test |
| REQ-N03 | Credentials come from the environment only, and reach no argv, log line or board render | `check_no_credentials` (existing) extended to the new keys |
| REQ-N04 | 429 and 529 honour `Retry-After`; 409 `conflict_error` is retried; 401/403 never are | `check_notion_retries_only_what_can_succeed` + self-test |
| REQ-N05 | `tree_ensure` is idempotent — called twice against a live workspace it returns one id | live measurement, recorded with its output |
| REQ-N06 | `log_append` is atomic — two processes appending 100 lines each yield 200 lines in one order both readers agree on | live measurement, recorded with its output |
| REQ-N07 | Forcing `atomicAppend: false` makes the coordinator refuse lease authority **and say so** | `check_notion_degrades_out_loud` + self-test |
| REQ-N08 | `init --backend notion` needs no `--url`, writes the three env keys, and prints only the steps that are the operator's | `check_init_notion_writes_its_env_keys` + self-test |
| REQ-N09 | `bootstrap` follows the configured backend instead of hardcoding Outline (`agent_sync.py:3170`) | `check_bootstrap_follows_the_configured_backend` + self-test |
| REQ-N10 | `check` accepts `notion` and validates its credentials rather than rejecting the config | `check_check_accepts_every_shipped_backend` + self-test |
| REQ-N11 | `references/backend-notion.md` exists, in the voice of `backend-outline.md`, and `SKILL.md` links it | `check_doctrine_is_current` (existing) extended |
| REQ-N12 | The version is one number across `package.json`, both manifests and `SKILL.md` metadata | `check_version_sync` (existing) |
| REQ-N13 | `_allocated_ids` reads the id pattern through `id_pattern()`, so the modern `pattern` key does not poison the baseline by one | `check_baseline_is_not_poisoned_by_the_next_free_line` + self-test |
| REQ-N14 | `FsAdapter`'s docstring and `check` agree about whether `.agent-sync/` is committed | grep, and the docstring itself |
| REQ-N15 | The generators emit the third backend wherever they enumerate backends (standing instruction 8) | `check_generated_docs_carry_current_doctrine` (existing) extended |

## Carry-over

Opened by this run, and nothing is deferred silently.

| # | What | Why deferred | Home |
|---|---|---|---|
| CO-N01 | `guard`'s `PreToolUse` matcher covers `Edit\|Write\|MultiEdit\|NotebookEdit` and `git commit`, not a guarded file written by `cat >` or `sed -i` | widening it means parsing shell to find write targets; it is a separate decision from this backend | board, after this run |
