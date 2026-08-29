## v1.18.5 — a pipe reaches the guard, and the installers stop deleting blind

Two enforcement holes, each of the same shape: a mechanism whose own text claimed more
than its code did.

- **ASY-05 — a piped commit bypassed the lease guard entirely.** `guard.sh`'s tokenizer
  said "each `&&`/`;`/`|` segment is its own command" and consumed only `&&`, `;` and
  `||` — so `echo msg | git commit -F -`, the ordinary way to commit a generated
  message, was one segment whose first token is `echo`, and the whole pipeline skipped
  the guard. The single pipe and `|&` are now consumed (ordered after `||`, or each
  would shatter into stray halves), three new `GUARD_SHAPES` cover the bypass and the
  pipe that must NOT block (`git log | grep commit`), and the self-test plants the
  shipped tokenizer back (`a piped commit slips past the guard`). Watched failing
  against the pre-fix guard: both piped shapes reached a guarded file with exit 0.
- **The installers consulted nothing before deleting the Claude Code channel.** This
  member's installers never write `~/.claude/skills/agent-sync` themselves — the skills
  CLI they drive recreates it — and both deleted that copy unconditionally afterwards.
  The family canon this implements (make-skill v0.25.0, distribution.md §3) names the
  fail-open class, and here it ran in mirror image: on a home where the plugin is NOT
  installed — no claude CLI, or the plugin install failed — the prune destroyed the only
  Claude Code channel the very same run had just installed, and exited 0. The fate of
  the copy is now a decision read from the target home's
  `~/.claude/plugins/installed_plugins.json` (the record of what is installed; plugin
  and marketplace names differ, so the spec is taken from the JSON), with the
  `marketplaces/<name>` dir kept only as the fallback signal: plugin present → the
  shadow is pruned and the message names the real spec, the plugin-channel remedy and
  the family launcher; no plugin → the copy is kept, because it IS the Claude Code
  channel; `--force` → kept beside the plugin, as the recorded choice to run two
  channels where the stale one wins. Absent or corrupt JSON reads as "no plugin" — fail
  open, never crash. Only the Claude Code channel is gated; other agents' installs are
  untouched.
- **The install now says how the next version arrives** — `npx @ssheleg/agent-sync@latest
  update`, or the family launcher — because an installer that never mentions updates has
  still chosen an update model: never.
- `test/installer_test.js`: 11 cases against throwaway HOMEs with the delegated CLIs
  stubbed through PATH — plugin-present prune with the spec from the JSON, a
  differently-named marketplace, `--force`, corrupt JSON, a prefix-collider
  (`agent-sync-extra@x`), the marketplaces-dir fallback, the update path, and the
  install.sh mirrors. Ten of eleven watched failing against the pre-fix installers.
  Wired into `npm test` and CI; `hooks_session_test.py` — already in `npm test` —
  joins CI in the same step block.

## v1.18.4 — the channel that sends the installs, on npm too

- The `skills.sh` badge and the canonical `homepage` reached GitHub in the previous cycle and stopped
  there: npm serves the README and the metadata from the last **publish**, so the package
  page still showed a badge-less README and a homepage pointing at GitHub.
  This release carries both across.
- No behaviour changes. Cut because a change that lands on `main` and never publishes is a
  change the package's own readers cannot see.

## v1.18.3 — the shared seam is explicit

Both shared validators now state `diverges: none`, completing the umbrella
mechanism contract.

## v1.18.2 — shared guards identify their owner

The eval and social-preview validators now declare their umbrella-owned shared
mechanisms, so the family drift gate can verify the seam without treating the
copies as unrelated implementations.

## v1.18.1 — the coordination skill publishes its own trust surface

A root skill card now states the credential, backend and enforcement boundaries.
Portable trigger and behavioral evals cover leases, id reservation and the
submodule finish protocol, while recording no model score that was not run. The
README's first viewport is installable, CI adds the pinned house audit and eval
plant, and the generated social preview is committed at 1200×630.
The open Bash-guard gap is now `AS-09`, removing its collision with the already
closed token-budget row `AS-06`.

## v1.18.0 — a third backend, and two people who can finally see each other

`fs` keeps the record on one machine and `outline` costs $10 a month for a team; between
them sat the case this release is for — **two people, two machines, one plane, nothing to
pay**. `notion` is that plane. The container is a page, every log is a child page of it,
and every line is one paragraph block appended by `PATCH /v1/blocks/{id}/children`, which
is a server-side append with no read-modify-write anywhere in the class.

**The capability flags are earned, and the earning found two defects.**
`test/notion_live_test.py` ran against a live workspace on 2026-08-25 and passed on the
fourth attempt: `200 appends from two processes, 200 lines on one page`, `two reads return
one order`, `tree.ensure is idempotent`, `both fresh shards enumerated, 200 lines across
them`, and the forced degradation. The three runs before it are the reason to trust the
fourth. Run two went red at 171 lines with a dead writer: the retry path had never executed
in run one, and it turned out `TimeoutError` is an `OSError` rather than a `URLError`, so a
read that timed out **after** the connection was established fell past both retry branches
into the catch-all and failed permanently. Run three went red differently — 100 lines, all
from one writer, both exiting 0 — because the two writers each called `tree.ensure` on a
shared title and the check-then-create raced into two pages of that name. That one is a
finding about the measurement: `Sync.log_id()` says *this run's OWN shard, one writer per
document, always*, so the protocol never creates it. The contention case is now handed the
page id, and the case the sharded design actually depends on took its place — two fresh
shards, both enumerated, which is precisely what Outline's search index could not do.

The suite covers both halves of the retry loop now. The success half had never existed:
every assertion drove a transport that always failed, so nothing measured whether a retry
can succeed — a loop that cannot is a loop that only fails more slowly.

It needs credentials and a network, so it stays outside `npm test` and **says SKIP loudly**
rather than passing quietly where it cannot run. `exclusiveLease` stays false and no
measurement can move it: Notion has no compare-and-swap, and the endpoint's own
`conflict_error` is a retry hint rather than an arbiter.

**One registry of backends, because three copies of a two-item list is how a backend comes
to half-exist.** `CLOUD_ADAPTERS` is the single home; `init`'s argument parser, `check`'s
known-adapter test and `bootstrap`'s dispatch all read it. `bootstrap` used to build an
Outline collection whatever the project was configured for — on an `fs` project it demanded
Outline credentials for a backend that has no container at all. `check`'s credential test
now asks the adapter (`REQUIRED_ENV`, `preflight()`) instead of naming Outline's variables
in an `if`, and the Notion preflight is a real call — `GET /v1/users/me` — because "knowledge
base reachable" printed after a function that only parsed a string is the defect class this
suite exists to refuse.

**B-34's other half.** `_allocated_ids` still read `nextFreeIdPattern` directly while
`id_pattern()` beside it accepted both keys, so a config written with the modern `pattern`
key never discarded its own *Next free ID* line: `--set-baseline` stamped one above reality,
and every id at the true top read as pre-baseline and was never asked for an as-built
record. Reproduced in `fabric` on 2026-08-25, fixed here, fixture plants it back.

**`FsAdapter`'s docstring said its files are "committed and pushed"** while `check` reports a
tracked `.agent-sync/` as a problem — a committed run id hands one checkout's identity to
every clone. The check was right; the line now says so.

**And a defect in the suite itself.** `Sync()` calls `load_env_file`, which writes the
project's variables into `os.environ` — correct for the CLI, a leak in-process. Two checks
built temporary `fs` projects and left `AGENT_SYNC_BACKEND=fs` behind, and that variable
OVERRIDES the configured backend, so the next check to depend on it silently read the
previous check's project. The new dispatch check passed alone and failed in the suite, which
is the signature of this class rather than of the code under test. Every check now runs
through `_guarded`, which restores the environment and the working directory after it.

Five checks, five planted defects: 51 → 56 fixtures.

## v1.17.0 — the identity that had never once been established

`_session_key()` falls back to one `shared` entry per checkout when it cannot tell which
session is asking, and everything downstream hedges about it: `classify_lock` answers
`ambiguous` rather than `reapable`, so an expired lease can never be cleared by the run that
took it. The fallback was documented as the rare case.

It was the only case. Measured 2026-08-25 in a checkout that had been running the tool all
day: `.agent-sync/sessions` did not exist, and `.agent-sync/run-id` held exactly one key —
`shared`. The stamping block in `session-start.sh` required `CLAUDE_SESSION_ID` in the hook's
**environment**, and Claude Code delivers the id to a hook on **stdin as JSON**, which is how
`guard.sh` next to it has always read its own payload. So the block had never run, in any
session, since it was written. A fallback that is always taken is not a fallback.

The hook reads its payload now, with the environment variable still winning where it exists.
`test/hooks_session_test.py` runs the real hook as a process — the only way this was ever
going to be caught, because every unit around it was correct — and covers the four ways it
must behave: an id from stdin is stamped, an id from the environment still wins, a payload
carrying no id stamps nothing rather than keying every session alike, and a payload that is
not JSON leaves the hook exit 0, because a SessionStart hook that throws takes the session
with it. A fifth case walks the whole chain: stamp, then the descendant's key, then the
run-id map, then `classify_lock` returning `reapable` where it used to return `ambiguous`.

Watched failing against the previous hook: 2 of the 5 cases red, and green after.

Also: the README no longer tells a reader to run `python3 test/validate.py` and `npm test`
from a package that ships no `test/` directory. It names where they run.

## Unreleased — both `AS-01` halves exercised outside their fixtures

The two rows sat at priority `unverified`: shipped, and confirmed by nothing but their own
fixtures. Both are now exercised against real state, and both hold. **Nothing in the tool
changed** — this records that the mechanism was watched working on something other than its
own test data, which is what `unverified` meant.

**The git plane, on a real remote.** A git-mode checkout was built against a local bare
remote, a lease taken at `ttl 2`, and its local note **deleted** — exactly the state a ref
won on another machine leaves behind, and the state the row said `residue` could not see.
`residue` printed *nothing in the lock directory* and then enumerated the git plane:
`refs/agent-sync/leases/DEMO-KEY @ 495370743e`, run `r-rverifya`, **expired 18s ago**,
`foreign`, closing with *1 ref(s) on the remote, 0 this run can prove it owns and has spent*.

**Reap, in both directions.** Reaping as a **different** run left the ref standing and said so
by name. Reaping as the run that took it removed it and reported *confirmed gone by re-reading
the remote* — the proof coming from `ls-remote`, not from a push's exit code.

**And on live residue.** `~/DATA/0xDEV` carries a genuine foreign expired lock —
`BLOG-SITEMAP`, run `r-blog-1429e`, **expired 4d 22h ago**. `reap` named it, said *left alone
— it belongs to run r-blog-1429e, not to this one*, and **deleted nothing**: both lock files
byte-identical afterwards. State a run cannot prove is its own is reported and untouched.

## v1.16.0 — the release that closes one version string over two trees

