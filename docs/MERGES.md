<!-- agent-sync:merge-log -->

# Merge log

Written by `agent_sync.py merge`. Entries newer than 7 days keep their detail; older ones are compacted to one line each on the next write. Read it before starting work: it is the shortest answer to *what landed while I was on my branch*.

### 2026-08-31 · `ASY-10` · asy-10-submodule-trigger → main · `4c1c604`
- run: r-f31d90ddb
- files: 11 (11 files changed, 238 insertions(+), 20 deletions(-))
- conflicts: none
- summary: v1.19.0: the description advertises the multi-repository finish (ASY-10) — q06 0/3 → 3/3 on haiku and on sonnet across two corpora differing in one line, all twelve queries re-probed (12/12 both models, up from 11/12); the local 1024 cap vs CI's 970 filed as AS-10

### 2026-08-31 · `ASY-W3` · asy-wave3-evals-schema → main · `6dfd6f9`
- run: r-asyw3fable
- files: 12 (12 files changed, 140 insertions(+), 13 deletions(-))
- conflicts: none
- summary: v1.18.7: the evals executed and dated — 11/12 triggers on haiku and sonnet, q06 stable miss filed as ASY-10, scenarios driven on disk (ASY-03); $schema in both manifests + json.schemastore.org allowlisted (ASY-09)

### 2026-08-30T15:04:58Z · `ASY-W2` · asy-wave2-guard-fail-closed → main · `4b3022d`
- run: r-f31d90ddb
- files: 16 (16 files changed, 206 insertions(+), 9 deletions(-))
- conflicts: none
- summary: v1.18.6: the guard fails closed without python3 (ASY-07), /clear re-stamps identity (ASY-08), refusal names its remedy (ASY-06), bytecode stays out of scripts/ (ASY-01)

### 2026-08-29T21:31:37Z · `ASY-05` · asy-05-wave15-installer → main · `587ef45`
- run: r-f31d90ddb
- files: 14 (14 files changed, 517 insertions(+), 25 deletions(-))
- conflicts: none
- summary: v1.18.5: guard.sh consumes the single pipe (ASY-05); installers settle the Claude channel against installed_plugins.json

### 2026-08-25T23:37:16Z · `AS-08` · as-08-id-width → main · `d0f6b9f`
- run: r-ras08
- files: 1 (1 file changed, 1 insertion(+))
- conflicts: none
- summary: AS-08 filed

### 2026-08-25T21:45:37Z · `AS-05-notion` · notion-backend → main · `00cd48a`
- run: r-rnotion1818
- files: 14 (14 files changed, 1457 insertions(+), 97 deletions(-))
- conflicts: none
- summary: the Notion backend, measured

## Compacted

_nothing older than the window yet_
