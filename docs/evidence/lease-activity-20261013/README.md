# FW04: bounded lease activity and PR handoff

Status: implementation and focused checks complete; full gate and independent
acceptance pending. No installed/native or remote-provider acceptance claimed.
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

## Observed reproduction and focused verification

Against unchanged `2f149b1`, `python3 test/lease_activity_test.py` ran seven real
CLI scenarios with a fake clock: five failed and two passed. Stale heartbeat,
unguarded tools, unrelated TASK-B work, missing legacy activity and releasing
TASK-A's resource while TASK-B remained reproduced the problem. Active guarded
work and explicit named keepalive were positive controls. The test wrapper uses
legacy `renew` on an old script, so baseline failures prove behavior rather than
an unknown new CLI option.

After the initial correction, all **14 focused tests passed**, including the real hook across
two registered repositories, malformed metadata, old lock contention, expired
claim refusal, ambiguous tasks, partial resource-release failure and retry, and
the `signal --help` contract. Existing `test/coordination_test.py`: **52 cases,
exit 0**. The owner structural validator also passed. The harness enters both
local and hosted gates through the existing `audit_regressions/*.py` discovery;
the test owns and removes its HOME and repository fixtures.

House skill audit: **0 GAP, 19 PASS**, description 936/970 characters, body 4553
tokens with the available tiktoken counter. Repeated explanatory prose was
shortened while adding the PR handoff and signal boundary; descriptions and
skill identity are unchanged.

## Lifetime and compatibility boundary

The activity window is **two renewal intervals** (600 seconds at the default
300-second interval). It controls eligibility for automatic renewal only.
The lease's configured TTL (default 2700 seconds) still decides exclusion from
its last successful renewal. Inactivity does not immediately release or shorten
a lease; it stops future automatic extensions. Publish and explicitly release
for a prompt handoff. No expired lease is resurrected by a guard or heartbeat.

Manual `renew <KEY>` remains an intentional keepalive. Legacy holdings without
activity are not automatically extended. A fresh guard or explicit renewal
establishes activity. Existing key/ref/payload/generation rules are unchanged;
old installed heartbeats keep their old behavior until upgraded. This change
does not claim to fix mixed-version PATH alias races (AS-14) or unrelated vault
and signal-delivery findings.

## Candidate CI and next task

The owner workflow now uses the already-approved family policy: 23:00
Europe/Warsaw schedule, `workflow_call`, explicit diagnostics with required
`candidate_reason`, and PR `ready_for_review` only. Local parsing verifies the
four events and unchanged job bodies. No push/opened/synchronize trigger exists.
Only a frozen, locally green and independently reviewed draft is promoted once.
Diagnostic dispatch does not satisfy required PR checks. Release callers and
tag workflows are unchanged; no hosted run or package release has occurred here.

## Independent-review follow-up

The first `npm test` run passed **20 suites**, including the initial 14 new
controls; the real HOME's 50 agent-sync files were unchanged. This result applies
to `162d483`, before the following review corrections, and is not the final gate.

Self-review reproduced a held resource allowing a guarded write after all task
leases expired (CLI exit 0 where exit 2 was required). The guard now refuses
until the current task is explicitly reacquired, retaining the resource claim
and refusing the competing writer. Independent review also reproduced a read-only
activity directory interrupting a confirmed resource release with PermissionError.
That cleanup is now best-effort with a bounded warning; stale scheduling metadata
cannot renew an absent lease and the unrelated live task is preserved.

A local bare-remote fixture additionally checks git-plane handoff, live contention,
inactive expiry/takeover and confirmed ref removal. Expired foreign/own refs are
not silently reaped: the fixture explicitly releases its active claims, leaving
the existing AS-11 expired-ref lifecycle outside this change.

The corrected focused suite passed **17/17**, exit 0. Independent review then
combined read-only directories with read-only marker files and reproduced a
same-run release/reacquire retaining an old task association after reset failed.
The new fixed-clock (same-second) regression first failed with an allowed guard.
Guards now require successful fresh activity/binding persistence; failure refuses
admission while retaining the claim for repair/retry. This uses no new lease
epoch or wire fields. Release cleanup remains best-effort after confirmed release.

The expanded focused suite passed **18/18**, exit 0. Next: complete owner full gates after review,
push the final SHA, then promote draft [PR 35](https://github.com/ssheleg/agent-sync/pull/35)
once for the eligible candidate check. No final independent approval is implied
by recording a review finding here.