**281 lines of shipped behaviour had been sitting behind the tag.** `AS-01a` (the
git plane is swept, not only disclosed) and `AS-01b` (an ambiguous lock is
clearable by a person, per key, attributably) landed on `main`, were pinned by the
umbrella, and were never tagged. So three channels served `1.15.0` and two
different trees:

| channel | source | `agent_sync.py` |
|---|---|---|
| npm `@ssheleg/agent-sync@1.15.0` | the tag | 4344 lines |
| the plugin marketplace | the branch tip | 4575 lines |
| the skills CLI | the branch tip | 4575 lines |

All three reported `1.15.0`. `check_pins.py` was green throughout — correctly,
because it compares the version STRING, and the string did match. A version that
identifies two artefacts cannot be reasoned about, and the family's own invariant
("the pin is the promise") was satisfied to the letter while being false in
substance.

Found on 2026-08-23 by fetching the npm tarball and counting lines in all three
channels rather than trusting any of them. This release makes the number true.

Also in this range: the release workflow refuses a tag whose commit no clone can
reach, and the evidence ledger records both `AS-01` halves exercised on a real
remote rather than in a fixture.

## v1.15.0 — a claim tag outlived its lease, and no command reached it

**GitHub issue #5, filed 2026-08-17, reproduced verbatim at v1.14.0.** A board row shipped
in a published release reading `(claimed: r-6e62c4dab)` while the lease plane said
`leases held: none`. `release <key>` printed `released`, exited **0** and changed nothing;
`residue` said *nothing on disk*; `reconcile` found *no mechanical divergence*. Three
commands, none of which could reach the tag, because `write_claim(key, None)` restores the
cell for the key it holds and it held none.

This is #4 from the other side. #4 was a lease that outlived its run; this is a tag that
outlived its lease — and the second is worse, because an invisible lease invites a check
while a confident wrong answer does not. Closed the same way: **one notion of held,
consulted by both planes.** `_lease_holder` stays the single reader, memoised per command
so a report over a tagged board costs one `ls-remote` per *tagged row* in git mode rather
than one per board row. `orphan_claims()` classifies a tag `orphan` — the TTL has ended its
lease — or `disputed`, live under another run, which is reported and never touched.
`release` clears an orphan and names the run it belonged to. `status`, `residue` and
`reconcile` each report one, so the disagreement is visible to a gate rather than to a
diff.

Four more, each with its plant watched refusing:

- **`residue` read as a complete answer in git mode and could not be.** It walks
  `.agent-sync/leases/*.lock`, which only the local plane writes, so a ref won on another
  machine was invisible. It now prints `⚠ INCOMPLETE IN THIS MODE` with the `ls-remote`
  command it does not run. The sweep itself is still open, and the row says so — a check
  that cannot look must not read as one that looked.
- **A `local` lock recorded no host**, so residue could not tell a lock written here from
  one written on another machine; 25 of 25 locks on this machine carried none. Both modes
  write it now, with a fixture separating two machines.
- **The ledger described an artifact nobody shipped**: its newest section was headed
  *(in tree, unreleased)* and quoted `PASS: agent-sync v1.13.0` while v1.14.0 was tagged,
  in `package.json` and on npm. Guarded three ways.
- **Two divisors for one token budget.** The family's auditor measured the pack's body at
  ~5084 tokens against a 5000 limit; this repo's own gate divided by 4 and passed at 4957.
  One divisor now (3.9, the auditor's), and the body is under it at ~4683 after a split.

Also corrected: the board's *"14 of the 17 live in repositories this row must not touch"*
recomputes to **16 outside this checkout, 13 outside it and the umbrella**, with the `find`
that produces each.

**Found while doing it:** a plant had stopped planting. `the script path is prose only`
substituted a paragraph that had since been reflowed, so it changed nothing — and a no-op
plant still reported `detected` for a check that never ran. Re-anchored on the values the
check actually reads.

Self-test fixtures 43 → **51**; the claim-cell suite 9 → **16 cases**.

## v1.14.0 — expiry ended a lease and left the file, and every reader folded that away

**`status` reported `leases held: none` over three expired locks in the directory it had
just read.** Not a bug in one function: *every* reader of lease state folded the TTL into
the read. `held()` keeps only what is this run's **and** alive; `_lease_holder()` returns
`None` for an expired lock; `all_holdings()` drops it. All three are correct for
exclusion — an expired lease is not held — and all three therefore give one answer for
*expired* and for *absent*. There was no fourth reader, so nothing could tell the two
apart, and `finish` printed `✓ no lease left held` beside a two-day-old corpse.

Measured across the family when the conformance audit went looking: **17 expired lock
files across 9 checkouts, the oldest 3 days 11 hours.**

### The mechanism

`classify_lock()` — pure, all arguments in — reads every `.agent-sync/leases/*.lock` as
`live` · `reapable` · `foreign` · `ambiguous`. `status` and `finish` report residue
instead of silence. Two new verbs: `residue` (report only, safe in any checkout) and
`reap`, which clears **only** provably-own spent state and refuses foreign or ambiguous
state out loud, with exit 1, even when it is named on the command line.

`reapable` requires four things together, and anything short of all four is reported and
never deleted: the lease is spent with a parseable clock; it records a run; that run is
this one; and the run id means something. That last clause carries the rule — a shell
with no session id is served one **shared** identity, so a matching run id under it
proves nothing and does not license a delete. In doubt: `ambiguous`.

**Teardown is verified by re-reading the directory**, not by trusting the delete's return
value. Driven by hand: with the lease directory made read-only, `reap` exits 1 with
*"MINE is STILL PRESENT after the delete … the teardown was not verified, whatever the
call returned"* — and the lock is still there, which is the point.

### Standing instruction 9

*A predicate cannot report the condition it folds into its answer.* That is the class,
and it is now in `docs/evidence/retro.md` rather than in this entry alone.

Self-test 38 → 43 fixtures, every one detected. Latency measured, not assumed: `status`
0.39 s against 0.31 s before; `guard` unchanged at 0.15 s — residue is not on the guard
path.

## v1.13.0 — the file carried two notions of *held* and they disagreed where it mattered

**A lease from a run that died could not be cleared by any command.** Measured in the field:
three locks past their TTL, one of them by **604x**, and the only way out was deleting the file
by hand — which is the single thing a coordination tool exists to stop anybody doing. The
refusal itself was correct; there was simply no path.

The cause is one function. `acquire` has always known a TTL runs out — `_steal_expired` exists
for exactly that. `_lease_holder`, which `release` consults, read **any** lock file as held and
never looked at the timestamp. So an expired lease was *gone* for `acquire` and *eternal* for
`release`, and the file never noticed it was answering one question two ways.

`_lease_alive()` is now the one definition, used by both. `release` reaps an expired foreign
lease and **says whose it was**, because the operator asked to release their own lease and is
getting somebody else's corpse cleared alongside it.

**What did not change is the half that matters.** A lease inside its TTL is still refused, and
that is fixtured beside the reap so the two cannot drift. An unparseable timestamp is treated
as expired rather than as a licence to hold forever — a corrupt lock was the other way to reach
the same unclearable state.

Fixtures 6 → **9**, each driving the shipped script as a process against a real project.
Closes #4.

### Also closed by measurement: #1

`claimTags` whole-row id matching made the claim unwritable on boards with a `Depends` column —
filed against **1.3.5**, and fixed by **B-42** on 2026-08-14 without anyone connecting the two.
Re-run against this version on the exact board shape the issue names (`| id | what | acceptance
| depends | decisions | status |`, with `T-02` and `T-03` both citing `T-01`):

```
$ agent_sync.py acquire T-01
docs/ROADMAP.md: `T-01` claim written through
| T-01 | … | open (claimed: r-cca8b75db) |
| T-02 | … | open |          ← untouched
| T-03 | … | open |          ← untouched
```

Closes #1.

## v1.12.0 — 246 kB of someone else's bytecode, in every install

**The published tarball carried a `.pyc` both ignore files were written to exclude.**
`@ssheleg/agent-sync@1.11.1` ships
`plugins/agent-sync/skills/agent-sync/scripts/__pycache__/agent_sync.cpython-312.pyc` —
**245.8 kB against 175.7 kB of source beside it**, 40% of the tarball, compiled by
whatever interpreter the publisher happened to be running. Verified by unpacking the
published artefact, not by reading the manifest.

`.gitignore:3-4` and `.npmignore:1-2` both exclude `__pycache__/` and `*.pyc`. **Neither
is consulted once `files` names a directory** — the whitelist wins, so the intent was
recorded twice and enforced nowhere. `files` now carries `!plugins/**/__pycache__` and
`!plugins/**/*.pyc`, and the packed result drops from **231.5 kB / 28 files to
125.8 kB / 27**.

**A filesystem walk would not have caught this**, which is why the new check asks npm.
`check_the_tarball_carries_no_bytecode()` reads `npm pack --dry-run --json` — the
packer's own answer to *what would ship* — and fails on any `.pyc` or `__pycache__` in
it. Where npm is absent it discloses rather than passing: a check that cannot look must
never read as one that looked. Watched failing against the exact state that shipped, and
added to `--self-test`, now **38 fixtures**.

### Fixed — three documents that told the reader something untrue

- **`references/hooks.md` said the hooks are removed by editing `.claude/settings.json`.**
  Nothing here ever writes a `hooks` block there, and Claude Code has no per-hook disable
  for a hook a plugin ships. A user who wanted them gone edited a file with no such block
  and concluded it had worked, while the SessionStart hook kept speaking in every session.
  The two real levers are named now — `enabledPlugins[…] = false` or `plugin uninstall` —
  plus the fact that every hook already self-disables in a project with no
  `.claude/agent-sync.json`.
- **`CONTRIBUTING.md` said five version surfaces move together; the validator enforces
  six.** The missing one is `VERSION` in `scripts/agent_sync.py`, and it is exactly what
  forced the 1.11.1 patch: a bump driver written from that page moved four of six and CI
  refused the tag. It is the constant `status` prints into every session, so its drift is
  invisible in the manifests and loud in the banner.
- **`CONTRIBUTING.md` said the self-test injects five defects; it injected thirty-seven.**
  The count is read off the run's own last line now. The number is the whole claim — a
  contributor who reads *five* will not think to add a fixture for the sixth thing they
  change.

Found by the nine-repository audit of 2026-08-16 (umbrella `B-72`; the three documents
are `F-agent-sync-05`, `-08` and `-11`).

## v1.11.1 — the gate can see an invariant it breaks elsewhere

**This gate can now see an invariant it breaks one repository away.** The family umbrella
routes work by matching a prompt against a table in `lib/triggers.js`, and every trigger
there must be a word this skill's own `description` advertises. Nothing here knew that
table existed. On 2026-08-16 `sheleg-design` 1.37.0 shipped green having dropped a phrase
that was still a live trigger, the umbrella found out minutes after the tag, and it cost a
patch release — because the member releases FIRST and the umbrella re-pins after.

`test/validate.py` now asks the umbrella's own checker (`test/advertised_check.js`), which
reads the module the hook itself calls. **No copy of the table lives here**, so there is
nothing to drift. With no umbrella above this checkout — a standalone clone, and CI — it
discloses rather than passing, because a check that cannot look must never read as one
that looked.

Watched refusing a real drop before shipping: every one of the seven members carrying
routed triggers had one of its own advertised phrases removed and every one of them failed
its own gate.

## v1.11.0 — releasing stops rewinding a cell, and a register with no pattern says so

Two more defects in the same afternoon that produced v1.10.1, both from using this tool
on a board that had just been worked through.

### Fixed

- **Releasing no longer rewinds a cell that moved.** The restore was verbatim, and in this
  family the claim cell IS the status cell — so `close then release` silently reopened a
  row closed with evidence minutes earlier. Caught by `finish` reporting the board
  uncommitted, not by anybody reading it. The protocol's intent is to remove *this run's*
  marker, not to rewind the cell, so a changed cell keeps its change, loses only the
  marker, and the note says so. An untouched cell is still restored exactly.
- **An id register with no pattern reports instead of crashing.** The script read
  `nextFreeIdPattern`; every config this family ships writes `pattern`. Both are accepted
  now — but the defect was the fallback: absence became `re.search("", text)`, which
  matches the empty string at position 0, so `check` took the found branch and died with
  `IndexError: no such group` rather than saying the register has no pattern. A component
  that never received its input approved and then fell over.

### Added

- **`test/claim_cell_test.py`** — 6 cases driving the shipped script as a process against
  real project directories, because these defects are about what the command does to a
  file on disk. **Four were watched failing** against the pre-fix script, including that
  exact `IndexError`. One of the six is a regression test for v1.10.1's own fix: a board
  where an id is cited by two other rows must still tag the row that owns it.

## v1.10.1

The claim tag could not be placed on nearly half a real board, and the lease was granted
anyway.

### Fixed

- **A row that CITES an id no longer defeats that id's claim.** `claimTags` in `cell` mode
  selected every row *containing* the id, so an ordinary board sentence — "closed by
  B-12", "blocked until T-1 ships" — made the id ambiguous and the tag was refused.
  Cross-referencing is what boards do: measured on `sshlg-skills`' own board on
  2026-08-14, **14 of 41 rows cited others, leaving 19 ids that could never be tagged**.

  The refusal itself was correct — guessing which row carries a claim is how a claim lands
  on somebody else's work. What was wrong is that `acquire` still returned `won`: the
  lease was granted while the registry silently carried no claim, which is precisely the
  state a claim exists to make visible.

  A markdown board declares which row is which in its **first cell**. `acquire` now
  narrows to the row whose id cell equals the key, and refuses only when no row's id cell
  matches. Tried *after* the existing marker narrowing, so `release` still trusts the
  marker it actually wrote rather than re-deriving the row.

  Covered by `check_claim_lands_on_the_row_whose_id_cell_matches()`, which builds a two-row
  board where the second row cites the first, and asserts the tag lands on exactly one row
  and that it is the right one — then that release still restores the file byte for byte.
  Watched failing before the fix: *"acquire wrote no claim — a row citing T-1 defeated the
  selector, and the lease was granted with the registry unmarked"*.

## v1.10.0

Following `task-pipeline` v1.53.0, which renamed the artifact root's default from
`docs/superpowers/` to `docs/evidence/` and made the root resolvable (`paths.artifacts`
in `pipeline.json`, else an existing `docs/evidence/`, else an existing
`docs/superpowers/` — **the legacy name stays supported and no run warns about it**).

Shipped surfaces name the new default now, which is why this is a minor rather than a
patch. This project's own records moved with the directory and were not rewritten: a
brief describes where things were when it was written.

## v1.9.0

### Changed

- **The installer now offers the family's routing block** (closing B-06 in the
  umbrella). Until now only `super-ux` delegated: install this skill on its own
  and no router was written at all, so an agent had the skill and no rule saying
  when to reach for it. The bundle installer wrote all eight, which is why
  nothing looked broken — the gap only opened for someone installing one member.

  Delegated to `npx sshlg-skills routers --member agent-sync` rather than
  reimplemented, for three reasons:

  - The block describes what the machine actually has. A lone member rendering
    the whole thing would print a table for routers nobody installed.
  - `--member` scopes the write to this skill's own section. Verified by damaging
    two sections of a real block and running this installer: its own was
    repaired, the other left exactly as it was.
  - The launcher is the only writer that copies the operator's global instruction
    file before touching it. That file has no version control behind it.

  `--no-install` keeps it from silently downloading a package nobody asked for.
  When the launcher is absent the command is printed instead of failing: ending
  an install in an error over an optional follow-up reads as a failed install.
  Both paths were exercised.

