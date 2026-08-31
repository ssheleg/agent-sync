# Evaluation results

**Status: executed twice on 2026-08-31. The v1.18.7 run measured twelve trigger
queries against two models (haiku, sonnet) and drove all three scenarios end to
end in scratch repositories. The v1.19.0 run re-measured every trigger query
after the ASY-10 description fix, with q06 — the only failure of the first run —
sampled n=3 per model on BOTH the old and the new description.**

CI still proves only that the files are shaped correctly. The rows below are
the dated runs; the Method section states exactly what was measured and what
this harness cannot measure.

| Date | Version | Model | Trigger pass rate (train / validation) | Scenario lines passed | Installed alongside | Notes |
|---|---|---|---|---|---|---|
| 2026-08-31 | 1.18.7 | haiku (Claude Code Agent-tool alias) | 6/6 / 5/6 | s01 1/4 · s02 1/4 · s03 3/4 | agent-stack 0.17.0, agent-sync 1.18.6, make-skill 0.25.1, seo-aeo-audit 0.25.7, sheleg-design 1.58.1, sheleg-dev 0.11.0, super-ux 0.50.0, task-pipeline 1.79.1, telegram-dev 0.1.9 | q06 missed 3/3 samples (answered `none` on all three); never invoked the shipped CLI in s01, hand-rolled its own lease format |
| 2026-08-31 | 1.18.7 | sonnet (Claude Code Agent-tool alias) | 6/6 / 5/6 | s01 2/4 (2 not reached, see Method) · s02 3/4 · s03 4/4 | agent-stack 0.17.0, agent-sync 1.18.6, make-skill 0.25.1, seo-aeo-audit 0.25.7, sheleg-design 1.58.1, sheleg-dev 0.11.0, super-ux 0.50.0, task-pipeline 1.79.1, telegram-dev 0.1.9 | q06 missed 3/3 samples (answered `task-pipeline` all three); s01 stopped at the skill's own operator gate — conformant, but the scored lines behind it were never exhibited |
| 2026-08-31 | 1.19.0 | haiku (Claude Code Agent-tool alias) | 6/6 / 6/6 | not re-run (see Method) | agent-stack 0.18.1, agent-sync 1.18.7, make-skill 0.25.3, seo-aeo-audit 0.25.8, sheleg-design 1.58.2, sheleg-dev 0.11.2, super-ux 0.52.3, task-pipeline 1.80.0, telegram-dev 0.1.10 | ASY-10 re-measurement: q06 0/3 on the old description, 3/3 on the new one; the other eleven queries unchanged |
| 2026-08-31 | 1.19.0 | sonnet (Claude Code Agent-tool alias) | 6/6 / 6/6 | not re-run (see Method) | agent-stack 0.18.1, agent-sync 1.18.7, make-skill 0.25.3, seo-aeo-audit 0.25.8, sheleg-design 1.58.2, sheleg-dev 0.11.2, super-ux 0.52.3, task-pipeline 1.80.0, telegram-dev 0.1.10 | same: q06 0/3 before, 3/3 after; no neighbour query moved |

## Per-query trigger results — v1.18.7 run (both models)

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

The one stable failure was a positive: **q06** carried no coordination
vocabulary, and the description's trigger list had no submodule / parent-
pointer literal to catch it — both models routed it elsewhere on every sample.
Filed on the board as ASY-10 and fixed in v1.19.0; the before/after measurement
is the next section.

## ASY-10 — the q06 fix, measured before and after (2026-08-31, v1.19.0)

The board row said the cause was lexical: the description carried no submodule
or parent-pointer literal, so nothing in it could match the request. The fix is
one line of front matter — `finishing work across a parent repo and its
submodules - clean, pushed, and pointed at` in the *Use when* clause (the words
`finish` itself prints), plus the symptom as a trigger pair in both languages:
`'the submodule is pushed, the parent points at the old commit' / 'сабмодуль
запушен, родитель на старом коммите'`.

**The claim being tested is causal, so both arms were run in this session, on
one protocol, with one variable between them:** two corpus files identical
except for the single `- agent-sync: …` line — the shipped v1.18.7 description
in one, the v1.19.0 description in the other.

| Query | Model | Old description (773 chars) | Shipped new description (951 chars) |
|---|---|---|---|
| q06 | haiku | 0/3 — `none`, `task-pipeline`, `task-pipeline` | **3/3 — `agent-sync`** |
| q06 | sonnet | 0/3 — `none`, `none`, `none` | **3/3 — `agent-sync`** |

## Per-query trigger results — v1.19.0 run (shipped description, both models)

Every query re-probed, not only the fixed one: a widened trigger steals a
neighbour's query, and the only way to know it did not is to ask the neighbours.

