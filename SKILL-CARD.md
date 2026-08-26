# Skill Card — agent-sync

## Identity

| Field | Value |
|---|---|
| Pack and skill | `agent-sync` |
| Version | `1.18.1` |
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
contact their configured service. Tokens belong in `.env.agent-sync`, mode 600,
and must not enter argv, logs or commits. Enforcement degrades explicitly when a
backend cannot provide the required capability.

## Distribution

Install from npm/GitHub, through the Agent Skills CLI, or as the
`agent-sync` Claude Code plugin. The plugin supplies hooks; it does not add a
second settings-based hook channel.

## Verification

- Repository validator: `python3 test/validate.py`
- Protocol tests: repository test suite and planted negative checks
- House audit: pinned `make-skill` auditor in `validate.yml`
- Behavioral data: `test/evals/`
- Evaluation status: authored, never executed against a model

## Known limits

A lease expires; it does not remove residue or finish a Git change. Local
advisory mode cannot claim remote enforcement. A submodule change remains
unfinished until its parent points at the released commit.