## v1.8.3

### Changed

- **The body is back inside the token budget** — ~5124 → ~4996 of 5000. Two sections
  were restating references that already carry the depth: *Two documentation sources*
  duplicated three sections of `two-sources.md`, and the stage list duplicated
  `pipeline-binding.md`. The body keeps the principle and the trap — `reconcile` is
  mechanical and refuses to judge whether the built thing matches the document, so
  reading its green as agreement is how a divergence survives both ends — and the depth
  stays where it already was.

## v1.8.2

### Changed

- `references/hooks.md` crossed 100 lines and gains the `## Contents` list the canon
  requires past that mark — generated from its own six headings. v1.8.1 did this for the
  six references that were over the line then; this one grew past it afterwards, which
  is the shape of the defect: the rule fires on a length that keeps changing.

## v1.8.1

### Changed

- **Six references over 100 lines now open with a `## Contents` list.** The
  canon asks for it because a partial read is what an agent actually does with
  a long reference; without the list, a partial read returns an arbitrary
  slice. Generated from each file's own `##` headings, so the list cannot
  disagree with the document.

## v1.8.0

**The board, cleared.** Nine rows opened by the two audits of 2026-08-10, closed with a check, a
measurement or a written decision — because a backlog nobody empties is a list of things everyone
has agreed to stop seeing.

### The two scenarios that were only ever driven by hand are now gated

Two agents contending for one task, and the guard across every shape a write arrives in, were
verified by executing them and had no check that fails on its own — the exact state in which the
first audit found six shipped defects. `check_two_agents_cannot_share_one_task` drives the full
sequence from two identities: the second run loses, is told who holds it, sees the holding in
`status`, is denied the guarded registry, cannot release a lease it does not hold, and the holder
can. `check_guard_covers_every_write_shape` drives nine payloads through the real hook — `Edit`,
`Write`, `NotebookEdit`, an unguarded file, `git commit`, `git -C <dir> commit`,
`cd <dir> && git commit`, `git log --grep=commit`, and malformed input — with and without the lease.

Planting the first defect took two mutations, not one, and that is worth recording: breaking only
`acquire`'s expiry check still refuses the steal, because `_steal_expired` re-reads the expiry
**inside** the critical section. The exclusion has two independent layers. The single-point fixture
was MISSED, which is how that was discovered.

### `check_merge_releases_only_its_key`

`merge --key` must release that lease and leave the others held. It was verified by reading the
code, and every defect the first audit found was in something verified by reading.

### `status` on a large repository: 3.2 s → 0.5 s

`check` resolved guarded and claim-tag patterns by walking the whole tree **once per pattern** —
five patterns over a 20 000-file repository is five full walks, measured at 3.2 s for a single
`status`, which is a `SessionStart` hook. It now walks once, and uses git's own index where there is
one, so `.gitignore` is honoured for free. Same output, six times faster.

### `check_commands_work_without_the_family_installed`

Runs the commands with `HOME` redirected at an empty directory — the state of every CI runner, and
where the task-pipeline ordering defect was obvious while this development machine could not see it.
The check proves its own isolation on a healthy project before asserting on a broken one, because a
probe that cannot fail is not a probe.

### The self-test: 6 min → 2.8 min

Each fixture now runs as its own process, eight at a time. It was a loop that reassigned module
globals and called `main()` in-band, once per fixture; at 32 fixtures it had already blown a
ten-minute command budget, and a suite people stop running is a suite that does not exist. The
subprocess also removes the global-state reset that made parallelism impossible.

### Decisions recorded rather than deferred again

**`settleSeconds` stays.** It is the adapter contract's extension point for a store whose writes are
not immediately readable; removing it would fail every config that carries one to buy nothing.
Written into `references/adapter-contract.md`, where the next reader will look.

**Blockers stay gone.** The concept needs a writer, a reader and a place on the board before the
word earns a document.

**Guard latency is fine:** 130–220 ms per guarded edit with no run id in the environment, against a
`PreToolUse` budget of 20 s. Measured, recorded, no change.

### Also

`actions/checkout@v5`, `actions/setup-node@v5`, `actions/setup-python@v6` — the v4 pins were being
forced onto Node 24 by the runner and annotated every release.

`merge` also gained a preflight it was missing. A merge commit is authorship, so it needs a real
git identity — unlike a lease object, which is plumbing and is written with a synthetic one on
purpose. Without one the merge started, git refused at the commit, and the abort path ran: it
recovers, and it is still not what a command whose whole doctrine is *every check before anything
is touched* promises. Found by CI, because a runner has no global `.gitconfig` and this machine
does — the same blind spot, twice in one day.

The validator now runs 44 checks and plants and catches 37 distinct defects.

## v1.7.1

**A second audit, along a different axis: not whether the tool keeps its promises, but whether an
agent reading this skill can follow it and get a correct result.** The scenarios were executed, not
imagined — cold start, the whole adoption chain, the per-task cycle, two agents contending for one
task, every hook with a realistic payload, and the edges (no git repository, uninitialised project,
absolute paths, `git -C <dir> commit`). The coordination core came through clean. What did not was
everything the agent *reads*.

### The documents the tool generates were two versions behind the doctrine it teaches

`setup` writes the snapshot every agent is told to read first — *"it states how documentation and
coordination work here"* — and `scaffold` seeds `AGENTS.md`. Neither mentioned a branch or `merge`
even once. Both prescribed a cycle ending in `release`, and the snapshot stated *"the claim tag in
git is written through"* as an unconditional fact, which has been false on any branch since 1.4.0 —
the branch being where the doctrine says the work belongs.

So an agent doing exactly what the skill instructs — trust the generated snapshot — got the
workflow from two releases ago. Regenerating did not help: the generator was what was stale. Both
now carry the branch rule, `merge --key`, and this project's integration branch by name.

`AGENTS.md` stopped restating the cycle altogether. It is seeded once and **never overwritten**, so
a copy of the protocol there is frozen on the day the project was created while the tool moves on —
and every project scaffolded before today would have kept the old one forever. It now points at the
snapshot, which is regenerable and which `check` fails on when it goes stale. One fact, one home.

### `check` blessed a project an agent cannot work in

A configuration declaring `idRegisters` on a backend whose `reserve` always raises passed as
`setup healthy`. `check`'s own promise is that it refuses a rule pointing at what is not there, and
a register nobody can allocate from is exactly that — while the snapshot it generates instructs
every agent to run `agent_sync.py reserve DEC`, which cannot succeed there. It is now a problem,
named with both ways out.

