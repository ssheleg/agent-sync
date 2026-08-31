# Evaluation results

**Status: executed 2026-08-31 against two models (haiku, sonnet), one blind
fresh-context probe per trigger query per model, plus n=3 on the one failing
query; all three scenarios driven end to end in scratch repositories.**

CI still proves only that the files are shaped correctly. The rows below are
the dated runs; the Method section states exactly what was measured and what
this harness cannot measure.

| Date | Version | Model | Trigger pass rate (train / validation) | Scenario lines passed | Installed alongside | Notes |
|---|---|---|---|---|---|---|
| 2026-08-31 | 1.18.7 | haiku (Claude Code Agent-tool alias) | 6/6 / 5/6 | s01 1/4 · s02 1/4 · s03 3/4 | agent-stack 0.17.0, agent-sync 1.18.6, make-skill 0.25.1, seo-aeo-audit 0.25.7, sheleg-design 1.58.1, sheleg-dev 0.11.0, super-ux 0.50.0, task-pipeline 1.79.1, telegram-dev 0.1.9 | q06 missed 3/3 samples (answered `none` on all three); never invoked the shipped CLI in s01, hand-rolled its own lease format |
| 2026-08-31 | 1.18.7 | sonnet (Claude Code Agent-tool alias) | 6/6 / 5/6 | s01 2/4 (2 not reached, see Method) · s02 3/4 · s03 4/4 | agent-stack 0.17.0, agent-sync 1.18.6, make-skill 0.25.1, seo-aeo-audit 0.25.7, sheleg-design 1.58.1, sheleg-dev 0.11.0, super-ux 0.50.0, task-pipeline 1.79.1, telegram-dev 0.1.9 | q06 missed 3/3 samples (answered `task-pipeline` all three); s01 stopped at the skill's own operator gate — conformant, but the scored lines behind it were never exhibited |

## Per-query trigger results (both models)

| Query | Expected | haiku | sonnet |
|---|---|---|---|
| q01 leases for three agents | trigger | pass (agent-sync) | pass (agent-sync) |
| q02 claims и TTL (ru) | trigger | pass (agent-sync) | pass (agent-sync) |
| q03 race-free decision ids | trigger | pass (agent-sync) | pass (agent-sync) |
| q04 adopt agent-sync | trigger | pass (agent-sync) | pass (agent-sync) |
| q05 кто держит файл / release lease (ru) | trigger | pass (agent-sync) | pass (agent-sync) |
| q06 finish a submodule change | trigger | **fail, 0/3 samples** | **fail, 0/3 samples (task-pipeline)** |
| q07 working alone, normal branch | no trigger | pass | pass |
| q08 полный цикл разработки (ru) | no trigger | pass (task-pipeline) | pass (task-pipeline) |
| q09 orchestrator for support agents | no trigger | pass (agent-orchestrator) | pass (agent-orchestrator) |
| q10 explain optimistic locking | no trigger | pass (none) | pass (none) |
| q11 GitHub Actions status (ru) | no trigger | pass (none) | pass (none) |
| q12 mutex around in-process cache | no trigger | pass | pass |

The one stable failure is a positive: **q06** carries no coordination
vocabulary, and the description's trigger list has no submodule / parent-
pointer literal to catch it — both models route it elsewhere on every sample.
Filed on the board as ASY-10; fixing it is a description change, which is a
minor release, not this patch.

## Scenario line results (2026-08-31)

Executed as real agent sessions in scratch repositories under
`/tmp/asy-evals-2026-08-31/` (fixtures: s01 bare roadmap repo with no config;
s02 fs-backend config with a `D` register seeded to `D-0004`; s03 parent repo
whose submodule pointer trailed the member's pushed `main` by one commit).
End states verified on disk, not only from the probe's report.

| Scenario | Line | haiku | sonnet |
|---|---|---|---|
| s01 | Checks for project configuration before claiming enforcement | fail — invented its own config format without checking the tool's | pass — found no config, ran read-only `adopt`, wrote nothing |
| s01 | Uses distinct session identities | fail | not reached (stopped at the skill's operator gate: backend must be asked, not guessed) |
| s01 | Acquires a scoped lease before the guarded edit | fail — documented a hand-rolled procedure, acquired nothing | not reached (same gate; the acquire/guard/release sequence was prescribed verbatim) |
| s01 | States TTL and the release path, including failure | pass (TTL + release stated, hand-rolled) | pass (2700s TTL; "release on every path, including failure") |
| s02 | Uses the reservation mechanism instead of local max-plus-one | partial — invoked `reserve D`, honored the fs refusal, then allocated manually WITHOUT a lease | pass — invoked `reserve D`, honored the fs refusal, allocated inside the O_EXCL lease per the tool's own remedy; two real processes contended and serialized |
| s02 | Journals the run that reserved the id | fail (run-id stamped, no journal entry) | fail (no `record` entry written) |
| s02 | Reads back the durable append before acting on it | fail | pass (read the register inside the critical section, verified the final state after release) |
| s02 | Does not put credentials in argv or logs | pass (no credentials exist under fs) | pass (no credentials exist under fs) |
| s03 | Identifies the parent pointer as unfinished work | pass | pass |
| s03 | Verifies the member commit is reachable upstream | pass (fetched it from the member remote) | pass (checked the bare remote's `main` explicitly) |
| s03 | Updates and verifies the parent pointer | pass (pushed, re-checked status) | pass (verified against the bare remote) |
| s03 | Releases the held claim and reports residue | fail — never touched the coordination plane; did report "nothing left behind" | pass — read the config, stated why no lease was required (empty `guardedFiles`), reported residue explicitly |

## Method (2026-08-31 run)

- **Trigger probes**: one fresh, context-free subagent per query per model via
  the Claude Code Agent tool (`general-purpose`, `model: haiku` / `sonnet`).
  The whole prompt was: the query verbatim, the installed family skills as a
  name-plus-description list built from the installed plugins' `SKILL.md`
  front matter, and "Which ONE skill would you invoke, or none? Answer with
  the name only. Do not use any tools." Pass for a positive = the probe named
  `agent-sync`; pass for a negative = it named anything else or `none`.
- **Sampling**: n=1 per query per model (the README asks for three; cost was
  traded for coverage of two models), except q06 — the only miss — which was
  re-sampled to n=3 per model and missed every time.
- **Scenarios**: one fresh `general-purpose` subagent per scenario per model,
  prompt = the scenario query verbatim plus the scratch repository path and a
  demand to report every command. Lines scored from the report AND the
  repository end state on disk.
- **Known limits, stated rather than hidden**: probes run on this operator's
  machine, so their context also carried the machine's global CLAUDE.md
  routing doctrine and the full installed-skill listing — blind to this
  conversation, not blind to the operator's routing rules. Scenario s02's
  committed fixture uses the fs backend, whose `reserve` refuses by design
  (`atomicAppend` false), so the "reservation mechanism" line is only fully
  satisfiable through the lease-guarded manual path the tool itself
  prescribes. Model ids are the Agent tool's aliases, not pinned snapshot
  ids — the harness does not expose the resolved snapshot. s01 (sonnet)
  stopped where the skill demands an operator decision (backend choice); the
  two lines behind that gate were prescribed correctly but not exhibited, and
  are recorded as not reached rather than passed.
