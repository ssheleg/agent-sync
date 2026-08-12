#!/usr/bin/env node
'use strict';

/**
 * agent-sync installer. Zero dependencies.
 *
 * One channel per agent: Claude Code gets the plugin, every other agent gets the
 * skill through the vercel skills CLI, and the plain ~/.claude/skills/agent-sync
 * copy that the skills CLI recreates on its own is pruned afterwards — that
 * duplicate shadows the plugin and silently serves a stale skill.
 */

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const REPO = 'ssheleg/agent-sync';
const NAME = 'agent-sync';
const SHADOW = path.join(os.homedir(), '.claude', 'skills', NAME);

const C = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  bold: (s) => `\x1b[1m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
};

function run(cmd, args) {
  process.stdout.write(C.dim(`  $ ${cmd} ${args.join(' ')}\n`));
  const r = spawnSync(cmd, args, { stdio: 'inherit' });
  return r.status === 0;
}

function has(cmd) {
  return spawnSync(process.platform === 'win32' ? 'where' : 'which', [cmd], {
    stdio: 'ignore',
  }).status === 0;
}

function usage() {
  console.log(`
${C.bold('agent-sync')} — coordination for concurrent agents

  npx @ssheleg/${NAME} install              install for Claude Code and other agents
  npx @ssheleg/${NAME} install --claude-only  Claude Code plugin only
  npx @ssheleg/${NAME} install --agent a,b    pick agents for the skills CLI
  npx @ssheleg/${NAME} update               update every channel, then prune the shadow
  npx @ssheleg/${NAME} --help

After installing, initialise the project — this is the step that asks where
coordination state should live:

  ${C.bold('/agent-sync init')}

It writes .claude/agent-sync.json (committed) and .env.agent-sync (gitignored,
mode 600), then tells you the one thing only you can do: create an API token in
your own knowledge-base instance and paste it into that file. The installer never
asks for a token and never stores one.
`);
}

function install(argv) {
  const claudeOnly = argv.includes('--claude-only');
  const noClaude = argv.includes('--no-claude');
  const agentIdx = argv.indexOf('--agent');
  const agents = agentIdx !== -1 && argv[agentIdx + 1] ? argv[agentIdx + 1].split(',') : null;

  let ok = true;

  if (!noClaude) {
    console.log(C.bold('\nClaude Code — as a plugin'));
    if (!has('claude')) {
      console.log(C.yellow('  claude CLI not found; skipping the plugin channel'));
    } else {
      // The full <name>@<name> form is required; `claude plugin install agent-sync`
      // answers "Plugin not found".
      ok = run('claude', ['plugin', 'marketplace', 'add', REPO]) && ok;
      ok = run('claude', ['plugin', 'install', `${NAME}@${NAME}`]) && ok;
    }
  }

  if (!claudeOnly) {
    console.log(C.bold('\nOther agents — via the skills CLI'));
    const args = ['--yes', 'skills', 'add', REPO, '--global', '--yes'];
    for (const a of agents || []) args.push('--agent', a);
    ok = run('npx', args) && ok;
  }

  pruneShadow();

  console.log(
    ok
      ? C.green('\n✓ installed')
      : C.red('\n✗ at least one channel failed — see the output above')
  );
  // Before the "Next:" block, so the last thing on screen stays the instruction
  // rather than the tail of a delegated command.
  offerRouters();
  console.log(`
${C.bold('Next:')} restart Claude Code, then run ${C.bold('/agent-sync init')} in your project.
It will ask where coordination state should live before writing anything.
`);
  return ok ? 0 : 1;
}

/**
 * Ask the family launcher to write the routing block, for this member only.
 *
 * Delegated rather than reimplemented, for three reasons. The block describes
 * what the machine actually has, so a lone member rendering the whole thing
 * would produce a table for routers nobody installed. `--member` limits the
 * write to the `agent-sync` section and leaves everyone else's alone, which is
 * what lets the bundle and a single installer both write. And the launcher is
 * the only writer that copies the operator's global instruction file before
 * touching it — that file has no version control behind it.
 *
 * `--no-install` keeps this from silently downloading a package nobody asked
 * for. When the launcher is absent, print the command rather than fail: ending
 * an install in an error because an OPTIONAL follow-up is missing reads as a
 * failed install.
 */
function offerRouters() {
  const r = spawnSync(
    'npx',
    ['--no-install', 'sshlg-skills', 'routers', '--member', NAME],
    { stdio: 'inherit', shell: process.platform === 'win32' }
  );
  if (r.status !== 0) {
    console.log(
      `\nTo have this skill apply by default in every project, add the ` +
      `family's\nrouting block to your agent's global instructions:\n\n` +
      `  npx --yes sshlg-skills routers --member ${NAME}\n`
    );
  }
}

/**
 * The shadow regrows on its own: `npx skills add|update --global` auto-detects
 * Claude Code and recreates ~/.claude/skills/<name> — often as a symlink — even when
 * claude-code was never named as a target. That copy shadows the plugin and serves a
 * stale skill, so the prune belongs INSIDE every command that touches the skills CLI,
 * not in a human's memory. lstatSync, because a symlink shadows exactly as a dir does.
 */
function pruneShadow() {
  let present = false;
  try {
    fs.lstatSync(SHADOW);
    present = true;
  } catch {
    /* not there */
  }
  if (!present) return;
  fs.rmSync(SHADOW, { recursive: true, force: true });
  console.log(C.dim(`  pruned duplicate ${SHADOW}`));
}

function update() {
  console.log(C.bold('\nUpdating every channel'));
  let ok = true;
  if (has('claude')) {
    ok = run('claude', ['plugin', 'marketplace', 'update', NAME]) && ok;
    // The full <name>@<name> id is required; `claude plugin update <name>` answers
    // "Plugin not found".
    ok = run('claude', ['plugin', 'update', `${NAME}@${NAME}`]) && ok;
  }
  ok = run('npx', ['--yes', 'skills', 'update', NAME, '--global', '--yes']) && ok;
  pruneShadow();
  console.log(ok ? C.green('\n✓ updated') : C.red('\n✗ a channel failed — see above'));
  console.log('\nRestart Claude Code so it picks the new version up.');
  return ok ? 0 : 1;
}

const argv = process.argv.slice(2);
if (argv.length === 0 || argv.includes('--help') || argv.includes('-h')) {
  usage();
  process.exit(0);
}
if (argv[0] === 'install') process.exit(install(argv.slice(1)));
if (argv[0] === 'update') process.exit(update());
usage();
process.exit(1);