### The slash command offered a verb the CLI does not have

`argument-hint` advertised `claim <KEY>` and the README showed `/agent-sync claim ASC-072`. The
command is `acquire`; `claim` is an `invalid choice`. First thing an agent reads, first thing it
types.

### `$SKILL_DIR` was used in every example and defined nowhere

Six invocations tell the agent to run `python3 "$SKILL_DIR/scripts/agent_sync.py"`, and the only
explanation was the prose *"this skill's own directory"*. Nothing gave a value. The Cursor rule
names a concrete path; the skill body now names both — `${CLAUDE_PLUGIN_ROOT}/skills/agent-sync`
and `~/.agents/skills/agent-sync`.

### What the audit found working

Worth recording, because a report that only lists faults says nothing about the rest: two agents
contending for one task behave correctly end to end — the second loses, sees who holds it and in
which repository, is denied the guarded file, and cannot release a lease it does not hold. The
guard denies `Edit`, `Write`, `NotebookEdit` and absolute paths, and blocks `git commit`,
`git -C <dir> commit` and `cd <dir> && git commit` when a guarded file is staged, while letting
`git log --grep=commit` and malformed JSON through. `SessionStart` stamps the identity and prints
the awareness block; `SessionEnd` releases. The adoption chain works with and without a pre-existing
`docs/`. An uninitialised project and a non-git directory both answer with the next action and a
non-zero exit.

### New checks

`check_every_advertised_verb_exists`, `check_generated_docs_carry_current_doctrine`,
`check_registers_need_a_backend_that_can_reserve`, `check_skill_gives_a_resolvable_script_path` —
with four more self-test fixtures. The validator now plants and catches 32 distinct defects.

## v1.7.0

**Observability, honest degradation, and the removal of things that were never load-bearing.**
1.5.3 stopped the tool saying untrue things; 1.6.0 made its guarantees hold. This closes the
remaining findings from the 2026-08-10 audit — each one a place where the tool was quiet rather
than wrong, which is the harder failure to notice.

### `status` and `check` gave two answers about one project

`status` printed `NEXT: acquire a lease` and exited 0 on a setup `check` called NOT healthy — a
guard pattern matching no file, a snapshot nobody links, an env file tracked by git. `status` is
the command every session runs and the only one most agents ever read, so anything it stays quiet
about is effectively unreported.

The validation now lives in one function, `check_setup()`, which both commands call. `status`
prints the count, the first four problems and one next action; `check` prints the whole list.

### Credentials were adopted from anywhere above the project

`find_env_file` walked every parent directory until something matched, so a stray
`.env.agent-sync` in a home or work directory silently configured every project beneath it and
pointed them all at one collection — a coordination plane shared by projects with nothing to do
with each other. A found file looks exactly like a configured one, so nothing reported it.

The search is now `AGENT_SYNC_ENV` if set, then this repository, then each **superproject** in
turn — a tree git can vouch for, which is the case the walk existed to serve. `check` prints which
file is in force and says when it comes from outside the repository.

### "New since you last looked" lost entries that arrived out of order

The watermark was an index into a list re-sorted on every read. An entry appended with an earlier
timestamp — clock skew, or a shard that was briefly unreachable — lands before the mark, shifts
everything after it, and is never reported; the slice returns an entry already seen instead. The
one section of `status` whose job is to announce what changed went quiet about exactly the change
that arrived late.

Entries are now remembered by identity, capped at 500, with a floor timestamp covering only what
fell out of that window — set only when something actually did, or the fix would have re-created
the bug in a new shape.

### `merge` released every lease the run held

The documentation says it releases the lease; it released all of them, quietly freeing work that
had not landed. It now releases the one named by `--key`, and says what it left held.

### An `acquire`/`release` round-trip left a diff

`SKILL.md` promises `git diff` empty afterwards. The claimed row was rebuilt from its cells, so
indentation and the original line ending were dropped — an unexplained change to a shared registry
file, which is the one kind of file agents are told never to touch casually. The bytes outside the
edited cell are now carried through rather than reconstructed.

### Declarations that were never load-bearing

`LOGS` carried a `blockers` document nothing wrote and nothing read, so a reader looking for
blockers found an empty page and concluded there were none. `_held_legacy` and
`Adapter.is_exclusive` had no callers. `Sync.settle` was computed and never used —
`settleSeconds` stays in the schema for a backend that must wait for writes to become visible,
and `check` now says plainly that nothing shipped reads it.

`os.uname()` and a literal `/dev/null` are gone in favour of `platform.node()` and `os.devnull`;
the coordinator now runs wherever python3 does, and only the enforcement hooks need bash.

### The setup verdict sat behind the task-pipeline gate

`status` checked for `task-pipeline` and returned before it ever reported on the project. On any
machine without the dependency installed — every CI runner — a project defect was therefore
invisible: the command exited non-zero for a reason that had nothing to do with it. The order is
now project first, machine second, because one is a fact about the repository everyone shares and
the other is a fact about the box you happen to be on.

Found by CI, on the release run for this very version, against a check that had passed locally
all day — and the check has been tightened to assert the problem is *named*, not merely that the
exit code is non-zero.

### Three tagged releases never reached npm

`v1.5.0`, `v1.5.1` and `v1.5.2` each pushed a tag, ran the release workflow, and failed at the same
step: *"no CHANGELOG section for 1.5.2"*. The extraction matched `## 1.5.2` while this file writes
`## v1.5.2` — a heading style that changed at 1.4.x and a workflow that did not. The registry sat
three releases behind while every tag looked delivered, and the failure lived in the one place CI
never runs on `main`.

Both patterns now accept the `v` prefix — the stop pattern too, or the notes run to the bottom of
the file — and `check_release_notes_are_extractable` runs the workflow's **own** awk program,
lifted out of the YAML, against the current version. A release that cannot be described now fails
on `main`, before the tag.

### New checks

`check_status_reports_the_setup_verdict`, `check_env_discovery_is_bounded`,
`check_watermark_survives_a_late_entry`, `check_no_orphan_logs`, `check_no_dead_declarations`,
`check_claim_round_trip_is_byte_exact` — with six more self-test fixtures. The validator now
plants and catches 27 distinct defects.

## v1.6.0

> **Never released on its own.** There is no `v1.6.0` tag and no `1.6.0` on npm,
> so `npm install @ssheleg/agent-sync@1.6.0` and `git checkout v1.6.0` both fail. This section
> describes work that shipped inside a later version. The note is here because
> the section reads as a release (2026-08-17, umbrella `B-71`).

**Four guarantees that were described but not delivered, and the one number now stated once.**
1.5.3 stopped the tool reporting things that were untrue; this release makes the properties it
claims actually hold, each with a check that has been watched fail against the defect it exists to
catch.

### Stealing an expired lease had a window two runs could both pass through

`unlink` then `O_EXCL create` are two operations. A second stealer that has already read the lock
as expired removes the lock the first one just created, and both then hold what each believes is an
exclusive lease. Twelve racing processes never exposed it — with a 300 ms delay injected between the
two calls, two of two won, and in production that delay is an ordinary scheduler hiccup.

The reap and the create are now one critical section: `<key>.lock.steal` is created with `O_EXCL`,
the expiry is re-read **inside** it (the holder may have renewed; another stealer may have finished),
and the section carries a 30-second abandonment grace, because without one a crash between two
filesystem calls costs the key until a human deletes a file nobody documents.

This mattered more after 1.5.3 than before it: with `renew` finally refreshing leases, the steal path
stops being the common case — but for four versions every long task went through it.

### `merge` measured one base and merged into another

Conflicts and the diff were computed against `origin/<target>`; the merge was made into the **local**
`<target>`, which nothing advances. So `merge` printed the staleness it had just measured — `main
moved: 1 commit(s)` — then `✓ merged as …`, wrote a merge-log entry, released the lease, and the push
was rejected as non-fast-forward. The work had not landed, `docs/MERGES.md` said it had, and the task
was free for somebody else to take.

The local integration branch is now fast-forwarded to its upstream before anything else, so the
preflight and the merge share a base. One that has genuinely diverged cannot be fast-forwarded and is
refused, with both counts named.

### One glob meant two things

The guard matched with `Path.match`, which anchors at the **right**: with `docs/DECISIONS.md` in
`guardedFiles`, an edit to `vendor/docs/DECISIONS.md` was denied — a file `check` never enumerated
and never validated, protected by a rule nobody wrote. In the other direction `Path.match` does not
walk `**` before Python 3.13, so `docs/**/*.md` guarded less than `check` reported. A pattern that
means two things means nothing. Both commands now resolve patterns through one function, anchored at
the repository root.

### The 2% rule was a constant nothing read

`MAX_UNPARSEABLE` was declared in 1.0.0 and never referenced. `SKILL.md`, `lease-protocol.md` and the
README all stated that a log past the threshold stops the run; the only implementation was a warning
line on the board that returned 0. Every reader now refuses a log past the limit and names the ratio,
so `status`, `board`, `check`, `reconcile` and `reserve` stop rather than replaying a partial history
— which reports holders who do not exist and silence where the real ones are, both of which look like
an answer.

`acquire` is deliberately not in that list, and the documents now say so: a lease is decided by the
lock or the ref, never by the log, so a corrupt log cannot make one look lost.

### The stage binding said three different things

`SKILL.md` announced "four of the eleven stages" and listed five (0, 1, 3, 9, 10); the README named
0, 3, 4, 5, 9 and 10; `pipeline-binding.md` agreed with the README and separately called stage 1
*"nothing shared to coordinate"* — the stage `reconcile` belongs to, and the one the tool's own
doctrine says must resolve every divergence before code is written. An agent wiring `pipeline.json`
from any one of the three got a pipeline missing a rule.

The numbers now live in a marker in `pipeline-binding.md` — `rules=0,1,3,9,10`,
`wired=0,1,3,4,5,9,10` — the two lists answer two different questions instead of being conflated,
stage 1 has its row and its `pipeline.json` entry, and a check fails when a surface stops agreeing.

### New checks

`check_steal_is_atomic`, `check_merge_refuses_stale_target`, `check_guard_and_check_agree_on_globs`,
`check_unparseable_log_fails_loudly`, `check_stage_binding_agrees` — plus five self-test fixtures
planting each defect back.

## v1.5.3

> **Never released on its own.** There is no `v1.5.3` tag and no `1.5.3` on npm,
> so `npm install @ssheleg/agent-sync@1.5.3` and `git checkout v1.5.3` both fail. This section
> describes work that shipped inside a later version. The note is here because
> the section reads as a release (2026-08-17, umbrella `B-71`).

**Five surfaces told the caller something that was not true.** An audit on 2026-08-10 ran the
commands instead of reading them, and every finding below was reproduced before it was fixed. The
validator was green throughout — which is the finding behind the findings, and the reason this
release ships six new checks that drive the tool rather than its functions.

### `reserve` handed three runs the same id

