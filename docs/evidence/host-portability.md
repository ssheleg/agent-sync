# Host portability — 2026-10-09

Base: `169436a28d4ba788ca471c83c8e7ddc024999466`. Scope: script discovery and
adapter claims; no runtime coordination algorithm, host config or model change.

The active SKILL.md location owns script resolution. A host supporting hooks does
not install this package's Claude adapter; missing/unverified runtime enforcement is reported separately
from the board's lease arbitration, and the same guard runs explicitly.

## Evidence and exact limits

Independent review accepted source `5669423d8f568e90294f619c40f7cb3f8a9d9232`.
The copied skill at a custom path containing spaces ran `agent_sync.py --help`
(exit 0, status/guard present). make-skill 0.29.1 audit: 0 GAP / 19 PASS; both strict
Claude plugin and marketplace validators exited 0. These checks do not claim
native host runtime acceptance.

Full local `npm test` on that payload exited **1**, after the main validator
reported `PASS: agent-sync v1.21.5 — all checks green`: 73 of 74 mutation cases
detected their defects. `the ledger names a version that did not ship` missed
because its regex searched past a candidate section without a quoted PASS into a
historical section, which the validator correctly ignores. No runtime defect was
found. The candidate ledger now records the actual main-validator output.

The repaired plant inserts `PASS: agent-sync v0.0.0` immediately after the first
section heading. A focused check compiled the actual lambda from the validator AST,
then invoked `check_ledger_names_the_shipped_version`: both the real candidate and
a candidate without a quoted output rejected v0.0.0; historical sections stayed
byte-identical; restoring the ledger passed (exit 0). This is a focused regression
result, **not** a claim that the failed whole local npm invocation passed.

Separately rerun local components exited 0: claim-cell 27 cases, hook-session 13
cases, and installer 11 cases.
The updated PR's full hosted gate and release gate must finish before publication.
The small fixture repair needs independent review too. Final source/tag/package
receipts belong to the family host-compat release evidence; no native runtime
acceptance is claimed.

