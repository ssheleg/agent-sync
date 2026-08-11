# Working the roadmap: claims, closing, re-planning

**Read this when** configuring `claimTags`, taking or releasing a task, closing one, or
re-planning work that is already on a board.

## Contents

- [The two records, and why both exist](#the-two-records-and-why-both-exist)
- [How the write is made safe](#how-the-write-is-made-safe)
- [Configuring it](#configuring-it)
- [Closing a task](#closing-a-task)
- [Re-planning](#re-planning)
- [When the claim cannot be written](#when-the-claim-cannot-be-written)


The roadmap is where a project says *what is being done and by whom*. With several
agents it is also the file most likely to be written by two of them at once, which is why
it is guarded — and why the tool touches it as narrowly as it possibly can.

## The two records, and why both exist

| Record | Says | Lives | Lifetime |
|---|---|---|---|
| **Lease** | who holds this task *this minute* | the lease backend | expires by TTL |
| **Claim tag** | who *owns* this task | the roadmap, in git | until released or done |

A lease without a claim tag is invisible to anyone reading the repository. A claim tag
without a lease is a name that never expires — the stale `[Backend]` that blocks a task
for a week because someone's session died. **Both, or neither.**

`acquire` writes the tag through; `release` restores exactly what was there. You do not
write it by hand, and you should not: an editor rewriting a shared registry is the
collision the lease exists to prevent.

## How the write is made safe

The roadmap is a shared registry, so the edit is deliberately the smallest one possible:

1. **One row.** The tool finds the *single* markdown table row containing the task id as
   a whole word. **Zero rows → nothing happens. Two or more → it refuses and says so.**
   It never guesses which row you meant.
2. **One cell.** Only the configured cell of that row changes. Everything else on the
   line — links, notes, other columns — is untouched, byte for byte.
3. **Reversible.** The previous cell text is stored in `.agent-sync/claims.json` and
   restored verbatim on release. Not a default, not a guess: what was actually there.
4. **Atomic.** Written to a temporary file and moved into place, so a crash mid-write
   cannot leave the register half-edited.

After `acquire` then `release`, `git diff` on the roadmap is **empty**. If it is not,
that is a bug, not a convention.

## Configuring it

```json
"claimTags": {
  "docs/WORKSTREAMS.md":     { "mode": "cell", "cell": 2,  "held": "{prev} · claimed {holder}" },
  "apps/*/docs/ROADMAP.md":  { "mode": "cell", "cell": -1, "held": "{prev} (claimed: {holder})" }
}
```

- `cell` is **0-based** over the row's cells. `-1` is the last cell, `-2` the one before
  it. Getting this wrong writes the claim into the wrong column and looks like it worked —
  check the result of your first `acquire` against the file before trusting the mapping.
- `held` is a template. `{prev}` is the cell's current text, `{holder}` the run id.
  Keeping `{prev}` is what makes `todo` become `todo (claimed: r-x)` rather than losing
  the status.
- A pattern may cover many files (`apps/*/docs/ROADMAP.md`); each is searched
  independently, and a file with no matching row is simply skipped.

`check` validates that every pattern matches a real file and that the cell exists.

## Closing a task

Closing is **not** the same as releasing, and the tool does not do it for you.

```
release <KEY>    → the claim is removed; the row returns to what it said before
```

That is the right behaviour when you stop working. **Closing** means the row's status
becomes `done` — a statement about the work, not about who was holding it, and one only
you can make. Do it as part of the same change that lands the work:

1. `record` what you actually built, with the decision id and the files;
2. edit the row's status to `done` yourself — you hold the lease, so the guard allows it;
3. `release` the lease;
4. `reconcile`, then `board`.

The tool refuses to write `done` on your behalf for the same reason it refuses to guess a
row: a status that a machine sets is a status nobody checked.

## Re-planning

New tasks, split tasks, re-ordered phases — all of it is an ordinary guarded edit:

- **Take a lease on the board itself** before restructuring it. Use the direction or
  board id as the key (`WS-9`), not a task id, so the lease says what it means.
- **Reserve every new id** with `reserve` before writing it. Re-planning is exactly when
  several agents mint ids at once, and reading a *next free id* line is not reserving it.
- **A task that moves keeps its id.** Ids are stable; renumbering breaks every reference
  in every other document and in the as-built record.
- **Record the re-plan as a decision** if it changes scope. A board that quietly grew a
  phase is a scope change nobody agreed to.

## When the claim cannot be written

If `acquire` prints nothing about the claim, no row matched — the task id is not on the
board, or not in a table row. That is worth noticing rather than ignoring: it usually
means the key you leased is not the key the board uses.

If it prints *refusing to guess which one*, two rows carry the id. Fix the board or
narrow the pattern; do not work around it by editing by hand, because the next agent's
lease will hit the same ambiguity.