`reserve` replayed `log_id("reservations")` — the document **this run writes**. Every other run's
shard was invisible to it, so three runs each saw an empty history, each seeded a `base` from the
register, and each was handed `DEC-0007`. Measured, three processes, one number.

The pure allocator was correct and tested; nothing ever asked it about the whole log. This is the
same failure that disqualified per-writer documents as a *lease* store in 1.0.0 — eight processes
reading only their own shard, eight winners — arriving in the allocation path, where the tested
unit hid it. Allocation now runs over the merged log.

Merging alone would have replaced one collision with another: two runs opening a register in the
same minute both append the same seed, and a `base` that re-seated unconditionally restarts the
count and hands the second run the id the first just took. A `base` now only ever moves allocation
**forward**; one at or below the current position is ignored.

### `renew` renewed nothing

It appended `op=renew` to the record plane — which has not decided a lease since 1.0.0 — and
touched a throttle file. The timestamp expiry is actually computed from, the one inside the lock
(or the git ref payload), was written once, by `acquire`.

So a run holding a lease lost it at TTL **while still working**: `whoami` reported `holds:
nothing`, its own `PreToolUse` guard began denying its edits, and another run acquired the task it
was in the middle of. With the default 2700 s, every task longer than forty-five minutes. The
`PostToolUse` hook made no difference, because there was nothing for it to move — and from the
record plane's side the renewals arrived exactly on schedule, so nothing looked wrong anywhere.
`renew` now rewrites the lock in `local` mode and re-pushes the ref with `--force-with-lease`
against the exact object it read in `git` mode.

### `check` rejected the config `init` had just written

`check` carried its own literal list of legal keys. `mergeLog` — written by `init` itself — and
`integrationBranch` — in the schema, in the shipped example, read by the code — were not in it, so
a freshly initialised project reported `config key 'mergeLog' is not in the schema — it will be
ignored`. Both halves false: it is in the schema, and it is not ignored.

That is worse than a wrong message; it is an instruction. An agent making `check` green deletes
working configuration. The list now lives in one place, `CONFIG_KEYS`, and the validator asserts it
equals the schema's properties exactly.

### Three commands reported success they had not achieved

`release-id` printed `released DEC-0007` and exited 0 on a backend that records nothing — the id
stayed a hole the board reports as a leak, and the only party who could have fixed it had been told
it was handled. `record` and `signal` printed success and exited 0 while stderr said the entry was
never published. All three now fail loudly.

An adapter `OSError` also walked past every `except Fail` into `main`: an unwritable state
directory handed the agent a Python traceback as the state of the coordination plane. Store errors
are now the tool's own failure type.

### The guard named a holder who held something else

A denial read `<path> is a guarded registry file and this run holds no lease — r-x holds a lease
right now`, where `r-x` held an unrelated task. Beside a path, that sentence gets repeated as "r-x
holds this file". The denial now names the run **and its key**, and says plainly that exit 2 is a
statement about the asking run, not about the file.

### The marketplace listing still sold the design 1.0.0 refuted

`marketplace.json` — the first thing anyone reads — described coordination as "decided by replaying
one append-only log so no backend needs compare-and-swap". That is the belief the first trap in
`SKILL.md` exists to forbid. Rewritten, and a check now fails on the phrase.

### Documentation corrected where it described behaviour that did not exist

`SKILL.md` and the Cursor rule on what exit 2 means; `lease-protocol.md` on what `renew` moves and
on the log a reader replays; the claim-tag vocabulary in `README.md` and `lease-protocol.md`, which
showed a role where the tool writes a run id; `hooks.md` on the `NotebookEdit` matcher, on what
`session-end.sh` actually does, and on the two timeouts that really apply.

### New checks

`check_reserve_is_race_free`, `check_renew_extends_the_lease`, `check_config_round_trip`,
`check_no_success_on_failed_publish`, `check_guard_denial_names_only_what_it_knows`,
`check_doctrine_is_current`. Each drives the real commands from more than one identity, because
every defect above lived in the gap between two things the validator already tested separately.

## v1.5.2

### `reserve` handed out ids that were already written

`reserve DEC` returned `DEC-0270`, then `0271`, then `0272`, in a project whose register's highest
heading was `DEC-0281` and whose own "next free" line read `DEC-0282`. All three were occupied;
`DEC-0270` was cited by name in another document.

The counter was not stuck — it incremented on every call. It was **11 behind**. The `base` event is
seeded once, from the register, and never consulted again. Every id written by a path that is not
this tool — a person editing the file, another session's Doc Loop, a merge — advances the register
and leaves the log where it was. The gap only ever grows.

This inverts the mechanism. An agent that follows the protocol exactly — reserve before minting,
never trust the register's own "next free" line, which is the rule the protocol states — is the one
that writes a duplicate, and it is silent at the point of use: the id looks fresh, the register
accepts a second heading with that number, and every citation to it becomes ambiguous. An agent that
ignored the tool and read the line would have been correct. That is the worst incentive a
coordination tool can teach.

The register is now consulted on **every** reserve and treated as a **floor**: it can push the
allocation forward, never pull it back. Ids reserved through this tool but not yet written do not
appear in the register, so honouring the floor never revokes a live reservation. The floor is
applied by re-basing mid-log, which the allocator now supports properly — a new `base` restarts the
count (it previously kept serving from the old one, skipping as many ids as had been handed out) and
drops freed ids that fall below it (the register has moved past them, so a heading exists there now,
and recycling one is the same collision through the other door). Ids freed at or above the base are
still reused, so the fix does not turn every release into a leak.

Proven end to end against the register that exposed it: the shipped 1.5.1 returns `DEC-0273`, an
occupied id; this build returns `DEC-0282`, then `DEC-0283`. Three assertions in `test/validate.py`,
all watched failing against a planted revert of the allocator — 202 where 200 was due, and a freed
`0100` handed back.

Found in nicegram-business, 2026-08-09, by an agent that recognised the returned number from another
document. That is luck, not a control, which is why the check now exists.

## v1.5.1

### `release` could not remove the claim it had written

`acquire` writes a marker into the status cell; `release` found the row again **by searching for the
task id**. Between the two, a run's own work can add another mention of that id — a new row, a
cross-reference — and release then sees several candidates, refuses to guess, and leaves
`(claimed: r-…)` in the cell **permanently**.

The result is worse than no claim: a status cell advertising a live lease that nobody holds, which
the next agent reads as an occupied file.

Release now narrows by the marker before falling back to the id. The marker names *this run*, so it
is unambiguous however many rows mention the id. Proven both ways on the same scenario — acquire
writes the tag, a second mention is inserted, and release removes it (`claim restored`); the
previous version leaves it behind.

## v1.5.0

### The commit guard was blind to `git -C <dir> commit`, and to submodules

Two defects, and the second is why the first went unnoticed.

**The test was a contiguous substring.** `case "$cmd" in *"git commit"*)` — and `git -C <dir> commit`
does not contain the string `git commit`. Every commit made that way skipped the guard entirely, in
any repository, submodule or not.

**The repository was hardcoded to `CLAUDE_PROJECT_DIR`.** So even a bare `git commit` inside a
submodule read the *umbrella's* index, found it empty, and passed: the staged files live in the
submodule's index.

Together they meant a full day of commits to guarded registers went unchecked, on 2026-08-07, while
the Edit-tool half of the same hook refused correctly the whole time — so the protection looked
present and was measured as present by anyone who tested it with the Edit tool.

The command is now tokenised in python rather than globbed in shell: `-C`, `-c` and `--namespace`
are consumed with their arguments, `cd <dir> &&` is honoured, each `&&`/`;`/`||` segment is examined,
and the guard runs **from** the resolved repository — `agent_sync.py` resolves the project from
`git rev-parse --show-toplevel` of its cwd, so a submodule gets its own `guardedFiles`.

Proven against the previous version on all three forms: `git -C <sub> commit` and
`cd <sub> && git commit` go from `rc=0` to `rc=2`, and `git log --grep=commit` stays `rc=0` — the
tokeniser exists so that one does not become a false positive.

# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 1.4.3 — 2026-08-03

### Fixed

- **The `pipeline.json` example did not validate against the schema it cited.** It
  claimed `task-pipeline`'s `pipeline.schema.json` permitted it while carrying a
  string `id`, a `title` where the schema says `name`, and no `state` at all — which
  is required. Anyone who copied it got a config `task-pipeline` rejects. The example
  is now checked against that schema.
- **Gate texts stated only this plugin's half.** Each `check` read as if it replaced
  the stage's own criteria, so a host that copied the block silently dropped them —
  stage 9 lost the propagation sweep and the documentation gate, stage 10 lost the
  ladder walk and the evidence rule. Every `check` is now written as
  *`<the stage's own criteria>` **AND** agent-sync's clause*.
- **Stage 9 pointed at the wrong doctrine.** `task-pipeline:artifacts` is the
  artifact-layout reference; that stage runs on `task-pipeline:documentation` and
  `task-pipeline:gates` since the documentation track landed.
- **`guardedFiles` did not cover what the pipeline now creates.** `docs/DOCMAP.md`
  holds a project's registers, propagation matrix and ratchet floors, and
  `docs/superpowers/retro.md` is capped at ten standing instructions — so a
  concurrent write there drops a lesson instead of conflicting visibly. Both are
  guarded, with the reasoning in `references/pipeline-binding.md` because the config
  schema keeps `agent-sync.json` to known keys.

## 1.4.2 — 2026-07-30

### Changed
- **The README's hook section now opens with what it actually is:** the only part
  of this plugin that executes code on your machine, four bash scripts with 15-20s
  timeouts, run by Claude Code on named events, with a pointer to `SECURITY.md`
  for every path the install touches. The facts were already in `SECURITY.md`; the
  README described the hooks' behavior without ever framing them as the security
  surface a reader should check first.

### Added
- **`displayName`** ("Agent Sync") in both manifests.

## 1.4.1 — 2026-07-30

### Fixed
- **`homepage` and `repository` sat at the top level of `marketplace.json`,
  where Claude Code does not recognize them.** They are plugin-entry fields;
  moved there, so the values reach the plugin listing instead of being ignored.
  This plugin's `argument-hint` was already quoted — the only one in the family
  that was.

### Added
- `claude plugin validate --strict` runs in CI against both the plugin and the
  marketplace manifest: the upstream schema, next to this repo's own validator.

## 1.4.0 — 2026-07-30

### Work happens on a branch, and the integration branch stays somebody else's stable base

Two agents renamed this repository the same minute yesterday, both committing straight to
`main`. The second push was rejected — after the work was already duplicated. Nothing in
the tool said where work should happen, so both did the obvious thing.

- **`acquire` writes the claim through only on the integration branch.** On any other
  branch it says so and writes nothing: a claim committed to a branch is invisible to
  every other agent until the merge, and it turns the shared roadmap into a file two
  branches both edit — a conflict on the one file that exists to prevent collisions. The
  holder lives in the coordination plane, where `status` already shows it to everyone
  without anyone fetching a branch.
