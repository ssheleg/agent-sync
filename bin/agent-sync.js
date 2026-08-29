#!/usr/bin/env node
'use strict';

/**
 * agent-sync installer. Zero dependencies.
 *
 * One channel per agent: Claude Code gets the plugin, every other agent gets the
 * skill through the vercel skills CLI, and the plain ~/.claude/skills/agent-sync
 * copy that the skills CLI recreates on its own is settled afterwards against
 * the home's installed_plugins.json — pruned when the plugin owns Claude Code
 * (the duplicate would shadow it and silently serve a stale skill), kept when no
 * plugin does (then it IS the Claude Code channel), kept on --force as the
 * recorded choice to run two channels. Canon: make-skill distribution.md §3.
 */

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const REPO = 'ssheleg/agent-sync';
const NAME = 'agent-sync';
const SHADOW = path.join(os.homedir(), '.claude', 'skills', NAME);

/**
 * The plugin spec (`<name>@<marketplace>`) installed for `name` in this home,
 * or null.
 *
 * `installed_plugins.json` is the record of what is actually installed. The
 * `plugins/marketplaces/<name>` directory — the only signal the family's
 * installers read until the 2026-08-29 canon (make-skill v0.25.0,
 * distribution.md §3) — under-reports: a marketplace added from a local
 * `directory` source has no dir there at all, and plugin names differ from
 * marketplace names, so a check keyed on it stays green while the shadow
 * lands. Absence and corruption both read as "no plugin": the fresh HOME is
 * the common case, and an installer that crashes on a parse error refuses the
 * machines that need it most.
 */
function installedPluginSpec(home, name) {
  try {
    const raw = fs.readFileSync(
      path.join(home, '.claude', 'plugins', 'installed_plugins.json'), 'utf8');
    const parsed = JSON.parse(raw);
    const plugins =
      parsed && typeof parsed === 'object' &&
      parsed.plugins && typeof parsed.plugins === 'object'
        ? parsed.plugins
        : parsed;
    if (!plugins || typeof plugins !== 'object') return null;
    for (const spec of Object.keys(plugins)) {
      if (spec === name) return `${name}@${name}`;
      if (spec.startsWith(name + '@')) return spec;
    }
  } catch {
    // missing or corrupt = no plugin — fail open on absence, never crash
  }
  return null;
}

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
  npx @ssheleg/${NAME} update               update every channel, then settle the
                                            ~/.claude/skills copy: pruned when it
                                            would shadow the installed plugin, kept
                                            when no plugin owns Claude Code
  npx @ssheleg/${NAME} install|update --force  keep the plain copy even beside the
                                            plugin — two channels, the stale one wins
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
  const force = argv.includes('--force');
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

  settleClaudeChannel(force);

  console.log(
    ok
      ? C.green('\n✓ installed')
      : C.red('\n✗ at least one channel failed — see the output above')
  );
  // Before the "Next:" block, so the last thing on screen stays the instruction
  // rather than the tail of a delegated command.
  offerRouters();
  // How the next version arrives — an installer that never says has still
  // chosen an update model: never.
  console.log(`
${C.bold('Updates:')} npx @ssheleg/${NAME}@latest update — every channel, and it settles
the plain copy that would shadow the plugin. Whole family:
npx --yes sshlg-skills@latest update.
`);
  console.log(`${C.bold('Next:')} restart Claude Code, then run ${C.bold('/agent-sync init')} in your project.
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
 * Decide the fate of ~/.claude/skills/<name> after a skills-CLI run.
 *
 * The shadow regrows on its own: `npx skills add|update --global` auto-detects
 * Claude Code and recreates ~/.claude/skills/<name> — often as a symlink — even when
 * claude-code was never named as a target. What that copy IS depends on the home it
 * landed in, and until v1.18.5 this function consulted nothing and deleted it
 * unconditionally — the family's fail-open class (make-skill distribution.md §3) in
 * mirror image: on a home where the plugin is NOT installed (no claude CLI, or the
 * plugin install failed), the unconditional prune destroyed the only Claude Code
 * channel this very run had just installed, and exited 0.
 *
 * - Plugin installed in this home — read from installed_plugins.json, with the
 *   marketplaces/<name> dir kept only as the fallback signal: the copy is a SHADOW.
 *   It outranks the plugin and serves the version it was copied from forever.
 *   Prune it, and name the plugin spec it would have shadowed plus the channel
 *   that owns updates.
 * - No plugin: the copy is Claude Code's only channel. Keep it, and say so.
 * - --force: the deliberate choice to run two channels, where the stale one wins.
 *   The copy stays even beside the plugin, and the output records the choice.
 *
 * The gate lives INSIDE every command that touches the skills CLI, not in a human's
 * memory. lstatSync, because a symlink shadows exactly as a dir does.
 */
function settleClaudeChannel(force) {
  let present = false;
  try {
    fs.lstatSync(SHADOW);
    present = true;
  } catch {
    /* not there */
  }
  if (!present) return;

  const home = os.homedir();
  const spec = installedPluginSpec(home, NAME);
  const marketplace = path.join(home, '.claude', 'plugins', 'marketplaces', NAME);
  const viaMarketplaceDir = !spec && fs.existsSync(marketplace);

  if (!spec && !viaMarketplaceDir) {
    console.log(C.dim(
      `  kept ${SHADOW} — no ${NAME} plugin is installed in this home,\n` +
      `  so this plain copy is Claude Code's only channel for the skill`));
    return;
  }
  if (force) {
    console.log(C.yellow(
      `  kept ${SHADOW} beside the installed plugin (--force) — two channels\n` +
      `  on one agent, and the stale plain copy is the one Claude Code reads`));
    return;
  }
  const found = spec
    ? `the Claude Code plugin ${spec} is installed (installed_plugins.json)`
    : `a Claude Code marketplace is registered at ${marketplace}`;
  fs.rmSync(SHADOW, { recursive: true, force: true });
  console.log(C.dim(
    `  pruned ${SHADOW} — ${found};\n` +
    `  a plain copy there would shadow the plugin and serve a frozen version\n` +
    `  forever. The plugin channel owns updates:\n` +
    `    claude plugin marketplace update ${NAME}\n` +
    `    claude plugin update ${spec || `${NAME}@${NAME}`}\n` +
    `  Pass --force to keep the plain copy anyway — a deliberate choice to run\n` +
    `  two channels, where the stale one wins.`));
}

function update(argv) {
  const force = argv.includes('--force');
  console.log(C.bold('\nUpdating every channel'));
  let ok = true;
  if (has('claude')) {
    ok = run('claude', ['plugin', 'marketplace', 'update', NAME]) && ok;
    // The full <name>@<name> id is required; `claude plugin update <name>` answers
    // "Plugin not found".
    ok = run('claude', ['plugin', 'update', `${NAME}@${NAME}`]) && ok;
  }
  ok = run('npx', ['--yes', 'skills', 'update', NAME, '--global', '--yes']) && ok;
  settleClaudeChannel(force);
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
if (argv[0] === 'update') process.exit(update(argv.slice(1)));
usage();
process.exit(1);
