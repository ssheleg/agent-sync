# Skill Card — agent-sync

## Identity

| Field | Value |
|---|---|
| Pack and skill | `agent-sync` |
| Version | `1.23.4` |
| License | MIT |
| Source | https://github.com/ssheleg/agent-sync |

## Job and boundary

Coordinate several coding-agent sessions in one project through scoped leases,
race-free ids, a run journal and generated views. It is not a replacement for
Git, a delivery pipeline, an in-process mutex or an agent orchestrator.

## Inputs and outputs

Inputs are project configuration, a session identity and a scoped claim.
Outputs are append-only coordination records, leases, reserved ids and generated
board views. Guarded documents are edited only after a claim.

## Runtime and trust

The bundled CLI is Python. Filesystem mode works locally; optional backends may
contact their configured service. The `notion` token belongs in the Observatory
vault (1.22.0): record-plane commands re-run themselves under
`use_secret.py run --vault-only`; the guard, `renew` and SessionEnd's
`release --held` never ask the vault. Without
Observatory, and for `outline`, the token lives in `.env.agent-sync`, mode 600,
reported as degraded for `notion`. No token enters argv, logs or commits.
Enforcement degrades explicitly when a backend cannot provide the required
capability. A session's leases in other repositories are listed under
`~/.agent-sync/session-repos/` so its hooks renew and release them there.

## Distribution

Install from npm/GitHub, through the Agent Skills CLI, or as the
`agent-sync` Claude Code plugin. The plugin supplies hooks; it does not add a
second settings-based hook channel.

## Verification

- Repository validator: `python3 test/validate.py`
- Protocol tests: repository test suite and planted negative checks
- House audit: pinned `make-skill` auditor in `validate.yml`
- Behavioral data: `test/evals/`
- Evaluation status: executed twice on 2026-08-31 against haiku and sonnet — 12/12 trigger queries on both models after the v1.19.0 description fix, with the q06 before/after arms, the scenario lines and the method's limits in `test/evals/RESULTS.md`

## Known limits

A lease expires; it does not remove residue or finish a Git change. Clearing
residue by age (`reap --long-expired --apply`) is an operator's decision, never
an agent's. The Bash write guard is a parser, not a sandbox: `$VAR` targets,
the arguments `xargs` reads from stdin, `perl -i`, `awk` and runtime writes are
outside it. A commit is checked with what the same command stages
(`git add X && git commit`, AS-16). Local
advisory mode cannot claim remote enforcement. A submodule change remains
unfinished until its parent points at the released commit.