- **`integrationBranch`** in the config, or the repository's own default branch when unset.
  Asked of the repository, never assumed.

### `merge` — land a branch, with every check before anything is touched

`merge` refuses a detached HEAD, the integration branch itself and a dirty tree; fetches
the target and reports how far it moved; computes conflicts with **`git merge-tree`, in
memory**; lists every other run's live lease; merges `--no-ff`; records the merge; and
releases every lease this run holds. On conflict it names the files, changes nothing and
exits non-zero — a merge that starts and aborts leaves the operator in a repository they
did not ask for, and a resolution nobody reviewed does not belong on the integration
branch. `--dry-run` stops after the checks, `--push` pushes afterwards.

### `merges` — what landed while you were on your branch

A merge log at `docs/MERGES.md` (`mergeLog.file`), written by `merge`. It answers what
`git log` cannot without reading every commit and what a changelog only covers once
released: *what landed while I was away, and was any of it near my work.*

**Compaction happens on write.** Entries inside `mergeLog.retentionDays` (7) keep their
detail; older ones fold to one line each on the next merge. No cron, no second command,
and no log that grows until people stop reading it — which is the same as not having one.

### Added
- `test/validate.py` exercises both rules against throwaway repositories: `acquire` on a
  feature branch must leave the roadmap untouched **and** say where the claim lives, while
  on the integration branch it must still write through; a conflicting `merge` must exit
  non-zero, name the conflicting file, leave the checkout on the original branch with a
  clean tree, and write **no** log entry for a merge that did not happen. Both have
  self-test fixtures that remove the guard and confirm the check goes red.
- `references/branching.md`, and the doctrine in the skill body.

## 1.3.9 — 2026-07-30

### Changed
- `license: MIT` added to the `marketplace.json` plugin entry. The front matter
  already declared it — this repo was the only one in the family that did — but
  the plugin listing, which is what a user reads before installing, did not.


## 1.3.8 — 2026-07-30

The 1.3.6 move was performed twice, by two agents in this repository within the same
minute, and neither held a lease on it. Both renames were correct and nearly identical —
the duplicated effort is the cost, and this project exists to make it visible. This
release carries the two things only one of them had, and it is the release that moves the
npm package page: 1.3.6 and 1.3.7 were tagged and never published.

### Changed
- **`LICENSE` and the README footer read `ssheleg`.** The rename moved every address and
  every manifest identity, and left the copyright line naming the previous owner — the one
  statement in this repository with legal weight.

### Added
- **`test/validate.py` fails a half-finished rename.** It derives the canonical slug from
  `package.json` and rejects any other owner of this repository name anywhere in the tree:
  in a URL, in a `github:` install argument, in a `marketplace add` line, or as a bare
  quoted slug. That last shape is what the installers actually clone, and the first draft
  of the check missed it — `bin/agent-sync.js` and `install.sh` both passed while still
  pointing at the old owner. Verified red against the pre-rename tree; the self-test
  fixture assembles its slug from parts, because written whole it makes the validator the
  file that fails the check.

## 1.3.7 — 2026-07-30

### Fixed
- **The 1.3.6 note below claimed `raw.githubusercontent.com` does not follow a
  repository transfer. It does** — the old owner's raw path returns 200 with the
  current file, no redirect involved, because GitHub resolves a transferred
  repository by identity on every surface (git, web and raw alike). Measured
  after the move, which is what the claim should have been before it was
  written. The paragraph is corrected in place so no install ships the wrong
  fact; the reason to update the URLs is unchanged and stated accurately there.


## 1.3.6 — 2026-07-30

### Changed

The repository moved from `appvillis-com/agent-sync` to **`ssheleg/agent-sync`**,
joining the rest of the family under one owner. GitHub keeps serving the old path
on every surface, so nothing breaks today — but that only holds while the
`appvillis-com` name is never re-registered, and a reference that depends on
somebody else not taking a name is worth one commit to remove.

- **Install paths** — `install.sh`, `bin/agent-sync.js` (the `npx github:…`
  fallback), and the README's npx and `claude plugin marketplace add` commands.
- **Identity** — `package.json` (homepage, repository, bugs, author),
  `.claude-plugin/marketplace.json` (owner, homepage, repository, plugin author),
  `plugins/agent-sync/.claude-plugin/plugin.json`, and the `author` in the skill's
  front matter: `appvillis-com` → `ssheleg`.
- **Raw URLs** — `agent-sync.schema.json` `$id`, the `$schema` in
  `agent-sync.example.json`, and the reference-loading fallback URL in `SKILL.md`.
  A `$id` is an identifier as much as a location, so it should name the repository
  that actually holds the schema.
- Cursor rule, code of conduct and the security-advisory link in the issue-template
  config.

The changelog entries below and `docs/superpowers/specs/` keep the old path on
purpose: they record where this was published at the time.

## 1.3.5 — 2026-07-29

### The two rules this plugin enforces are now stated, with the failures that taught them

Both are already code. Neither was written down, and a mechanism nobody can explain is a mechanism
the next person removes.

- **Identity comes before coordination.** Both ways of getting it wrong have happened here: one
  session with two identities (acquired as one, denied by its own guard as the other) and two
  sessions with one identity, which is worse because it is silent — `whoami` reported a lease that
  belonged to somebody else and `release` would have taken it.
- **Work in a submodule is not finished until its parent points at it.** Neither repository looks
  wrong alone; the disagreement lives between them, which is why it survives every check that runs
  inside one. That is what `finish` is for.
- **The rule under both:** before trusting a tool's report about the world, make it report something
  you can already verify.

## 1.3.4 — 2026-07-29

### Fixed
- **`release` reported success for a lease it did not release, and cleared the
  board claim on the way.** The lease plane refused correctly — a lease held by
  another run stayed held, and `_git_release` printed a note saying so — but the
  command printed `released <key>` over the top of it and exited 0, and
  `write_claim` had already blanked the claim cell before the refusal was
  reached. The result was the board advertising a task as free while the lease
  still held it: the exact disagreement a lease exists to prevent, manufactured
  by the tool. Ownership is now checked **first**, in whichever plane arbitrates
  it; nothing is written when the answer is no; the command exits non-zero and
  says who holds it. Both lease modes were affected.
- A regression check covers it in `local` and `git` mode, and was probed against
  the old code in both before being trusted.

## 1.3.3 — 2026-07-29

### Fixed
- **The git lease backend could not take a lease on any machine without a git
  identity.** Acquiring writes a lease object with `git commit-tree`, which
  refuses when `user.email` is unset and cannot be auto-detected — CI runners,
  containers, a freshly provisioned box. So the backend that advertises
  *exclusive across machines* failed on precisely the machines least likely to
  have a personal git config, with `could not create the lease object` and no
  further detail. The lease object is plumbing, not authorship: it now carries a
  fixed `agent-sync <agent-sync@localhost>` identity passed inline, so it never
  depends on ambient config.
- The same failure now reports git's own last line instead of swallowing it. The
  bug survived six red CI runs because the message named a possible cause and
  showed no evidence.

## 1.3.2 — 2026-07-29

Open-source hygiene — the repo is public and ships in the `sshlg-skills` bundle,
so the files a first-time contributor looks for now exist.

### Added
- `CODE_OF_CONDUCT.md`, issue forms and a pull-request template. The forms ask
  the question that actually matters for this project: **how many agents were
  running, against what checkout** — a coordination bug reported without the
  concurrency shape is not reproducible.
- The PR checklist requires a negative self-test with any new validator guard —
  plant the defect, watch the check fail, then trust the green.
- README points at the code of conduct and at the family bundle.

## 1.3.1 — 2026-07-29

### The git lease was invisible to everything that reads a lease — fixed

Found in production, blocking real work three times in one session. In `git` lease mode `acquire`
won the lease by pushing `refs/agent-sync/leases/<key>` and stopped there, while `held()` — the one
function behind `whoami`, `status` and the **PreToolUse guard** — read `.agent-sync/leases/*.lock`,
which nothing in that path ever wrote. The result was the exact inversion of the tool's purpose:
`acquire` printed *won*, `whoami` printed *holds: nothing*, and the guard **denied the run that held
the lease**. Every guarded register was unwritable under the mode this tool recommends, and the only
way past it was to bypass the guard — which is the behaviour the guard exists to prevent.

The git ref remains the authority; it is what makes exclusion hold across machines. What was missing
is that the winner now leaves a local note, so the local question — *does this run hold that key?* —
is answered locally instead of putting a network round-trip in front of every Edit. `release`
already removed that note, which is why only one half of the loop was ever written.

**Why it shipped:** the lease-visibility assertion existed only for the local mode. `test/validate.py`
now runs acquire → `whoami` → `guard` → release against **both** modes; against 1.3.0 it fails with
the two symptoms above, which is the point of adding it.

## 1.3.1 — 2026-07-29

### Ignoring the state directory does nothing once git is tracking it

Found in the project this plugin was built for: `.agent-sync/` was gitignored **and committed**,
because the files went in before the rule existed. Consequences, all of them silent:

- every tool call rewrites `last-renew`, so all three repositories were permanently dirty and no
  run could ever report itself finished
- `run-id` is the checkout's **agent identity**. Committed, it reaches every clone — two machines
  would then coordinate as one run, which is the failure 1.3.0 fixed at the other end

`init` now untracks the directory when it finds it tracked, and `check` reports it as a problem
rather than passing a project whose state is versioned. Probed: a repository with a committed
`.agent-sync/run-id` fails `check` with the exact removal command, and passes once it is untracked.

## 1.3.0 — 2026-07-29

### Two agents in one checkout were one identity — fixed

Found in production, in the case this plugin exists for: **two Claude sessions working the same
checkout shared a single run id**, so the lease could not separate them. A hook runs with
`CLAUDE_SESSION_ID` in its environment and a plain shell command does not, and the marker file held
one id per checkout — so the second session adopted whatever the first had stamped. Both acquired
as one run, both were guarded as one run, and `release` would take a lease the caller never
acquired. The failure is silent: `whoami` reports a lease, and it is somebody else's.

- the marker is now a **map** keyed by session, and migrates the old single-value file into it
- a plain shell has no session id, so the `SessionStart` hook stamps
  `.agent-sync/sessions/<CLI pid>` with the session it *does* know, and later commands find
  themselves by walking their own ancestry. Exact, and no command-line parsing: the throwaway
  shell every tool call runs in carries claude paths in its argv and defeated every heuristic
  aimed at the CLI binary
- stale stamps are removed when their process is gone, so the directory cannot grow
- where identity still cannot be established, the run says so rather than presenting a shared
  entry as separation

### `scaffold --full` — the architecture that keeps documentation linked, not merely present

`scaffold` seeded a decision register and an agent protocol. That is enough to be coordinated and
not enough to stay coherent: the things that rot are the links between documents, and nothing was
seeding the pieces that hold them — a question register that resolves into decisions, an index
nobody has to scan the register to use, one place for facts about two repositories, one definition
per entity with a checkable address, **and a gate**, because each of those decays silently.

