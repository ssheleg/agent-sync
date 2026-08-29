#!/usr/bin/env node
/*
 * Installer functional tests — both installers, against throwaway HOMEs.
 *
 * The case that earns this file its place is the CLAUDE-CHANNEL GATE. This
 * repository's installers never write ~/.claude/skills/agent-sync themselves:
 * the skills CLI they drive recreates that copy on its own, and until v1.18.5
 * both installers deleted it unconditionally, consulting nothing — the family's
 * fail-open class (make-skill v0.25.0, distribution.md §3) in mirror image. On
 * a home where the agent-sync plugin IS installed the unconditional prune was
 * right; on a home where it is NOT — no claude CLI, or the plugin install
 * failed — the prune destroyed the only Claude Code channel the very same run
 * had just installed, and exited 0. Every member's CI tested a fresh HOME only,
 * so neither branch of that decision had ever been asserted anywhere.
 *
 * The delegated commands are stubbed through PATH: a fake `npx` plays the
 * skills CLI (it recreates the plain copy exactly the way the real one does)
 * and no `claude` exists on the controlled PATH, so the plugin channel is
 * exercised purely through the state file that records it —
 * installed_plugins.json, which is what the gate reads.
 *
 * House residue rule: a passing case loses its temp HOME at exit, a failing
 * case KEEPS it (a defect is debugged by reading the tree it landed in), and
 * the run ends with one line saying what it left, `nothing` included.
 */
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const BIN = path.join(ROOT, 'bin', 'agent-sync.js');
const SH = path.join(ROOT, 'install.sh');
const NAME = 'agent-sync';

if (process.platform === 'win32') {
  // The PATH shims are POSIX shell scripts, and install.sh is POSIX-only by its
  // own header. Said loudly rather than passed quietly: CI runs this on Linux.
  console.log('skip: installer cases (POSIX shims — CI covers this on Linux)');
  process.exit(0);
}

let failures = 0;
const homes = []; // { dir, label, failed }

function freshHome(label) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-sync-test-home-'));
  homes.push({ dir, label, failed: false });
  // The fake npx: plays the skills CLI by recreating the plain Claude Code copy
  // — the exact uninvited write the gate under test must settle — and refuses
  // the optional `sshlg-skills routers` follow-up, which the installer must
  // survive by printing the command instead of failing.
  const fakeBin = path.join(dir, 'fake-bin');
  fs.mkdirSync(fakeBin, { recursive: true });
  fs.writeFileSync(
    path.join(fakeBin, 'npx'),
    '#!/bin/sh\n' +
    'case " $* " in\n' +
    '  *" skills "*)\n' +
    `    mkdir -p "$HOME/.claude/skills/${NAME}"\n` +
    `    echo "stale copy the skills CLI wrote" > "$HOME/.claude/skills/${NAME}/SKILL.md"\n` +
    '    echo "fake skills CLI: recreated the plain Claude Code copy"\n' +
    '    exit 0 ;;\n' +
    '  *) exit 1 ;;\n' +
    'esac\n',
    { mode: 0o755 });
  return dir;
}

function run(cmd, args, home) {
  const r = spawnSync(cmd, args, {
    cwd: home, // never the repo: npx inside the package's own repo resolves locally
    env: Object.assign({}, process.env, {
      HOME: home,
      USERPROFILE: home,
      // Only the shims, node, and the system tools `which`/`sh` need — the real
      // claude and npx must be unreachable or the test talks to the network.
      PATH: [path.join(home, 'fake-bin'), path.dirname(process.execPath),
             '/usr/bin', '/bin'].join(path.delimiter),
    }),
    encoding: 'utf8',
    timeout: 120000,
  });
  return { status: r.status, out: (r.stdout || '') + (r.stderr || '') };
}

const installer = (home, ...args) => run(process.execPath, [BIN, ...args], home);
const shInstaller = (home, ...args) => run('sh', [SH, ...args], home);

function skillDir(home) {
  return path.join(home, '.claude', 'skills', NAME);
}

function declarePlugin(home, spec) {
  const dir = path.join(home, '.claude', 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'installed_plugins.json'), JSON.stringify({
    version: 2,
    plugins: { [spec]: [{ scope: 'user', installPath: '/nonexistent', version: '1.18.4' }] },
  }, null, 2));
}

function caseRun(label, fn) {
  const home = freshHome(label);
  const rec = homes[homes.length - 1];
  try {
    fn(home);
    console.log(`ok: ${label}`);
  } catch (e) {
    rec.failed = true;
    failures++;
    console.error(`FAIL: ${label}\n  ${e.message}`);
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- node CLI --

caseRun('no plugin in this home: the skills-CLI copy is KEPT — it is the only Claude channel', (home) => {
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `the plain copy was pruned although no plugin owns Claude Code in this home:\n${r.out}`);
  assert(/kept .*only channel/s.test(r.out), `the keep is not explained:\n${r.out}`);
  // the installer states how the next version arrives
  assert(r.out.includes('sshlg-skills@latest update'), `no update path named:\n${r.out}`);
  assert(r.out.includes(`@ssheleg/${NAME}@latest update`), `own update line missing:\n${r.out}`);
});

caseRun('plugin present in installed_plugins.json: the shadow is pruned, spec and remedy named', (home) => {
  declarePlugin(home, `${NAME}@${NAME}`);
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(!fs.existsSync(skillDir(home)),
    `the shadow copy survived beside the installed plugin:\n${r.out}`);
  assert(r.out.includes(`claude plugin update ${NAME}@${NAME}`),
    `remedy does not name the plugin spec:\n${r.out}`);
  assert(r.out.includes('--force'), `override flag not offered:\n${r.out}`);
});

caseRun('plugin under a differently-named marketplace: the message carries the real spec', (home) => {
  declarePlugin(home, `${NAME}@sshlg-skills`);
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(!fs.existsSync(skillDir(home)), `the shadow copy survived:\n${r.out}`);
  assert(r.out.includes(`claude plugin update ${NAME}@sshlg-skills`),
    `the spec from the JSON is not in the remedy:\n${r.out}`);
});

caseRun('--force keeps the copy beside the plugin, deliberately', (home) => {
  declarePlugin(home, `${NAME}@${NAME}`);
  const r = installer(home, 'install', '--force');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `--force still pruned the copy:\n${r.out}`);
  assert(/kept .*--force/s.test(r.out), `the recorded choice is not in the output:\n${r.out}`);
});

