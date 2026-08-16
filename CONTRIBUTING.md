# Contributing

## Verify a change — offline, no credentials needed

```bash
python3 test/validate.py
```

Exit 0 prints `PASS: agent-sync vX.Y.Z — all checks green`. That covers the Agent
Skills spec floor, this repo's house rules, version sync, and the two rules that
exist because breaking them ships a secret.

Then prove the validator can still fail:

```bash
python3 test/validate.py --self-test
```

It copies the tree and injects one defect at a time — an over-cap description, a
version drift, a leaked host name, a token passed in `argv`, a stray `SKILL.md`,
bytecode in the npm tarball, and the rest — requiring each to be caught. A
validator that cannot fail is decoration.

**The count is read off the run's own last line, not from this page.** It said
*five* while the suite injected thirty-seven, and the number is the whole claim:
a contributor who reads five will not think to add a fixture for the sixth thing
they change.

Exercise the coordinator itself against a scratch repository:

```bash
S=plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py
T=$(mktemp -d) && (cd "$T" && git init -q && git commit -q --allow-empty -m init)
python3 "$S" status                      # expect exit 1 and one next action
(cd "$T" && python3 "$OLDPWD/$S" init --backend fs)
```

Two-process contention, which is the whole point of the protocol:

```bash
AGENT_SYNC_RUN_ID=alpha python3 "$S" acquire T-1   # won
AGENT_SYNC_RUN_ID=beta  python3 "$S" acquire T-1   # lost, names alpha
```

## House rules

- **One skill, one job.** If a change needs a second skill, it probably needs a
  clearer boundary instead.
- **Contracts live inside the skill directory.** The skills CLI ships only that
  folder; a sibling `references/` arrives broken on every agent outside Claude Code.
- **Gotchas belong in `SKILL.md`, not only in `references/`.** An agent cannot know
  to open a file about a trap it has not met.
- **Every reference file states when to read it.** An unconditional pointer gets
  loaded always or never.
- **Degrade out loud.** Any path where the tool cannot do what it claims must say
  so. Silent best-effort is the failure mode this project exists to prevent.
- **No host address, no credential** in anything under `plugins/`, `bin/`, `test/`
  or an example. Identity belongs in the environment.

## Versioning

**Six** places move together, and the validator enforces all six:

`.claude-plugin/marketplace.json` · `plugins/agent-sync/.claude-plugin/plugin.json` ·
`package.json` · the top `CHANGELOG.md` entry · `metadata.version` in `SKILL.md` ·
`VERSION` in `plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py`.

The sixth is the one that cost a patch release. This page said five, a bump driver
written from it moved four, and v1.11.1 exists because CI refused the tag — the
`VERSION` constant is what `status` prints into every session, so its drift is
invisible in the manifests and loud in the banner. Match both the quoted and the
bare form when bumping it.


### The family catalogue moves with the release

`sshlg-skills` — the launcher that installs and updates the whole ssheleg family — pins every
member's version in its own `skills.json`. **A release that does not bump that pin is invisible.**
`npx sshlg-skills list` keeps reporting the previous version, `update` keeps installing it, and
anyone comparing their install against `list` is told the wrong number with nothing to reveal it.

So a release is not finished at `npm publish`:

```bash
# in ssheleg/sshlg-skills
#   1. bump this member's "version" in skills.json
#   2. bump the launcher's own version, changelog, tag
npm publish --access public
npx --yes sshlg-skills@latest list   # the new number must appear here
```

This is not hypothetical. On 2026-07-29 `agent-sync` 1.3.4 was on npm, installed everywhere, and
`list` still said 1.3.3 — so a project whose onboarding compares the running version against `list`
told every agent to update to a version it already had.

## Coordinating with other agents

`docs/AGENT_SYNC.md` describes how coordination is wired in this repository and
what it does **not** guarantee. It is generated from `.claude/agent-sync.json`:
read it before editing a file that config guards, and regenerate it with
`agent_sync.py setup` in the same change that alters the config.

## Commits

Conventional commits. One logical change each. Note in the message when a change
alters the log grammar or the replay rules — those are the only parts where a
mistake corrupts state that already exists in someone's knowledge base.
