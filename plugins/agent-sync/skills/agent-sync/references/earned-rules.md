# Two rules this plugin exists to enforce, and the failures that taught them

Both were found by running this plugin on a four-repository project with several agents working at
once. Each is now code, not advice — and each is stated here because a mechanism nobody can explain
is a mechanism the next person removes.

**Identity comes before coordination.** A lease is only a lease if two agents get two identities.
Both ends of getting this wrong have happened: deriving the id from `CLAUDE_SESSION_ID` alone gave
**one session two identities** — it acquired as one and was denied by its own guard as the other —
and keeping one id per checkout gave **two sessions one identity**, which is worse because it is
silent. Both acquired as one run, both were guarded as one run, `whoami` reported a lease that
belonged to somebody else, and `release` would have taken it. The resolution order is
`AGENT_SYNC_RUN_ID` · `CLAUDE_SESSION_ID` · the session that started this shell · shared, and where
none can be established the run says so instead of presenting a shared entry as separation.

The third position is the one that needs explaining, because the obvious implementation of it is
wrong. A plain shell command carries no session id; a hook does. So `SessionStart` stamps
`.agent-sync/sessions/<CLI pid>` with the session it knows, and any later command finds itself by
walking its own **process ancestry** to a stamped pid, removing stamps whose process is gone. It is
deliberately not command-line parsing: the throwaway shell every tool call runs in carries claude
paths in its own `argv`, so every heuristic aimed at spotting the CLI binary matched that shell
instead — and matching the wrong ancestor hands two sessions one identity, which is the failure
above.

**Work in a submodule is not finished until its parent says so.** A parent records each submodule as
a pointer to one commit, and moving the submodule does not move the pointer. The work is committed,
pushed, green in CI and marked done in its own roadmap — and a clone of the parent gets the commit
**before** it. Neither repository looks wrong on its own; the disagreement exists only between them,
which is why it survives every check that runs inside one. `finish` is that check, and it is the
reason this plugin's pipeline binding no longer describes gate expressions it never ran.

**The rule under both:** before trusting a tool's report about the world, make it report something
you can already verify. A test suite reporting green having skipped every assertion, a gate printing
`FAIL` and exiting `0`, containers reporting healthy while the tools talked to the host's services,
and a lease held by the wrong identity are the same failure — a tool describing a world it is not
looking at.
