# Conformance — Fabric's coordination specification

agent-sync is the reference implementation of the coordination operations Fabric
specifies in `docs/specification/coordination.md`. This map is pinned to
**passioncode-ai/fabric-agent-contract@bcada4a**. `test/coordination_test.py` checks
that every command named in the **Command** column exists in this tool's CLI. When the
contract moves, re-read it, update the pin, and re-run the test.

## Operations

| Contract operation | Command | Notes |
|---|---|---|
| `claim(workId, owner, scopes, ttl)` | `acquire` | Owner is the run id (hashed per key since 1.22.0); ttl is `leaseTtlSeconds`. Atomic: a git-ref compare-and-swap in `git` lease mode, an exclusive create in `fs`. |
| `renew(claimId, owner, ttl)` | `renew` | Only this run's LIVE leases (`held()`); an expired lease cannot be renewed. |
| `release(claimId, owner, outcome)` | `release` | Outcome is recorded by `merge` (landed) or `journal` (stopped). `release --held` releases everything this run holds. |
| `reserveId(namespace, owner, count, ttl)` | `reserve` | One id per call; `release-id` returns an unused one. Ids are printed at the register's own width. |
| `recordAsBuilt(claimId, revision, artifacts)` | `record` | Appends what was built, with its commit and files, to the record plane. |
| `reconcile(claimId)` | `reconcile` | Compares intent (git) with as-built (record plane). Residue is classified by `residue`. |

## Write scopes

The contract's scopes map to `guardedFiles` in `.claude/agent-sync.json`:
- `guard` answers whether this run may write a path (exit 2 means no).
- The PreToolUse hook asks `guard` before every Edit/Write.
- Bash writes are checked by the guard's command parser (1.22.0).

## Lifecycle and residue

| Contract state | Here |
|---|---|
| claimed | a lock file (`fs`) or `refs/agent-sync/leases/<key>` (`git`) inside its TTL |
| released | the lock or ref is removed by `release` / the SessionEnd hook |
| expired | TTL elapsed. `status` and `residue` report it: `reapable` (this run's), `foreign`, or `ambiguous` |
| abandoned (owner reports failure) | `journal` the failure, then `release` |
| reconciling / reconciled | `residue` classifies; `reap` clears this run's own; `reap --i-own-this KEY` is an operator decision per key; `reap --long-expired --apply` sweeps locks expired more than 7 days (not the contract's `abandoned` — time alone) |

**Residue is evidence.** Every operator-decided removal prints the payload it destroyed
and journals it. Nothing is cleared silently, as the contract requires.

## Not yet conformant

These are said here rather than implied by the table:
- **Idempotency key per command.** Only `reserve` takes one (`--rkey`). `acquire` is
  idempotent for its own holder, but it takes no key.
- **Residue categories `uncommitted-files`, `unpushed-commit`, `open-pr` and
  `merged-unreconciled`.** These are reported by `finish` (clean, pushed, pointed at)
  rather than by `residue`.
- **`external-effect`.** Not modelled.