`--full` adds `OPEN_QUESTIONS.md`, `INDEX.md`, `DEPENDENCIES.md`, `DATA_MODEL.md` (with the entity
register and the one-definition rule) and `scripts/check-docs.sh`, which fails on: an id cited and
never defined, a next-free-ID line that is not next, a relative link to a file that does not exist,
a `#anchor` that does not exist in the file it points at, and a decision with no index row. All
five probed against planted defects.

**A fresh scaffold passes its own gate.** The first version did not — it counted the template block
and the allocation line as real ids — and a project that starts red teaches everyone that the gate
is noise.

### `finish` — the gate expressions this plugin declares, executed

`references/pipeline-binding.md` has always listed *submodule pointers current* and *every lease
released* as gate expressions "verified by the coordinator, not by prose". Nothing ran them:
`check` validates the **setup** — config, registers, credentials, snapshot — and never looks at the
state of the repositories.

`finish` answers the other question, *is the work finished*:

- every submodule's recorded gitlink equals its HEAD. This is the failure it exists for and it is
  invisible from either side alone: the submodule is pushed, its CI is green, its roadmap says
  done, and a clone of the parent has the commit before the work
- every repository — parent included — is clean and pushed, with a detached submodule accepted
  only when its commit exists on some remote branch
- no lease left held, because a run that ends holding one blocks the next agent for the whole TTL
- `--gates` also runs the project's own declared gate commands

## 1.2.4 — 2026-07-29

### Fixed — the tool misreported its own version, and disagreed with itself about the lease
- **`VERSION` drifted a release behind.** The constant said `1.2.2` while every manifest
  said `1.2.3`, so each `status` and `adopt` header named the wrong version — the exact
  number the README tells an operator to compare when hunting a stale install channel.
  `check_version_sync()` read five manifests and not the script; `check_scripts_run()` ran
  `--version` only to prove the process starts, and threw the answer away. The constant is
  now part of the sync check, so this cannot drift silently again.
- **`gated` was decided by the record backend, which has not decided a lease since 1.0.0.**
  It read the adapter's `atomicAppend`/`totalOrderRead` capabilities, and both directions
  lied: `outline` with a local lock reported `gated` while exclusion was machine-local —
  the pretended lease the skill's own trap 2 warns about — and `fs` with git refs reported
  `ungated` while every lease was a genuine cross-machine compare-and-swap. It now derives
  from `leaseBackend`.
- **Six surfaces phrased the guarantee independently, and two called the knowledge base
  the "lease authority".** `status` said `lease authority: NO — degraded` for the same
  project where `check` said `exclusive on this machine` and `acquire` said something else
  again. One guarantee described three ways reads as three guarantees, and an operator acts
  on the weakest. The wording now lives in one table (`lease_guarantee()`), used by
  `status`, `acquire`, `check`, the board, the setup snapshot and `init`. `status` reports
  the record plane and the lease as the separate facts they are.

### Added
- **`test/validate.py` exercises the agreement**: for `leaseBackend` `local` and `git` it
  runs `status`, `acquire` and `check` against a throwaway repository (a real bare remote
  for `git`) and fails if any of them omits the guarantee, or if `status` still calls the
  record backend the lease authority. Verified red against 1.2.3, green after.

## 1.2.3 — 2026-07-29

### Fixed — the guard blocked commits in projects that never installed agent-sync
`_lib.sh` states the contract: *"Every hook is a no-op in a project that does not use
agent-sync, so installing the plugin globally changes nothing elsewhere."* `guard.sh` was
the one hook that never sourced `_lib.sh`, and it honored that contract on only one of its
two branches.

- **The `git commit` branch had no configuration check.** It ran `agent_sync.py guard` on
  every staged path; in an uninitialized project that command exits 2 with *"no
  `.claude/agent-sync.json` in this project"*, which the loop read as "this run holds no
  lease" — so every commit in every repo without agent-sync was blocked, with a message
  naming a lease the project could not possibly need. The single-file branch had the check
  all along, which is why the failure only ever surfaced on commits.
- `guard.sh` now sources `_lib.sh` and gates on `agent_sync_configured` like the other three
  hooks, so the check cannot drift apart from them again. The hand-rolled `AGENT_SYNC_PY`
  path and the duplicated `[ -f … ]` test are gone.
- The staged-path listing now runs against `${CLAUDE_PROJECT_DIR:-$PWD}`, the same directory
  the configuration check reads. Before, the two could point at different repositories.

### Added
- **`test/validate.py` exercises the no-op contract** instead of only checking syntax: every
  hook runs against a throwaway git repository that has a staged file and no
  `.claude/agent-sync.json`, and must exit 0. Verified red against the pre-fix `guard.sh` and
  green after — a `bash -n` pass could never have caught this.

## 1.2.2 — 2026-07-29

### Changed
- **The npm package is `@ssheleg/agent-sync`.** Unscoped `agent-sync` was rejected on publish
  with a 403: npm's name-similarity policy fires only on `PUT`, so `npm view` reporting E404
  ("free") predicts nothing — the collision was with an existing `agentsync`. Scoped names are
  exempt from that policy, which is the documented fix.
- **The command is still `agent-sync`.** A package's `bin` name is independent of its package
  name, so nothing about daily use changes; only the install line grows a scope.
- GitHub install (`npx github:appvillis-com/agent-sync`) and the Claude Code plugin are
  unaffected — the registry only ever bought the short name.

## 1.2.1 — 2026-07-29

### Fixed — documentation that contradicted the code
Compressing the skill surfaced three statements that measurement had already disproved and
that nobody had gone back to correct. This is the drift the tool exists to catch, in the
tool's own documentation.

- `lease-protocol.md` opened by declaring that *no backend offers compare-and-swap, so both
  leases and id reservations are decided by replaying one append-only log*. Half of that is
  still true — id allocation is positional over the log — and half has been false since
  1.0.0. It now separates the two mechanisms, because confusing them is how this went wrong
  twice.
- The same file still said the **run** writes the claim tag and the tool only verifies. As
  of 1.2.0 the tool writes it through.
- `backend-fs.md` described a "git-file lease" — commit the lock, push it, read the
  rejection — a design that was never built. Leases have never depended on which knowledge
  backend is configured, and the file now says so.

### Changed
- `SKILL.md` trimmed from 4779 to 4325 tokens (13% headroom under the 5000 cap), with the
  full measurement history left in `CHANGELOG.md` and `lease-protocol.md` where it belongs.
- `lease-protocol.md` gains the cross-machine section it was missing.

## 1.2.0 — 2026-07-29

### Added — the claim is written through to the roadmap again
Demoted to a check in 1.0.0 because an unattended process rewriting a shared registry is
the collision a lease exists to prevent. It is back, with that objection engineered out:

- **One row.** The single table row containing the task id as a whole word. Zero rows →
  nothing happens. Two or more → **refused**, with the reason. It never guesses.
- **One cell.** Only the configured cell changes; links, notes and every other column are
  untouched byte for byte. `cell` is 0-based, negative counts from the end.
- **Reversible.** The previous text is stored in `.agent-sync/claims.json` and restored
  verbatim on release — not a default, what was actually there. After acquire→release,
  `git diff` on the roadmap is **empty**.
- **Atomic.** Written to a temp file and moved into place, so a crash cannot leave the
  register half-edited.

Closing a task is still yours: the tool refuses to write `done` on your behalf, because a
status a machine sets is a status nobody checked. `references/roadmap.md` documents the
whole cycle — claiming, closing, re-planning, and what to do when the claim cannot be
written.

### Added — cross-machine leases
`leaseBackend: "git"` pushes a commit to `refs/agent-sync/leases/<key>`, and the remote's
non-fast-forward rejection **is** a compare-and-swap. Verified against a hosted remote
before being written: A created the ref, B pushed a different commit without force and was
rejected, the ref still held A. Then eight parallel processes against the real remote —
**one winner, seven losers all naming it**.

Expired leases are stolen with `--force-with-lease` against the exact object seen, so a
steal cannot clobber a holder who renewed in between. `local` remains the default and is
still exclusive between processes on one filesystem; `acquire` and `check` now say which
guarantee you actually have instead of implying the stronger one.

## 1.1.0 — 2026-07-29

The skill can now take **any** project from nothing to a validated setup on its own.

### Added
- **`check`** — validates the whole setup and refuses to call a broken one healthy. It fails
  on a register whose allocation pattern matches nothing, a guard glob that matches no file
  (a rule protecting nothing), a gate whose script is missing, a mirror source that is not
  there, empty credentials, a `.gitignore` that misses the env file — or that file being
  **tracked by git**, the one unrecoverable mistake here — a hand-edited or stale snapshot,
  **a snapshot no agent instruction file links**, and a register with no baseline. Every one
  of those failed for real during this tool's own adoption.
- **`scaffold`** — creates the documentation architecture where it is absent: a decision
  register with an allocation line and an `AGENTS.md` pointing at the generated snapshot. It
  never touches an existing file. A tool that rewrites a project's conventions on adoption is
  worse than one that does nothing.
- The snapshot is stamped with a **hash of the configuration** it describes, so staleness
  means the configuration moved on — not that a commit happened. Comparing commits was wrong
  at both boundaries: a snapshot is generated before the commit that carries it, and the
  config is usually added in that same commit, so the very first adoption always looked stale.

## 1.0.1 — 2026-07-29

### Fixed
- **A rate-limited knowledge base stopped the work, not just the record.** `journal`, `record`
  and `signal` raised when the store was unreachable or throttling, so a burst of shard creation
  could fail a run outright. The plane carries visibility, not correctness: publishing now
  reports the gap loudly and lets the caller continue. Swallowing it would hide a hole in the
  record; raising made an availability dependency out of a notebook.
- Retries widened to seven attempts for `429` and transient `5xx`, which is what a burst of
  document creation actually needs.
- Run journals moved to the shard naming scheme (`20 Runs — <run>`) so they are enumerated like
  every other log.

## 1.0.0 — 2026-07-29

A full audit of the running system against its own promises. Three surfaces were
configured and unimplemented, and the central safety claim was false. All measured, none
inferred.

### Fixed — the lease was not exclusive
- **A shared append-only document loses writes.** Twelve concurrent appends to one Outline
  document returned twelve successes and left **three** lines: `editMode: append` reads,
  appends and writes back, so simultaneous writers clobber each other and each is told it
  succeeded. A lease decided on that can be held by two runs, each with proof.
- **Sharding per writer fixes the loss and breaks the decision.** 12/12 land, but without
  compare-and-swap nothing can answer "is a contender still writing?", so eight parallel
  processes each read only their own shard and **eight won one key**. A three-second settle
  window took it to five. It cannot reach one.
- **Exclusion now comes from `os.open(O_EXCL)`** — an atomic create is the decision, and the
  plane carries the record. Twelve parallel processes, **one winner, eleven losers all naming
  the same holder**. Publishing to the plane can fail without affecting correctness, so it is
  reported rather than raised.
