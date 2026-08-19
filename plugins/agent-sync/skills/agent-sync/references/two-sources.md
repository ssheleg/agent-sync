# Two documentation sources, and the duty to reconcile them

**Read this when** starting a task, finishing one, or deciding where a piece of
documentation belongs.

## Contents

- [They answer different questions](#they-answer-different-questions)
- [The duty, both ends of a task](#the-duty-both-ends-of-a-task)
- [What `reconcile` decides, and what it refuses to](#what-reconcile-decides-and-what-it-refuses-to)
- [The baseline — why the check is a ratchet](#the-baseline--why-the-check-is-a-ratchet)
- [Where a piece of documentation belongs](#where-a-piece-of-documentation-belongs)
- [Lifetime, and why nothing is deleted](#lifetime-and-why-nothing-is-deleted)


## They answer different questions

| Source | Answers | Written | Authority over |
|---|---|---|---|
| **Git docs** (`docs/`, ADRs, specs, contracts) | *How it should be* | before the code, often without it | intent, design, scope, decisions |
| **The coordination plane** (`70 As-built`) | *How it actually is* | from what agents really wrote | the record of what was built, by whom, at which commit |

Neither is a copy of the other and neither outranks the other, because neither is
answering the other's question. A spec that describes an unbuilt thing is not wrong —
it is intent. An as-built record that contradicts the spec is not wrong either — it is
what happened.

**The gap between them is the finding.** It is drift between plan and reality, and
surfacing it is the whole point. A system where the two can never disagree has simply
hidden the disagreement.

This is why the as-built record does **not** violate a single-source-of-truth rule:
there is no second home for one fact, there are two facts. What would violate it is
copying a decision's *body* into the cloud and editing it there.

## The duty, both ends of a task

**Before starting** — during the pipeline's docs-study stage:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" reconcile
```

Read the git documents for the area you are about to touch **and** the as-built
record for it. Then resolve every divergence one of three ways:

1. the git document is stale → fix it, or record why it stands;
2. the as-built record is wrong or incomplete → correct it with `record`;
3. they genuinely disagree and the disagreement is real → that is a decision to make,
   not a discrepancy to paper over. Raise it before you write code against either.

Starting work on top of an unresolved divergence means building against a document
that describes a system that does not exist.

**After finishing** — during the pipeline's docs stage:

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" record "what you actually built" --decision DEC-0216 --files a.py,b.ts
python3 "$SKILL_DIR/scripts/agent_sync.py" reconcile
```

Update **both** sides in the same change: the git documents that state intent, and the
as-built record of what landed. Then run the check again. A task that updated only one
side has left the next agent a divergence to discover the hard way.

## What `reconcile` decides, and what it refuses to

It is mechanical, and it says so in its own output. It finds:

- an as-built entry whose commit is **not in this repository's history** — recorded
  from a branch that never landed, or from another repo;
- an id written in a register **after the baseline** with no as-built record — decided
  since adoption, and nothing reports it was built;
- an as-built entry citing an id that **exists in no register** — built against
  something never written down.

It does **not** judge whether the built thing matches what the document describes.
That is a reading, not a diff. The tool points at where to look and refuses to imply
it checked the substance.

## The baseline — why the check is a ratchet

A project adopting this on day one has every prior decision unrecorded. A check that
reports all of them reports nothing: it is noise, and noise is what gets a gate
switched off.

```bash
python3 "$SKILL_DIR/scripts/agent_sync.py" reconcile --set-baseline
```

Run once per project. Ids at or below the baseline become a **backlog** — counted,
visible, allowed only to shrink. Ids after it must carry an as-built record or the
check fails. This is the same shape as a well-behaved lint ratchet, and for the same
reason: a gate that fails on history is a gate nobody keeps.

## Where a piece of documentation belongs

| It is… | Home |
|---|---|
| a decision about what to build | git — the decision register |
| a contract, schema, spec | git |
| user-facing behaviour | git |
| "this is what I implemented, here is the commit" | the as-built record |
| "the implementation diverged from the spec, here is why" | **both** — as-built for the fact, git for the decision |
| who is doing what right now | the claims log — ephemeral, never git |

When in doubt: if it must survive the tool being uninstalled, it goes in git.

## Lifetime, and why nothing is deleted

| Information | Home | Lifetime |
|---|---|---|
| Decisions, specs, contracts, user-facing behaviour | git | permanent, append-only register |
| What was actually built, with its commit | as-built log | permanent, append-only |
| Cross-repo dependency state | signal log | permanent, append-only |
| Who holds a task right now | claims log | expires by TTL |
| Per-run narrative | that run's journal | permanent |
| The board, the repo page, the setup snapshot | generated | replaced on every regeneration |

**No log entry is ever edited or deleted.** The logs are replayed in order to decide who
holds what and which id was allocated, so removing a line silently rewrites a conclusion
every other agent has already acted on. Correct by **appending**:

- a lease is **released**, never removed;
- a reserved id you did not use is returned with `release-id`, which appends;
- a wrong as-built entry is superseded by a later, correct one, and both stay visible —
  the history of what was believed is itself worth keeping.

Generated pages are the exception, and a narrow one: they are rewritten wholesale, and a
page whose first line has lost its `agent-sync:generated` marker is **refused** rather
than overwritten, because a human took it over.

**The marker itself.** The board and the mirror are machine-written, and their first line is

```
<!-- agent-sync:generated source=<repo>@<sha> at=<iso8601> — edit in git, not here -->
```

A write to an object missing it is refused, not forced; report the takeover and stop. The
mirror is a **rendering** of git stamped with the source commit, and has no authority — when
its stamp and `HEAD` disagree the board gate fails, and that is drift, not formatting.

**Growth.** These logs are small — one line per event — so rotation is not urgent. When a
log does need trimming, archive the whole document and start a fresh one with a `base`
line carrying the current allocation state. Never delete lines from a live log to shrink
it: replay would then produce a different answer than it did yesterday.

**Removing the tool.** Everything durable is already in git. Delete `.agent-sync/`, the
config and the env file; the knowledge-base pages can be kept as a record or archived.
Nothing in the repository depends on the tool being installed, which is the property that
makes adopting it reversible.
