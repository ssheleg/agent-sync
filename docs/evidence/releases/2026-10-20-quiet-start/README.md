# agent-sync 1.23.0 release packet and handoff

Objective (operator task, 2026-10-20, relayed by the hub coordinator): make the
SessionStart output short and only actionable, bound the PreToolUse guard's slow path to
about 2 s, and trim the skill description to a router of at most 450 characters. Then
release. The hub session re-pins the submodule; this repository does not.

Branch `feat/quiet-session-start`, cut from `origin/main` at `5bd8bec`. Board rows
AS-25, AS-26, AS-27 (closed by this release), AS-28 and AS-29 (opened by it), in
[the backlog](../../backlog.md). Evidence rows: [verification ledger](../../verification.md),
section v1.23.0.

## What changed

1. `hooks/session-start.sh` stamps the session on startup, resume and clear (the matcher
   is unchanged: dropping `resume|clear` would bring back ASY-08, a cleared session
   inheriting the ended run's identity), and prints only on `startup`: `status --brief`,
   one line or nothing. `AGENT_SYNC_SESSION_VERBOSE=1` prints the full `status`.
2. `status --brief` (new flag) reads local state only and never moves the signal
   watermark. It names the leases this run holds, a lease held past the scheduled
   interval, this run's expired locks with `reap`, and claim tags out of step.
3. `Sync.guard` takes a file claim that needs a remote in a forked, detached process and
   waits for it until `AGENT_SYNC_GUARD_BUDGET` (1.5 s; `0` = synchronous, as before).
   Records under `.agent-sync/claiming/`. The acquire `push` is bounded at 60 s.
4. SKILL.md description 936 to 445 characters, all 14 hub triggers kept; the "before a
   shared registry, even unasked" rule moved into the body.

## Decisions taken without the operator (recorded per the standing instruction)

- **The matcher stays `startup|resume|clear`.** The task said "startup only"; its purpose
  (no repeated context on resume and clear) is met by printing nothing there, while the
  stamp ASY-08 needs keeps running. Reversible: one `case` line in the hook.
- **Nothing is printed when nothing is actionable**, rather than a "no leases held" line.
- **Another run's residue and holdings are not in the brief.** `acquire` refuses a held
  key, and `status` still lists both.
- **Past the budget the write is allowed**, as the host already did after its 20 s
  timeout. A foreign claim is refused from the next write (AS-29 tracks the window).
- **Trigger evals not re-measured** for the new description (AS-28); the hub's
  advertised-trigger check passes.

## Checks actually run

See the ledger section for numbers. Local: `npm test` (all suites), `npm run
test:negatives`, hub `advertised_check.js`, A/B timing of both hooks against the hub
checkout and against github.com. Hosted CI and the release workflow are recorded below
once they run.

## Release state

Filled in by the release run: PR, merge commit, tag, release run, npm version.

## Next task for whoever picks this up

1. Hub session: pin `skills/agent-sync` to the v1.23.0 tag, bump `skills.json`, release
   the hub, then `npx --yes sshlg-skills@latest update` on this machine.
2. AS-28: re-run the trigger evals for the 1.23.0 description on haiku and sonnet.