- The limit is stated instead of implied: a lock file is exclusive between processes on one
  filesystem, advisory across machines. `exclusiveLease` joins the capability set and defaults
  to false, because declaring it without compare-and-swap is the most damaging lie an adapter
  can tell.

### Fixed — configured but not implemented
- **`claimTags`** appeared in the schema, in every config and in DEC-0216, and was read
  nowhere. `status` now reports where a held lease and the git claim tag disagree — and says
  plainly when the configured mapping *cannot be verified at all*, which is the case in the
  project that shipped it. The tool verifies; the run writes. A process that rewrites a shared
  registry unattended from a hook is the mechanism that clobbers other agents' work.
- **Mirror drift detection** was asserted in a docstring beside code that never checked it.
  `status` now reports pages whose stamped commit is not HEAD.
- Transient `5xx` from the knowledge base are retried like `429`; twelve concurrent document
  creations had been failing outright.

## 0.6.0 — 2026-07-29

### Fixed
- **The mirror was configured, documented and not implemented.** `mirror.enabled` and
  `mirror.sources` existed in the schema, the config and the generated setup snapshot, and
  nothing read them — a surface with nothing behind it, which is the failure mode that reads as
  finished. `board --mirror` now renders each configured document into the plane, stamped with
  the commit it was made from, refusing any page whose generated marker a human removed. A cap
  on the number of files is reported rather than applied silently: a quiet truncation reads as
  "everything is mirrored" when it is not.

## 0.5.0 — 2026-07-29

### Added
- **`adopt`** — inspect an existing project and *propose* a configuration. Adoption is where a
  coordination tool most easily starts lying: guess a register wrong and every later check is
  confidently about the wrong file. So it reads the repository, prints what it found, prints the
  decisions it **refuses to make for you** (a registry file carrying ids with no "next free id"
  line cannot have allocation reserved safely), and writes nothing. In a submodule it proposes no
  registers at all, because decisions belong to the parent repository.

## 0.4.0 — 2026-07-29

Found by simulating three agents working three repositories at once, entered from one
umbrella — the arrangement this tool is for. Every defect below was invisible from inside
a single checkout.

### Fixed
- **Every submodule agent ran isolated, in degraded mode, seeing nobody.** A submodule is
  its own git repository, so the project root is the submodule and `.env.agent-sync` — which
  lives in the superproject — was never found. Three agents entered from one umbrella and
  coordinated with nothing, each reporting `ungated` while believing it was configured. The
  env file is now located from the superproject and parent directories, so one credential
  file serves the whole tree.
- **A submodule's `reconcile` reported every umbrella decision as an orphan.** The as-built
  log is shared by all repositories; id registers are per-repository, and a service repo
  declares none because decisions live in the parent. Comparing the shared log against a
  local register produced a wall of false findings — the loudest possible way to teach
  people to ignore a check. Register checks are now scoped to what the checkout can judge,
  and say plainly when they are not evaluated here.
- **Regenerating the board from a submodule replaced the shared view with a narrower one.**
  Four repositories wrote one page; last writer won. The board now carries only facts true
  from every checkout, and repo-local findings moved to their own generated page.

### Added
- **`setup`** writes a generated snapshot of how *this* project is wired — registers,
  guarded files, gates, the two documentation sources, what is written where, and what is
  never deleted. Commit it and link it from the project's agent instructions so every agent
  reads the same description of the pipeline instead of inferring it from behaviour. It is
  generated rather than hand-written, because a hand-written description of a configuration
  drifts from it, which is the exact failure this tool exists to surface.
- **The lifetime and deletion protocol is now stated.** Nothing in a log is edited or
  deleted: the logs are replayed in order, so removing a line silently rewrites a
  conclusion other agents already acted on. Correct by appending. Generated pages are the
  narrow exception, and one that has lost its marker is refused, not overwritten.

## 0.3.3 — 2026-07-29

### Fixed
- **The enforcement hook ran in a different mode from the agent it was guarding.** A hook is
  spawned with a bare environment and never inherits the operator's
  `set -a && . ./.env.agent-sync`, so every hook silently fell back to the `fs` backend while
  the agent's own commands used the cloud. Consequences, both invisible: the guard **denied
  edits whose lease was properly held**, and it recorded runs as `ungated` while the agent had
  been told `gated`. The gate was structurally broken in exactly the scenario it exists for.
  The tool now loads `.env.agent-sync` itself — the path is deterministic, so correctness must
  not depend on how the process was invoked. An already-set variable still wins.

## 0.3.2 — 2026-07-29

### Fixed
- **`reconcile` demanded an as-built record for an id nobody had taken.** The id scraper
  matched every `DEC-\d+` token in a register, including the "Next free ID" pointer — the one
  number that by definition is *not* allocated. Two symptoms, one cause: a permanent false
  finding on the unallocated id, and a baseline stamped one higher than reality, which quietly
  excused the newest real decision from ever being checked. The register's own
  `nextFreeIdPattern` now identifies that pointer and subtracts it.
  Found by running the new duty against this project rather than by reading the code.

## 0.3.1 — 2026-07-29

### Fixed
- **The guard blocked the lease holder.** A hook runs with `CLAUDE_SESSION_ID` in its
  environment and a plain shell command usually does not, so the run id was derived two
  different ways for one session: the agent acquired a lease as `r-f49d900b9` and was then
  denied by its own `PreToolUse` guard as `r-5ef2554fe611`. The primary flow — acquire,
  then edit a guarded register — could not complete. Found when the gate refused the very
  commit that was writing its decision record.
  The marker file is now authoritative for the checkout, with the session name recorded
  beside it: a different session rotates the id, while a run that merely *learns* its
  session name adopts it instead of rotating.

## 0.3.0 — 2026-07-29

### Fixed
- **Three of the four hooks were dead on macOS.** They called `timeout`, which is GNU
  coreutils and absent from a stock macOS, so `session-start`, `renew` and `session-end`
  all died with "command not found" — leases were never renewed and never released
  there, the exact abandoned-lease failure this tool exists to prevent. A portable
  `run_limited` helper now uses `timeout`, then `gtimeout`, then a plain-bash watchdog.
  `guard` was unaffected, so enforcement itself never lapsed.

### Added
- **The as-built record, and the duty to reconcile it against git.** Git documents say
  how it *should* be — written before the code, often without it. `70 As-built` says how
  it *actually is*, derived from what agents really wrote. Two source-of-truths answering
  two different questions; the gap between them is the finding, not a defect. New
  `record` and `reconcile` commands, wired into the pipeline's docs-study stage (resolve
  divergence before writing code) and docs stage (update both sides, then re-check).
- **`reconcile` is a ratchet, not a flood.** `--set-baseline` stamps today's ids as a
  counted backlog that may only shrink; ids written after it must carry an as-built
  record. A check that fails on all of history is a check that gets switched off.
- **Awareness names the repository.** Work spans several repos entered from one umbrella,
  and "r-alpha holds ASC-072" is only actionable once you know which checkout it is in.
- **`npx agent-sync update`** — updates every channel and prunes the shadow copy in the
  same step, because `npx skills update --global` recreates it on its own even when
  claude-code was never targeted.
- **`install.sh`** POSIX fallback and a **Cursor rule** (`cursor/rules/agent-sync.mdc`,
  no relative links, since the file gets copied into foreign projects).

## 0.2.0 — 2026-07-29

Coordination is not only mutual exclusion. An audit against the stated purpose — *agents
see what each other are doing and pick up important changes in time* — found the tool
enforced exclusion and delivered neither half of the awareness.

### Fixed
- **`status` shows what other runs are doing.** It reported only the caller's own leases,
  so an agent learned a task was taken and nothing about who held it or what they were
  touching. A lease you cannot see makes you blocked; a lease you can see makes you
  coordinated.
- **Cross-repo signals were write-only.** `signal` appended and nothing ever read the log,
  so a producer was still never told a dependency had been filed against them — the exact
  failure the feature exists to prevent. `status` now surfaces what landed since this run
  last looked, watermarked per run so it stays quiet until something actually changes.
- **The board renders recent signals** alongside live leases.

### Notes
- Verified with three concurrent runs across two repositories: an agent working inside a
  submodule alone sees the leases and signals of agents in the parent repository, because
  both read one coordination plane.

## 0.1.0 — 2026-07-29

First release.

### Added
- **Lease authority with TTL** — `acquire` / `renew` / `release`, decided by replaying
  one append-only log. Document order is authoritative; timestamps only expire a lease,
  because agents' clocks differ and the protocol must not depend on them.
- **Race-free id reservation** — positional allocation over the same log, so two agents
  cannot be handed one number. An id reserved and never written to git is reported as a
  leak rather than silently reclaimed.
- **Pluggable adapter contract** — six primitives, three declared capabilities
  (`atomicAppend`, `totalOrderRead`, `search`), and a mandatory honest-degradation path:
  a backend that cannot arbitrate exclusively must refuse lease authority and say so.
- **Backends** — `outline` (hosted or self-hosted; server-side append gives a total order
  without compare-and-swap) and `fs` (local, degraded, `ungated`).
- **Run journal and cross-repo signal feed** — `filed → accepted → delivered → closed`,
  so a producer learns a dependency was filed against them.
- **Generated board** — commit-stamped, and it refuses to overwrite a page that lacks the
  generated marker, so a page a human took over is reported instead of clobbered.
- **Claude Code hooks** — `SessionStart`, `PreToolUse` (deny a guarded edit or a commit
  staging one without a live lease), throttled `PostToolUse` renew, `SessionEnd` release.
  The guard exits 2 on its own internal failures, because any other code fails open.
- **`init` as the first command** — it asks where coordination state lives instead of
  guessing, writes committed shape and a gitignored mode-600 env file, and leaves the
  token line empty for the operator to fill.
- **Validator with a negative self-test**, plus CI.

### Verified against a live instance
Built and then exercised end to end against a real Outline deployment, which surfaced three
defects the unit-level work had not:
- **Markdown normalisation.** The store rewrites a `- ` bullet to `* `, so the log parser now
  emits `- ` and accepts `-`/`*`/`+`. Anchoring to the character written rejected every line
  the server returned.
- **A silent pre-filter hid malformed lines from the counter meant to expose them**, so the
  unparseable ratio read 0% while nothing parsed. Anything entry-shaped now reaches the pattern
  and is counted.
- **An unreadable log reported as a lost race.** `acquire` now raises above a 2% unparseable
  ratio rather than naming a holder who does not exist.
- HTTP error bodies are surfaced instead of dropped — a bare `400` cost a debugging round when
  the response said `collectionId: Invalid UUID`.
- The collection may be given as a UUID, a `urlId`, or the whole `name-urlId` slug from the
  address bar, because that is what a person actually copies.

### Notes
- Hooks exist only in Claude Code. Elsewhere the same checks run as a self-check and the
  run is recorded `ungated` — a documented limit, surfaced rather than hidden.
- Requires [task-pipeline](https://github.com/ssheleg/task-pipeline) for its stages.
