## v1.23.4: the guard fails silent on a wrong-shaped payload, the snapshot carries no em dash (2026-10-28)

- **Done.** the guard fails silent on a wrong-shaped payload, the snapshot carries no em dash: the family member gate (`test/family_gate.py`, with its shared-mechanism
  declaration) and the documentation merged since v1.23.3, released.
- **Next task.** After merge: tag `v1.23.4`, watch the release, then the sshlg-skills pin bump; then
  the highest open board row.

## v1.23.3: the guard reads Codex's apply_patch (2026-10-27)

- **Objective.** Hub board row B-211 (agent-sync part): the lease guard never acted on Codex edits,
  which arrive as `apply_patch` with the patch text in `tool_input.command`.
- **Done.** `hooks/hooks.json` names `apply_patch` in the PreToolUse matcher; `hooks/guard.sh`
  checks every path the patch adds, updates, deletes or moves to, through the same `check_one` the
  Edit branch now uses, and never parses the patch as a shell command. Twelve `GUARD_SHAPES` rows in
  `test/validate.py`. Board: AS-23 and AS-24 closed with the PR #35 receipt (`8c93030`, v1.22.2).
- **Checks run.** The repository gate (`npm test`) on the candidate tree, and the fixtures against
  1.23.2's `guard.sh` (eight of twelve wrong there). PR, CI run, merge commit and release run: in
  the pull request for this change.
- **Next task.** After the release: the sshlg-skills pin bump to 1.23.3 (hub B-211 close); then the
  highest open board row, AS-28 (re-measure the trigger evals).

## v1.23.2: descriptions within a 300-character budget (2026-10-25)

- **Done.** Every skill description in this member is at or under 300 characters (hub measure:
  whitespace collapsed, front matter only), per hub board row B-255 (a); the words a description
  dropped live in its `## Triggers`. Before/after figures are in CHANGELOG.md. Checked: the
  repository gate, and in the sshlg-skills worktree `node test/route_coverage.js` (125 named, 0
  spoke where silence was right, unchanged), `node test/triggers_test.js` (70 OK) and
  `node test/advertised_check.js` for this member.
- **Next task.** After merge: tag `v1.23.2`, watch the release, then the sshlg-skills pin bump; then
  the highest open board row.

## v1.23.1: the family member gate (2026-10-24)

- **Done.** the family member gate: the family member gate (`test/family_gate.py`, with its shared-mechanism
  declaration) and the documentation merged since v1.23.0, released.
- **Next task.** After merge: tag `v1.23.1`, watch the release, then the sshlg-skills pin bump; then
  the highest open board row.

# Start here: agent-sync

This page holds the current entry and the next bounded task. Open work lives in
[the backlog](evidence/backlog.md); release receipts in [evidence/releases](evidence/releases/).

## v1.23.0 released and pinned (2026-10-22)

- **State.** agent-sync 1.23.0 is tagged, on npm and pinned by sshlg-skills; installed on the
  operator machine for Claude Code and Codex. Until today the next task lived only in a release
  receipt (`evidence/releases/2026-10-20-quiet-start/README.md`); this page is now its home.
- **Next task.** AS-23 and AS-24 read "verified candidate; merge pending" while no pull request
  is open: find where their candidates went, then land them or reopen the rows with the reason.
  After that, AS-28: re-measure the trigger evals for the 1.23.0 description
  (`test/evals/RESULTS.md` measured an older one).
