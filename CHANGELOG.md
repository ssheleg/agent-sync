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