| Query | Expected | haiku | sonnet |
|---|---|---|---|
| q01 leases for three agents | trigger | pass (agent-sync) | pass (agent-sync) |
| q02 claims и TTL (ru) | trigger | pass (agent-sync) | pass (agent-sync) |
| q03 race-free decision ids | trigger | pass (agent-sync) | pass (agent-sync) |
| q04 adopt agent-sync | trigger | pass (agent-sync) | pass (agent-sync) |
| q05 кто держит файл / release lease (ru) | trigger | pass (agent-sync) | pass (agent-sync) |
| q06 finish a submodule change | trigger | **pass, 3/3 samples (agent-sync)** | **pass, 3/3 samples (agent-sync)** |
| q07 working alone, normal branch | no trigger | pass (task-pipeline) | pass (none) |
| q08 полный цикл разработки (ru) | no trigger | pass (task-pipeline) | pass (task-pipeline) |
| q09 orchestrator for support agents | no trigger | pass (agent-orchestrator) | pass (agent-orchestrator) |
| q10 explain optimistic locking | no trigger | pass (none) | pass (none) |
| q11 GitHub Actions status (ru) | no trigger | pass (none) | pass (none) |
| q12 mutex around in-process cache | no trigger | pass (none) | pass (none) |

12/12 on both models — train 6/6, validation 6/6, up from 11/12. **No neighbour
was stolen.** The two negatives with the most to lose from submodule and finish
vocabulary are q07 (solo git branching) and q08 (the full delivery cycle,
task-pipeline's own ground); neither named `agent-sync` on either model. q07 on
haiku answered `task-pipeline` here and `none` on the discarded draft below —
sampling noise on a negative at n=1, and a pass either way, since a negative
passes on anything that is not this skill.

## Method (2026-08-31, v1.19.0 run)

- **Corpus.** One file per arm, `- name: description` per line, 28 skills —
  byte-identical to the list the v1.18.7 run used, with exactly one line
  replaced (`diff` reports one changed line, the `- agent-sync:` one). The
  corpus therefore carries the OTHER members at the descriptions they had on
  2026-08-31, not at whatever is installed today.
- **Probes.** One fresh, context-free `general-purpose` subagent per sample per
  model via the Claude Code Agent tool (`model: haiku` / `sonnet`), told to
  `cat` the corpus file, that the file is the authoritative and complete skill
  list, to ignore any other listing, and to answer with two lines: a count
  proving the read, and the skill name. **68 probes across three arms:** the
  old description (6 enforced on q06, plus 6 unenforced pilots), a discarded
  1017-char draft (6 on q06 + 22 on the rest), and the shipped 951-char text
  (6 on q06 + 22 on the rest).
- **The discarded draft, reported because it was measured.** The first fix ran
  to 1017 characters — inside the 1024 spec cap and **past the 970 house
  working limit** the family's pinned auditor enforces
  (`audit_skill.py --house` → `GAP DESC_HEADROOM`, exit 1, which is a CI
  failure, not a warning). It scored the same 3/3 on q06 and 12/12 overall; it
  is reported rather than quietly dropped, and the shipped text is the 951-char
  rewrite that passes the auditor `0 GAP, 14 PASS`. **Measuring one text and
  shipping another is the defect this file exists to prevent**, so every number
  in the tables above comes from the text in `SKILL.md`.
- **Why the read is forced, and it changed a result.** The first six probes only
  *offered* the file. All three haiku probes read it; all three sonnet probes
  answered with no tool call at all — from the skill listing the harness puts in
  every subagent's own prompt, where this machine's INSTALLED plugin still
  carries the old description. Those answers measure the machine, not the
  corpus, so every arm above was run with the read enforced. (Unenforced,
  old-description arm: `task-pipeline` on all three samples of each model — the
  same miss either way, but only haiku's three can be shown to have read the
  corpus at all.)
- **Installed alongside.** Measured from
  `~/.claude/plugins/marketplaces/*/…/plugin.json` at probe time and listed in
  the table rows. The installed `agent-sync` was 1.18.7 throughout — the new
  description existed only in the working tree and in the corpus file, which is
  why the after-arm result is conservative: the ambient listing in every probe's
  own prompt still advertised the OLD description.
- **Scenarios were not re-run.** The change is one front-matter line; it moves
  routing, not behaviour, and no scenario line depends on the description text.
  The v1.18.7 scenario results below stand as the current measurement, and the
  s01/s02 failures they record are still open.
- **Limits, unchanged from the v1.18.7 run.** Probes execute on this operator's
  machine, so their context also carries the machine's global routing doctrine
  and its own installed-skill listing — blind to this conversation, not blind to
  the operator's rules. Model ids are the Agent tool's aliases, not pinned
  snapshot ids. n=1 per query per model except q06 (n=3 per arm per model), so a
  single negative cell is one sample, as q07 above shows. The probe self-reported
  skill counts varied (25–53 against an actual 28), so treat that field as
  evidence that a read happened, not as an arithmetic result.

## Scenario line results (2026-08-31, v1.18.7 run)

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

## Method (2026-08-31, v1.18.7 run)

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