caseRun('corrupt installed_plugins.json reads as "no plugin" — keep the copy, never crash', (home) => {
  const dir = path.join(home, '.claude', 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'installed_plugins.json'), '{ this is not json');
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0 (fail open)\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `a parse error was read as "plugin present":\n${r.out}`);
});

caseRun('other plugins, and a prefix-collider, do not trigger a false prune', (home) => {
  const dir = path.join(home, '.claude', 'plugins');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'installed_plugins.json'), JSON.stringify({
    version: 2,
    plugins: {
      'telegram-dev@telegram-dev': [{ scope: 'user', installPath: '/x', version: '1.0.0' }],
      [`${NAME}-extra@somewhere`]: [{ scope: 'user', installPath: '/y', version: '1.0.0' }],
    },
  }));
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `a foreign plugin name pruned this skill's channel:\n${r.out}`);
});

caseRun('marketplaces/<name> dir alone still prunes (fallback signal)', (home) => {
  fs.mkdirSync(path.join(home, '.claude', 'plugins', 'marketplaces', NAME),
    { recursive: true });
  const r = installer(home, 'install');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(!fs.existsSync(skillDir(home)),
    `the marketplace-dir fallback did not fire:\n${r.out}`);
  assert(r.out.includes(`claude plugin update ${NAME}@${NAME}`),
    `no default remedy spec:\n${r.out}`);
});

caseRun('update settles the channel the same way the install does', (home) => {
  declarePlugin(home, `${NAME}@${NAME}`);
  const r = installer(home, 'update');
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(!fs.existsSync(skillDir(home)),
    `update left the shadow standing beside the plugin:\n${r.out}`);
});

// --------------------------------------------------------------- install.sh --

caseRun('install.sh: no plugin — the copy is kept, and the update path is named', (home) => {
  const r = shInstaller(home);
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `install.sh pruned Claude Code's only channel:\n${r.out}`);
  assert(r.out.includes('sshlg-skills@latest update'), `no update path named:\n${r.out}`);
});

caseRun('install.sh: plugin present — pruned with the spec; --force keeps it', (home) => {
  declarePlugin(home, `${NAME}@sshlg-skills`);
  const r = shInstaller(home);
  assert(r.status === 0, `exit ${r.status}, expected 0\n${r.out}`);
  assert(!fs.existsSync(skillDir(home)), `the shadow copy survived:\n${r.out}`);
  assert(r.out.includes(`claude plugin update ${NAME}@sshlg-skills`),
    `remedy does not carry the spec from the JSON:\n${r.out}`);
  const forced = shInstaller(home, '--force');
  assert(forced.status === 0, `--force exit ${forced.status}\n${forced.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `--force still pruned the copy:\n${forced.out}`);
});

caseRun('install.sh: corrupt JSON fails open, unknown arg exits 2', (home) => {
  fs.mkdirSync(path.join(home, '.claude', 'plugins'), { recursive: true });
  fs.writeFileSync(path.join(home, '.claude', 'plugins', 'installed_plugins.json'),
    '{ this is not json');
  const r = shInstaller(home);
  assert(r.status === 0, `exit ${r.status}, expected 0 (fail open)\n${r.out}`);
  assert(fs.existsSync(path.join(skillDir(home), 'SKILL.md')),
    `a parse error was read as "plugin present":\n${r.out}`);
  const bad = shInstaller(home, '--wat');
  assert(bad.status === 2, `unknown arg exit ${bad.status}, expected 2\n${bad.out}`);
});

// ----------------------------------------------------------------- residue --

let removed = 0;
const kept = [];
for (const h of homes) {
  if (h.failed) {
    kept.push(h);
  } else {
    fs.rmSync(h.dir, { recursive: true, force: true });
    removed++;
  }
}
if (kept.length === 0) {
  console.log(`residue: this run left nothing — ${homes.length} temp home(s) created, ${removed} removed`);
} else {
  console.log(`residue: ${kept.length} of ${homes.length} temp home(s) KEPT`);
  for (const h of kept) {
    console.log(`  ${h.dir}  (case: ${h.label})  — rm -rf '${h.dir}' when done`);
  }
}

if (failures) {
  console.error(`FAIL: installer — ${failures} case(s) red`);
  process.exit(1);
}
console.log(`PASS: installer — ${homes.length} case(s)`);
