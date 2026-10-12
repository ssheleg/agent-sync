# Conformance — Fabric's coordination specification

agent-sync implements the coordination operations Fabric specifies in
`docs/specification/coordination.md`, with the gaps listed at the end of this page.
Fabric's contract names no implementation; this is a mapping, not an endorsement. This map is pinned to
**passioncode-ai/fabric-agent-contract@bcada4a**. `test/coordination_test.py` checks
that every command named in the **Command** column exists in this tool's CLI. When the
contract moves, re-read it, update the pin, and re-run the test.

## Operations

| Contract operation | Command | Notes |
|---|---|---|
| `claim(workId, owner, scopes, ttl)` | `acquire` | Owner is the run id (hashed per key since 1.22.0); ttl is `leaseTtlSeconds`. Atomic: a git-ref compare-and-swap in `leaseBackend: git`; an exclusive create in `local` lease mode — one machine only; cross-machine exclusion needs `leaseBackend: git`. |
| `renew(claimId, owner, ttl)` | `renew` | Only this run's LIVE leases (`held()`); an expired lease cannot be renewed. |
| `release(claimId, owner, outcome)` | `release` | `release` takes no outcome (see the gaps); one is recorded beside it by `merge` (landed) or `journal` (stopped). `release --held` releases everything this run holds. |
| `reserveId(namespace, owner, count, ttl)` | `reserve` | One id per call, no reservation ttl (see the gaps); `release-id` returns an unused one. Ids are printed at the register's own width. |
| `recordAsBuilt(claimId, revision, artifacts)` | `record` | Appends what was built, with its commit and files, to the record plane. |
| `reconcile(claimId)` | `reconcile` | Compares intent (git) with as-built (record plane). Residue is classified by `residue`. |

## Write scopes

The contract's scopes map to `guardedFiles` in `.claude/agent-sync.json`:
- `guard` answers whether this run may write a path (exit 2 means no).
- The PreToolUse hook asks `guard` before every Edit/Write.
- Bash writes are checked by the guard's command parser (1.22.0), in every session, for
  every target inside a repository whose `guardedFiles` match it — the shapes it reads are
  the capability matrix in `hooks/guard.sh`; what it cannot read is listed there as
  UNPARSED or UNSUPPORTED and is not enforced.
- A commit is checked against the index AND what the same command stages (`git add X &&
  git commit`, 1.22.0 AS-16).

## Lifecycle and residue

| Contract state | Here |
|---|---|
| claimed | a lock file (`fs`) or `refs/agent-sync/leases/<key>` (`git`) inside its TTL |
| released | the lock or ref is removed by `release` / the SessionEnd hook |
| expired | TTL elapsed. `status` and `residue` report it: `reapable` (this run's), `foreign`, or `ambiguous` |
| abandoned (owner reports failure) | `journal` the failure, then `release` |
| reconciling / reconciled | `residue` classifies; `reap` clears this run's own; `reap --i-own-this KEY` is an operator decision per key; `reap --long-expired --apply` is an operator's sweep of locks expired at least 7 days (time alone — not the contract's `abandoned`; see the gaps) |

**Residue is evidence.** Every operator-decided removal prints the payload it destroyed
and journals it. Nothing is cleared silently, as the contract requires.

## Not yet conformant

These are said here rather than implied by the table:
- **`claim` is atomic across machines only in `leaseBackend: git`.** In the default `local`
  lease mode the exclusive create is a mutex between processes on ONE machine; two
  machines on a shared or synced checkout are separated only by the lock's `host` field
  (classified `foreign`), not by an atomic operation.
- **Write scopes are enforced only where a hook can see the write.** A language runtime's
  own file write, `perl -i`, `awk`, `dd` and the other UNSUPPORTED rows of `guard.sh`'s
  matrix are outside the boundary; the contract's scope is advisory there.
- **Idempotency key per command.** Only `reserve` takes one (`--rkey`). `acquire` is
  idempotent for its own holder, but it takes no key.
- **Residue categories `uncommitted-files`, `unpushed-commit`, `open-pr` and
  `merged-unreconciled`.** These are reported by `finish` (clean, pushed, pointed at)
  rather than by `residue`.
- **`external-effect`.** Not modelled.
- **`release` has no `outcome` argument.** Success, failure and cancellation are not
  carried by the release itself; the outcome lives in a separate `merge` or `journal`
  line, so a reader of the claims log alone cannot tell them apart.
- **`reserveId` has no `count` and no `ttl`.** One id per call, and a reserved id never
  expires: an unused one is returned by `release-id`, or reported as a leak, never
  reclaimed on a timer.
- **Scope containment is not checked.** The contract's scopes conflict when one CONTAINS
  the other; here a task lease and a file's resource claim (`res--<repo>--<path>`) are
  separate keys, so a claim on `docs/` and one on `docs/x.md` do not conflict. Only the
  same path, or `guardedFiles` membership, is arbitrated.
- **"Expiry does not prove the writer stopped; a new owner MUST inspect branch and
  residue before writing the same scope."** `acquire` takes over an expired lease without
  inspecting anything, and `reap --long-expired --apply` removes residue by age. The
  first is the lease protocol's design (stealing is the ordinary acquire path); the second
  is kept an operator's decision for exactly this reason — see
  `references/lease-protocol.md` → *A sweep by age*. Neither performs the inspection the
  contract requires of the new owner; that stays the agent's duty.
