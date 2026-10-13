# FW04: bounded lease activity and PR handoff

Status: source reproduction and implementation plan, not an acceptance receipt.
Base: `2f149b1fe9a071a9462696851b7e8d33698fd3f8`; source findings are
AS-23 and AS-24 in [the owner board](../backlog.md).

## Objective and contract

A PostToolUse heartbeat must not keep renewing claims for unrelated work after
an agent has handed off a pushed PR. Explicit release remains the primary
handoff; inactivity is a bounded fallback, not proof that an unfinished edit is
safe to take over. Every resumed write still needs the normal lease/guard check.

Preserve lease names, payloads, TTL/CAS arbitration and explicit manual renewal.
Add an activity-only heartbeat mode, propagated to every session repository.
Successful acquisition, intentional renewal and an allowed guarded-file check
are activity. Unrelated tools and heartbeat renewal are not activity. Activity
is per run and key, stored locally, with a window of two renewal intervals.
Legacy holdings without an activity record need fresh guarded activity or an
explicit renewal; do not reinterpret unknown activity as recent work.

A resource guard must not mark every task held by the session active. Associate
the resource with an unambiguous task when possible; ambiguous existing task
sets require explicit named renewal or release of stale claims. Preserve the
existing admission rule rather than silently select another task's ownership.

In a PR flow, push the implementation and durable handoff, then release. A
merge-only lease must have a watcher releasing it within two minutes of merge.
`signal` records dependency state; it does not transmit a cross-project ask.
Where Fabric serves the project, the ask goes through `com.submit` and its
board message is referenced by the dependency record.

## Execution and acceptance

1. Reproduce stale automatic renewal with a fake clock and a real CLI fixture.
2. Add the bounded activity mode and local-only metadata with failure-safe
   behavior; retain explicit renewal and competing-writer arbitration.
3. Test active guarded work, unrelated calls, task/resource separation, a
   second repository, legacy metadata, explicit release/reacquire and takeover.
4. Update hook/lease/branch doctrine and the signal boundary in the same change.
5. Run focused tests, owner gates and independent review at a frozen SHA; push
   the task branch. Hosted admission, merge and package release remain distinct.

## Ownership and boundaries

Worktree `agent-sync-leases`, branch `fix/lease-activity-1013`. The owner checkout
and foreign expired leases remain untouched. Local advisory arbitration is
reported as such. No vault, Notion, Outline, provider or real-HOME acceptance is
claimed; fixtures must own and clean their temporary directories.

Skills: task-pipeline for the plan and delivery, agent-sync for ownership,
make-skill for construction limits, evidence-docs for bounded receipts.

Next task: run the failing reproduction before changing the implementation.
