# Branch discipline, merging, and the merge log

**Read this when** starting work that will produce commits, merging a branch, or deciding
where a claim should be written.

## The rule

> **Work happens on a branch. The integration branch is somebody else's stable base.**
> Nothing about work in flight is committed there — the holder lives in the coordination
> plane until the work lands.

Three failures make this a rule rather than a preference, and all three happened here:

| What breaks | Why |
|---|---|
| Two agents edit the roadmap to mark a claim | The one file that exists to prevent collisions becomes the file every branch touches, so every merge conflicts on it |
| A claim committed to a branch tells nobody | It is invisible on the integration branch until the merge — exactly when it stops being useful |
| Two agents commit straight to the integration branch | The second push is rejected, the work is already duplicated, and the repository has two versions of one change |

## Where a claim lives

`acquire` writes the claim through to the roadmap **only when the run is on the
integration branch**. On any other branch it says so and writes nothing:

```
claim for `ASC-072` left in the coordination plane — this run is on 'feature/x',
not main. `status` shows the holder to every agent; `merge` records the outcome.
```

That is not a downgrade. The plane is where the claim is *more* visible: `status` lists
every other run's live holdings, from any checkout and any repository, and neither agent
has to fetch the other's branch to see it. Git keeps what must survive — the merge, and
the log entry naming it.

The integration branch is `integrationBranch` in the config, or the repository's own
default branch when that is unset. It is asked of the repository, never assumed.

## Landing the work

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" merge --key ASC-072 --summary "what landed"
```

In order, and every check before anything is touched:

1. **Refuses a detached HEAD, the integration branch itself, and a dirty tree.** A merge
   cannot tell uncommitted work from the branch's own commits.
2. **Fetches the integration branch and fast-forwards the local one to it**, so the
   preflight and the merge share a base, then reports how far it moved since the branch
   started. Until 1.6.0 they did not share one: conflicts and the diff were measured
   against `origin/<target>` while the merge went into a local `<target>` nothing
   advanced — so `merge` printed the staleness it had just measured, printed `✓ merged`,
   wrote the log entry and released the lease, and the push was rejected. The work had not
   landed, the log said it had, and the task was free for somebody else. A local branch
   that has *diverged* (commits on both sides) cannot be fast-forwarded and is refused
   with both counts.
3. **Computes conflicts with `git merge-tree`**, in memory. A merge that starts and then
   aborts leaves the operator in a repository they did not ask for. On conflict it names
   the files, changes nothing, and exits non-zero: resolve in your own branch, where the
   resolution is reviewable, and run it again.
4. **Lists every other run's live lease.** Their work is not in this diff; if it touches
   the same files, they merge into what is about to land.
5. Merges `--no-ff`, so the branch stays visible in history.
6. **Writes the merge log** and commits it.
7. **Releases the lease named by `--key`.** Only that one: releasing every lease the run
   holds is a different statement from the one this step makes, and it quietly frees work
   that has not landed. Without `--key` there is nothing to name, so it releases what the
   run holds and says so. A run that ends holding a lease blocks the next agent for the
   whole TTL — so release, but release what you landed.

The remote is `origin`, the conventional home of the integration branch. `leaseRemote` is a
different setting for a different job: where the lease refs live.

`--dry-run` stops after step 4. `--push` pushes the integration branch afterwards;
without it, `finish` is the next call — it checks every repository, not just this one.

## The merge log

`docs/MERGES.md` by default, `mergeLog.file` to move it, `mergeLog.retentionDays` to
change the window (7 days).

It answers one question an agent returning from a branch cannot answer from git alone
without reading every commit: **what landed while I was away, and was any of it near my
work.** `git log` has the same facts and none of the summary; a changelog has the summary
and only for released work.

```markdown
### 2026-07-30T16:42:11Z · `ASC-072` · feature/rename → main · `ec414cd`
- run: r-4f9c2
- files: 8 (8 files changed, 120 insertions(+), 31 deletions(-))
- conflicts: none
- summary: every address moved to the new owner

## Compacted

- 2026-07-21 · `ASC-060` · fix/lease-visibility → main · `9817f19` · the guard denied the holder
```

**Compaction happens on write.** Every `merge` re-reads the file, keeps entries inside the
window in full, and folds everything older into one line each under `## Compacted`. No
cron, no second command, and no log that grows until people stop reading it — which is the
same as not having one.

Read it at the start of a task:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" merges           # recent detail
python3 "$SKILL_DIR/scripts/agent_sync.py" merges --all     # including the compacted tail
python3 "$SKILL_DIR/scripts/agent_sync.py" merges --compact # force the pass now
```

## What this does not do

- **No pull request.** This is the flow for agents that share one repository and merge
  their own work. A project that reviews through pull requests keeps doing that; `merge`
  is then the wrong command, and `finish` is still the right one.
- **It does not make a branch safe to skip a lease.** Two agents on two branches editing
  one file still collide — later, at merge time, with more work already spent. `acquire`
  first; the branch is where the work goes, not what decides who does it.
- **It does not rebase for you.** A conflicting merge is refused with the file list; the
  resolution belongs in the branch, in a commit somebody can read.
