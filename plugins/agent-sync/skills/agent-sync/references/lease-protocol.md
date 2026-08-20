# Lease and id-reservation protocol

**Read this when** changing acquisition, expiry, stealing, or id allocation — or
when two agents disagree about who holds something.

## Contents

- [Line grammar](#line-grammar)
- [Acquiring — the third design, and the first that is true](#acquiring--the-third-design-and-the-first-that-is-true)
- [Expiry and stealing](#expiry-and-stealing)
- [Releasing](#releasing)
- [Residue — what expiry leaves behind](#residue--what-expiry-leaves-behind)
- [Id reservation](#id-reservation)
- [The lease is not the claim](#the-lease-is-not-the-claim)


Two different mechanisms, and confusing them is how this went wrong twice:

> **A lease is decided by an atomic operation** — `O_EXCL` on one filesystem, or a pushed
> git ref across machines. **An id reservation is decided by replaying the log**, where
> allocation is positional and every reader computes the same answer.

The log never decides a lease. It records one, so other agents can see it.

## Line grammar

One event per line, appended, never edited. Exactly this shape:

```
- `2026-07-29T10:42:13Z` `op=acquire` `key=ASC-072` `run=r-7f3a91` `ttl=2700` `repo=account-session-connect` `sha=9bba6d2`
```

Parsed by:

```
^[-*+] `(?P<ts>[^`]+)`(?P<pairs>(?: `[a-z_]+=[^`]*`)+)$
```

**Emit `- `; accept `-`, `*` or `+`.** The bullet is deliberately liberal because a
knowledge base normalises markdown on the way in — Outline rewrites `- ` to `* ` —
so a parser anchored to the character you wrote rejects every line the server hands
back. Observed live, and it presented as a lost race rather than a parse failure.

Required on every line: `op`, `key`, `run`. `op` is one of
`acquire` · `release` · `renew` · `base` · `reserve` · `release_id` · `signal` · `journal`.

Unparseable lines are **counted and reported**, never guessed at. Anything
entry-shaped (`^[-*+] \``) that fails the full pattern counts as unparseable; blank
lines, prose and the generated marker are skipped without counting.

**Do not put a narrower pre-filter in front of the pattern.** A `continue` that
tests for the exact bullet you emitted skips malformed lines *before* they can be
counted, so the ratio reads 0% while nothing parses — the guard and the counter both
go quiet at once. This is not hypothetical; it is how the bug above stayed invisible.

**An unreadable log is not a lost race.** Past 2% unparseable, **every command that
replays a log refuses it** — the read itself fails, so `status`, `board`, `check`,
`reconcile` and `reserve` stop and name the ratio instead of acting on a partial
history. Replaying it would report holders who do not exist and silence where the real
ones are, and both of those look exactly like an answer.

`acquire` is not in that list, and the reason is the design: the lease is decided by the
lock file or the git ref, never by the log, so a corrupt log cannot make a lease look
lost. Until 1.6.0 the threshold was a constant nothing read — declared, quoted in three
documents, and implemented nowhere except a warning line on the board that returned zero.

## Acquiring — the third design, and the first that is true

```
1. free     os.open(lock, O_CREAT | O_EXCL) — this is the decision, and it is atomic
2. held     live holder -> report it; this run -> already ours
3. expired  take the steal section, re-read expiry inside it, then reap and create
4. lost     FileExistsError, or the section is held -> read the holder and report it
5. won      write {run, ts, ttl, repo}; publish op=acquire to the plane for visibility
```

**Step 3 is one critical section, not two calls.** `unlink` followed by `O_EXCL create`
leaves a gap, and a second stealer that has already read the lock as expired removes the
lock the first one just created — both then hold what each believes is exclusive. Twelve
racing processes never showed it; a 300 ms delay injected between the two calls produced
two winners out of two, and in production that delay is an ordinary scheduler hiccup. So
`<K>.lock.steal` is created with `O_EXCL`, the expiry is re-read **inside** it (the holder
may have renewed, or another stealer may have finished), and it carries its own 30-second
abandonment grace — without one, a crash between two filesystem calls would cost the key
until somebody deleted a file nobody documents.

**Publishing is not the decision.** A failure to reach the knowledge base costs
visibility, never correctness: the lock is already held. So the append is wrapped and
its failure reported, not raised.

### Why not the knowledge base

Two earlier designs were measured and rejected. **One shared append-only document** loses
writes: twelve concurrent appends, twelve reported successes, three lines present — so a
lease decided on it can be held by two runs, each with proof. **One document per writer**
loses nothing (12/12) but cannot decide: without compare-and-swap nothing knows whether a
contender is still writing, and eight parallel processes each read only their own shard —
**eight winners for one key**. A longer settle window reached five, never one.

`O_EXCL` answers what the store cannot: twelve processes, one winner, eleven losers naming
the same holder. Full history in `CHANGELOG.md` (1.0.0).

### Cross-machine: `leaseBackend: "git"`

A lock file is exclusive between processes on **one filesystem**; two machines have two.
For cross-machine exclusion the lease is a commit pushed to
`refs/agent-sync/leases/<key>`, and the remote's non-fast-forward rejection **is** a
compare-and-swap — verified against a hosted remote, then proven with eight parallel
processes: one winner, seven losers naming it. Expired leases are stolen with
`--force-with-lease` against the exact object seen, so a steal cannot clobber a holder who
renewed in between.

The tool reports which guarantee is in force; it never implies the stronger one.

**Residue in this mode is on the remote, and the enumerating read goes there.** The local
directory holds a *note* — `_note_local` writes one only for the run that won the key on
this machine — so a ref won elsewhere leaves nothing to walk, and for a while `residue`
could print `nothing on disk` over an expired lease sitting on the remote. That is the one
shape a residue report must never take. `residue` and `reap` now sweep
`refs/agent-sync/leases/*` on the configured remote and classify each ref with **the same
classifier the local plane uses**, because whose lease this is must have one definition.
`reap` deletes with the same `--force-with-lease=<ref>:<sha>` compare-and-swap `release`
uses, and proves the ref went by re-reading `ls-remote` — a push's exit code is the wish,
the second read is the state.

**A remote that cannot be reached is `COULD NOT LOOK`, never an empty sweep.** The two read
identically to an operator and mean opposite things, so they are printed differently and
`reap` exits non-zero on the first.

## Expiry and stealing

A lock is expired when `now > ts + ttl` for the timestamp inside it.

**`renew` moves that timestamp, in the plane that arbitrates the lease** — it rewrites the
lock file in `local` mode, and re-pushes the ref with `--force-with-lease` against the
exact object it read in `git` mode. The `op=renew` line it also appends to the record plane
is visibility, not renewal.

That distinction is the whole of the bug fixed in 1.5.3: `renew` wrote *only* the record
line. The lock's `ts` was written once, by `acquire`, so a run holding a lease lost it at
TTL while still working — its own guard began denying it, and another run acquired the task
it was in the middle of. Nothing reported it, because from the record plane's side the
renewals were arriving exactly as scheduled. **A renewal that does not move the timestamp
the expiry is computed from is not a renewal**, however faithfully it is logged.

Default `ttl` is 2700 s (45 minutes). `renew` is emitted at most once per
`renewIntervalSeconds` (default 300 s) — by the `PostToolUse` hook in Claude Code,
and by the agent itself everywhere else. A `renew` for a key this run does not hold
refreshes nothing and says so.

**Stealing an expired lease is the ordinary `acquire` path.** There is no force flag: the
steal section above removes an expired lock and creates the new one, as one operation. The
steal is visible on the plane with both run ids, so an operator can see that it happened
and when.

## Releasing

`release` on every path, including failure. An abandoned lease is indistinguishable
from active work until its TTL runs out, and during that window the task looks
taken. Report the failure and release; do not hold the lease "in case".

## Residue — what expiry leaves behind

**Expiry ends a lease. It does not remove a file.** Every reader of lease state in this
tool folds the TTL into the read — `held()`, `_lease_holder()` and `all_holdings()` each
answer *none* for an expired lock and *none* for a lock that is not there. That is exactly
right for exclusion, and it means an expired lock is not un-owned but **invisible**.

Measured across the nine repositories of one skill family on 2026-08-19: **seventeen lock
files, all seventeen expired**, the oldest by three days. `status` printed `leases held:
none` / `other runs: none holding anything` in a checkout holding three of them, and
`finish` printed `✓ no lease left held` beside a two-day-expired one. Nothing was wrong
with any of those answers. Nothing enumerated.

So `status` and `finish` report residue, `residue` prints it in full, and `reap` is the
only thing that removes any of it. The split between the two kinds is the mechanism:

| Verdict | What it means | What happens to it |
|---|---|---|
| `live` | still inside its TTL | held, not residue — untouched |
| `reapable` | this run PROVABLY owns it and has spent it | `reap` clears it |
| `foreign` | it demonstrably belongs to another run or another machine | reported, left alone |
| `ambiguous` | ownership or expiry cannot be established at all | reported, left alone |

**What makes ownership provable** — all four, or the verdict is not `reapable`:

1. the lock is past its TTL (a live lease is held, not residue);
2. it records a `run`, and that run is this one;
3. **this run's identity is not the shared fallback.** `run_id()` keys its marker by
   session; a shell with no session id is served one shared entry, and that identity is
   shared with any other session in the same checkout. Under it a matching run id proves
   nothing — so it does not license a delete, and every such lock is reported as
   `ambiguous` instead;
4. the lock's `repo` is this checkout and its `host` — written by the git mode, absent in
   `local`, where the lease is machine-local by construction — is this machine.

In doubt the answer is `ambiguous`, never `reapable`. A classifier that resolves doubt by
deleting is worse than none, because it deletes under a claim of authority.

**And that has a consequence the classifier cannot fix: nobody could ever clear an
`ambiguous` lock.** A shell with no session id answers to the shared identity, so a
matching run id proves nothing, so the lock is `ambiguous`, so no run reaps it — for ever.
28 of them had accumulated on this machine by 2026-08-20, the oldest overdue by 21 days
against a 2700-second TTL, every one of them correctly refused.

`reap --i-own-this <key>` is the way out, and it is a **person's** decision rather than a
run's proof. M-50 forbids a RUN from deleting what it cannot establish; it does not forbid
an operator from deciding. Three things keep it a decision instead of a sweep:

* **named keys, and it refuses to run without them** — a blanket override is the sweep the
  classifier exists to refuse, wearing a flag. Use `--` before a key that starts with a
  dash: the tool slugifies guarded-file paths into keys like
  `-claude-plugin-marketplace-json`, and a key the tool writes must be a key the tool can
  address;
* **it refuses a LIVE lease** — residue is what it clears, and a live lease belongs to a run
  that may still be working;
* **it prints the payload it destroyed** — run, timestamp, machine, how long expired — and
  journals the decision where a record plane is configured. The classifier's verdict is
  printed unchanged beside it, because the override does not make the lock provably yours;
  it makes the deletion attributable.

**Teardown is verified by re-reading the state, never by the delete's return value.**
`unlink` returns nothing and raises nothing on a filesystem where the entry survives the
call — a read-only mount, an NFS write that never lands, another process recreating the
name. `reap` therefore reads the lease directory again and compares `(run, ts)`: a lock
still there under the same identity is a **failure**, however cleanly the delete went, and
nothing is reported as cleared. A key that came back as another run's live lease *was*
torn down, and calling that a failure would teach an operator to ignore the one message
that matters.

`reap` never touches `foreign` or `ambiguous` state, including when it is named on the
command line: naming one is refused out loud with a non-zero exit, because an operator
reads a silent zero as done.

### Why `release <KEY>` may reap a foreign corpse and `reap` may not

They look contradictory and they are not. `release` clears an expired lock in another
run's name — that is the remedy for the #4 incident, a lease measured at **604×** its
2700-second TTL that no command could clear, and it is tested
(`test/claim_cell_test.py`). What makes it safe is the thing `reap` does not have: **a
person named that one key.** The reap is announced in the output, because the operator
asked to release *their* lease and is getting somebody else's corpse cleared as well.

`reap` is a sweep nobody named key by key. A sweep that deletes what it cannot prove it
owns is how one run destroys another's only record of work in progress — an agent that
stopped renewing may still be running. So the sweep refuses, and the two verbs keep two
contracts on purpose. Do not "align" them by widening `reap`.

## Id reservation

Reading a "next free id" line from a file is not reserving it. Allocation is
**positional over the log**, so no agent has to trust another's arithmetic.

A register is opened once:

```
- `…` `op=base` `key=DEC` `value=0216` `run=r-bootstrap`
```

Then, replaying in order and maintaining a free list:

- `op=release_id key=DEC value=NNNN` pushes `NNNN` onto the free list.
- `op=reserve key=DEC` takes the free-list head if it is non-empty; otherwise it
  takes `base + (count of prior reserves not served from the free list)`.

Every reader computes the same assignment for every reserve line, including its own —
**and "the log" means every shard merged, never the one this run writes.** Reading only
its own document is how `reserve` handed three runs `DEC-0007` three times (fixed in
1.5.3): each replayed a log containing only its own lines, each seeded its own `base`
from the register, and each was correct about a history nobody else shared. The failure
is the same one that disqualified per-writer documents as a *lease* store, arriving in
the allocator — so a `base` now only ever moves allocation **forward**, and two runs
opening a register in the same minute cannot restart each other's count.

**An id you reserved and did not write to git must be released** with
`release_id`. An id that is reserved, unreleased and absent from git after its run
closes is reported by the board as a **leak** — never reclaimed automatically,
because a half-written decision on a branch is not the same thing as an unused
number, and silently handing it out again would produce two documents with one id.

## The lease is not the claim

| Fact | Home | Lifetime |
|---|---|---|
| Who holds this task **right now** | the lease log | ephemeral, TTL |
| Who **owns** this task | the git claim tag — `todo (claimed: r-7f3a91)`, the template from `claimTags.held` | durable |

`acquire` writes the git tag through and `release` restores exactly what was there. The
objection that once demoted this to a check — an unattended process rewriting a shared
registry is the collision a lease exists to prevent — is engineered out rather than
accepted: one row, one cell, refused on ambiguity, atomic, and reversible from stored
state. See `roadmap.md`.

Do not add a third place that records ownership — a project with two claim vocabularies
has, in practice, none.
