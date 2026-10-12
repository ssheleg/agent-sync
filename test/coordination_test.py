#!/usr/bin/env python3
"""Coordination fixes of the 2026-10-12 integration wave (hub plan R17–R22).

Each case names the defect it pins. Every one was run against the script at origin/main
before the fix and watched failing — run this file with `AGENT_SYNC_SCRIPT=<old copy>` to
repeat that.

Fixtures are temp trees removed in `finally`; nothing is left in $TMPDIR.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.environ.get("AGENT_SYNC_SCRIPT") or os.path.join(
    REPO, "plugins", "agent-sync", "skills", "agent-sync", "scripts", "agent_sync.py")

cases = 0
failures = []


def case(name, fn):
    global cases
    cases += 1
    try:
        fn()
        print("  PASS   %s" % name)
    except AssertionError as e:
        failures.append(name)
        print("  FAIL   %s\n         %s" % (name, e))
    except Exception as e:
        failures.append(name)
        print("  ERROR  %s\n         %s: %s" % (name, type(e).__name__, e))


def _module():
    spec = importlib.util.spec_from_file_location("agent_sync_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _env:
    """Set environment variables for a block and restore them after."""

    def __init__(self, **kv):
        self.kv = kv
        self.old = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ----------------------------------------------------------------- R17 run identity

def two_keys_sharing_a_prefix_are_two_runs():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        from pathlib import Path
        with _env(AGENT_SYNC_RUN_ID="observatory-scheduled-tick-1", CLAUDE_SESSION_ID=None):
            a = mod.run_id(Path(d))
        with _env(AGENT_SYNC_RUN_ID="observatory-scheduled-tick-2", CLAUDE_SESSION_ID=None):
            b = mod.run_id(Path(d))
        assert a != b, "two distinct AGENT_SYNC_RUN_ID values shared one run id: %s" % a
    finally:
        shutil.rmtree(d, ignore_errors=True)


def a_short_key_keeps_its_readable_id():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        from pathlib import Path
        with _env(AGENT_SYNC_RUN_ID="tick7", CLAUDE_SESSION_ID=None):
            assert mod.run_id(Path(d)) == "r-tick7", mod.run_id(Path(d))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def the_same_key_is_the_same_run_every_time():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        from pathlib import Path
        with _env(AGENT_SYNC_RUN_ID="observatory-scheduled-tick-1", CLAUDE_SESSION_ID=None):
            assert mod.run_id(Path(d)) == mod.run_id(Path(d))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- AS-08 register width

def _fake_sync(mod, root, reg, file):
    class Fake:
        pass
    f = Fake()
    from pathlib import Path
    f.root = Path(root)
    f.cfg = {"idRegisters": {reg: {"file": file}}}
    f._register_text = lambda r: mod.Sync._register_text(f, r)
    return f


def a_three_digit_register_does_not_leak_ids_it_holds():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        open(os.path.join(d, "contracts.md"), "w").write(
            "| CO-049 | a |\n| CO-050 | b |\n| CO-051 | c |\n| CO-052 | d |\n")
        events = [{"op": "base", "key": "CO", "run": "r-a", "value": "0050"},
                  {"op": "reserve", "key": "CO", "run": "r-a", "value": "0051"},
                  {"op": "reserve", "key": "CO", "run": "r-b", "value": "0052"}]
        fake = _fake_sync(mod, d, "CO", "contracts.md")
        leaks = mod.Sync._leaks(fake, events)
        assert leaks == [], "ids present as CO-051/CO-052 reported as leaks: %r" % (leaks,)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def an_id_really_missing_is_still_a_leak():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        open(os.path.join(d, "contracts.md"), "w").write("| CO-050 | b |\n| CO-0510 | x |\n")
        events = [{"op": "base", "key": "CO", "run": "r-a", "value": "0050"},
                  {"op": "reserve", "key": "CO", "run": "r-a", "value": "0051"}]
        fake = _fake_sync(mod, d, "CO", "contracts.md")
        leaks = mod.Sync._leaks(fake, events)
        assert [(r, v) for r, v, _ in leaks] == [("CO", 51)], leaks
    finally:
        shutil.rmtree(d, ignore_errors=True)


def a_reserved_id_is_printed_at_the_registers_width():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        open(os.path.join(d, "contracts.md"), "w").write("| CO-050 | b |\n")
        fake = _fake_sync(mod, d, "CO", "contracts.md")
        assert mod.Sync.fmt_id(fake, "CO", 51) == "CO-051", mod.Sync.fmt_id(fake, "CO", 51)
        open(os.path.join(d, "contracts.md"), "w").write("")
        assert mod.Sync.fmt_id(fake, "CO", 51) == "CO-0051"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- R19 long-held leases

def a_lease_held_past_the_scheduled_interval_is_reported():
    mod = _module()
    got = mod.long_held({"A": 7200.0, "B": 60.0}, 3600)
    assert [k for k, _ in got] == ["A"], got


def no_interval_means_nothing_to_report():
    mod = _module()
    assert mod.long_held({"A": 99999.0}, None) == []
    assert mod.long_held({"A": 99999.0}, 0) == []


# ----------------------------------------------------------------- AS-11 long-expired residue

def _repo():
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    subprocess.run(["git", "init", "-q", d], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump({"backend": "fs", "leaseTtlSeconds": 2700, "gated": True,
                   "guardedFiles": []}, fh)
    open(os.path.join(d, "README.md"), "w").write("x\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True)
    return d


def _cli(root, *args):
    env = dict(os.environ)
    env["AGENT_SYNC_RUN_ID"] = "coordtest-sweeper"
    env.pop("CLAUDE_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)


def _lock(root, mod, key, run, ts, ttl=2700):
    d = os.path.join(root, ".agent-sync", "leases")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, mod.lease_name(key) + ".lock")
    with open(path, "w") as fh:
        json.dump({"key": key, "run": run, "ts": ts, "ttl": ttl,
                   "repo": os.path.basename(root), "host": "elsewhere.example"}, fh)
    return path


def a_long_expired_foreign_lock_is_listed_then_cleared_only_with_apply():
    mod = _module()
    root = _repo()
    try:
        old = _lock(root, mod, "OLD-1", "r-deadrun", "2026-08-01T00:00:00Z")
        fresh = _lock(root, mod, "FRESH-1", "r-deadrun",
                      mod.now_iso(), ttl=1)   # expired a moment ago, not long expired
        listed = _cli(root, "reap", "--long-expired")
        assert listed.returncode == 0, listed.stdout + listed.stderr
        assert "OLD-1" in listed.stdout and "FRESH-1" not in listed.stdout, listed.stdout
        assert os.path.exists(old), "the default must only list"
        applied = _cli(root, "reap", "--long-expired", "--apply")
        assert not os.path.exists(old), applied.stdout + applied.stderr
        assert os.path.exists(fresh), "a lock expired less than the threshold was removed"
        assert "long expired (>= 7d)" in applied.stdout, applied.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_live_lease_is_never_long_expired():
    mod = _module()
    entries = [{"key": "L", "state": mod.LIVE, "expired_for": None},
               {"key": "U", "state": mod.AMBIGUOUS, "expired_for": None},
               {"key": "O", "state": mod.FOREIGN, "expired_for": 8 * 86400}]
    assert [e["key"] for e in mod.long_expired(entries, 7 * 86400)] == ["O"]


# ----------------------------------------------------------------- R20 token resolution

def the_vault_is_asked_before_the_plaintext_file():
    mod = _module()
    argv = mod.vault_reexec_argv("notion", {}, ["status"], "/py", "/s/agent_sync.py",
                                 "/obs/use_secret.py", "proj", True)
    assert argv == ["/py", "/obs/use_secret.py", "run", "--vault-only", "proj",
                    "AGENT_SYNC_NOTION_TOKEN", "--", "/py", "/s/agent_sync.py", "status"], argv


def no_reexec_when_set_explicitly_already_rerun_no_slot_or_not_notion():
    mod = _module()
    f = mod.vault_reexec_argv
    assert f("notion", {"AGENT_SYNC_NOTION_TOKEN": "x"}, [], "p", "s", "u", "j", True) is None
    assert f("notion", {"AGENT_SYNC_VAULT_REEXEC": "1"}, [], "p", "s", "u", "j", True) is None
    assert f("notion", {}, [], "p", "s", "u", "j", False) is None
    assert f("notion", {}, [], "p", "s", None, "j", True) is None
    assert f("fs", {}, [], "p", "s", "u", "j", True) is None


def a_token_from_the_file_is_reported_as_degraded():
    mod = _module()
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        from pathlib import Path
        open(os.path.join(d, ".env.agent-sync"), "w").write(
            "AGENT_SYNC_BACKEND=fs\n" + "AGENT_SYNC_NOTION_" + "TOKEN=x\n")
        with _env(AGENT_SYNC_NOTION_TOKEN=None, AGENT_SYNC_BACKEND=None, AGENT_SYNC_VAULT_REEXEC=None):
            mod.load_env_file(Path(d))
            assert mod.TOKEN_SOURCE == "file", mod.TOKEN_SOURCE
            os.environ.pop("AGENT_SYNC_NOTION_TOKEN", None)
            os.environ.pop("AGENT_SYNC_BACKEND", None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- R21 conformance map

MAP = os.path.join(REPO, "plugins", "agent-sync", "skills", "agent-sync", "references",
                   "fabric-coordination.md")


def every_command_the_fabric_map_names_exists():
    import re
    text = open(MAP).read()
    assert re.search(r"fabric-agent-contract@[0-9a-f]{7,}", text), "the map carries no contract pin"
    ops = re.findall(r"^\| `(claim|renew|release|reserveId|recordAsBuilt|reconcile)\(.*?\| `([a-z-]+)` \|",
                     text, re.M)
    assert {o for o, _ in ops} == {"claim", "renew", "release", "reserveId", "recordAsBuilt",
                                   "reconcile"}, ops
    out = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True,
                         timeout=60).stdout
    m = re.search(r"\{([a-z,-]+)\}", out)
    assert m, "could not read the subcommand list from --help"
    cli = set(m.group(1).split(","))
    missing = [c for _, c in ops if c not in cli]
    assert not missing, "map names commands the CLI does not have: %s" % missing


# ----------------------------------------------------------------- AS-07 second repository

def a_lease_in_a_second_repository_is_released_from_the_session_project():
    a, b = _repo(), _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        env = dict(os.environ, HOME=home, CLAUDE_PROJECT_DIR=a, AGENT_SYNC_RUN_ID="coordtest-two-repos")
        env.pop("CLAUDE_SESSION_ID", None)
        env.pop("CLAUDE_CODE_SESSION_ID", None)

        def run(cwd, *args):
            return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=cwd, env=env,
                                  capture_output=True, text=True, timeout=120)
        won = run(b, "acquire", "W-1")
        assert won.returncode == 0, won.stdout + won.stderr
        assert _registries(home), "acquiring in a second repository recorded nothing for the session"
        rel = run(a, "release", "--held", "--session-repos")
        assert "W-1" in rel.stdout, rel.stdout + rel.stderr
        who = run(b, "whoami")
        assert "W-1" not in who.stdout.split("leases")[-1] or "none" in who.stdout, who.stdout
        assert not _registries(home), "a repository with nothing held stayed on the session's list"
    finally:
        for d in (a, b, home):
            shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- AS-09 Bash writes

def _bash_writes():
    path = os.path.join(os.path.dirname(SCRIPT), "bash_writes.py")
    spec = importlib.util.spec_from_file_location("bash_writes_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def the_parser_reads_the_write_shapes_agents_use():
    f = _bash_writes().bash_write_targets
    assert f("cat > a.md <<'EOF'", "/r") == ["/r/a.md"]
    assert f("echo x >| clobber.md", "/r") == ["/r/clobber.md"]
    assert f("make &> build.log", "/r") == ["/r/build.log"]
    assert f("make &>> build.log", "/r") == ["/r/build.log"]
    assert f("make >& both.log", "/r") == ["/r/both.log"]
    assert f("echo 'a;b|c' > q.md", "/r") == ["/r/q.md"], "a quoted ; or | split the command"
    assert f("(cd sub; echo y > in.md); echo z > out.md", "/r") == ["/r/sub/in.md", "/r/out.md"]
    assert f("env A=1 sed -i s/a/b/ envd.md", "/r") == ["/r/envd.md"]
    assert f("echo x | sudo tee /etc/hosts", "/r") == ["/etc/hosts"]
    assert f("echo x | sudo -u root tee -a s.md", "/r") == ["/r/s.md"]
    assert f("A=1 nohup cp a.md b.md", "/r") == ["/r/b.md"]
    assert f("cp -t dest a.md b.md", "/r") == ["/r/dest/a.md", "/r/dest/b.md"]
    assert f("echo 2 > two.md", "/r") == ["/r/two.md"], "an argument `2` was read as a descriptor"
    assert f("echo x >> b.md", "/r") == ["/r/b.md"]
    assert f("printf x | tee -a c.md d.md", "/r") == ["/r/c.md", "/r/d.md"]
    assert f("sed -i '' 's/a/b/' e.md", "/r") == ["/r/e.md"]
    assert f("sed -i -e s/a/b/ f.md g.md", "/r") == ["/r/f.md", "/r/g.md"]
    assert f("cd sub && echo y > h.md", "/r") == ["/r/sub/h.md"]
    assert f("mv old.md new.md", "/r") == ["/r/new.md"]


def the_parser_ignores_what_is_not_a_file_write():
    f = _bash_writes().bash_write_targets
    assert f("npm test 2>&1 | tail -5", "/r") == []
    assert f('echo "x | y > z.md"', "/r") == [], "a quoted > was read as a redirect"
    assert f("cat <<< 'x > y.md'", "/r") == []
    assert f("# echo x > c.md\necho y > d.md", "/r") == ["/r/d.md"]
    assert f("ls > /dev/null 2>/dev/null", "/r") == []
    assert f("grep -n x a.md", "/r") == []
    assert f("sed -n 1,5p a.md", "/r") == []


def _run_guard(root, payload, home):
    plugin = os.path.join(REPO, "plugins", "agent-sync")
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=plugin, CLAUDE_PROJECT_DIR=root, HOME=home,
               AGENT_SYNC_RUN_ID="coordtest-guard")
    env.pop("CLAUDE_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run(["bash", os.path.join(plugin, "hooks", "guard.sh")], cwd=root, env=env,
                          input=json.dumps(payload), capture_output=True, text=True, timeout=120)


def a_bash_write_into_a_guarded_file_without_a_lease_is_refused():
    root = _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        cfg = os.path.join(root, ".claude", "agent-sync.json")
        c = json.load(open(cfg))
        c["guardedFiles"] = ["notes.md"]
        json.dump(c, open(cfg, "w"))
        bash = lambda cmd: {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": root}
        r = _run_guard(root, bash("echo x > notes.md"), home)
        assert r.returncode == 2, "a redirect into a guarded file passed (%d): %s" % (r.returncode, r.stderr)
        r = _run_guard(root, bash("sed -i '' 's/a/b/' notes.md"), home)
        assert r.returncode == 2, "sed -i into a guarded file passed: %s" % r.stderr
        r = _run_guard(root, bash("echo x > other.md"), home)
        assert r.returncode == 0, "an unguarded write was refused: %s" % r.stderr
        r = _run_guard(root, bash("npm test 2>&1 | tail -3"), home)
        assert r.returncode == 0, "a command writing nothing was refused: %s" % r.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


# ----------------------------------------------------------------- no temp space, no lockout

def the_guard_needs_no_temp_file_to_allow_a_harmless_command():
    """A full disk turned every Bash call into a refusal, the cleanup included: bash 3.2 writes
    a here-string to a temp file, the parser saw nothing, and the guard failed closed
    (measured 2026-10-12 on the operator's machine). Reproduced with an unwritable TMPDIR."""
    root = _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        plugin = os.path.join(REPO, "plugins", "agent-sync")
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=plugin, CLAUDE_PROJECT_DIR=root, HOME=home,
                   AGENT_SYNC_RUN_ID="coordtest-tmp", TMPDIR="/nonexistent-agent-sync-tmp")
        env.pop("CLAUDE_SESSION_ID", None)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        for payload in ({"tool_name": "Bash", "tool_input": {"command": "df -h"}, "cwd": root},
                        {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(root, "README.md")}}):
            # `ulimit -f 0` makes every file write fail, as a full disk does; TMPDIR alone does
            # not, because bash falls back to /tmp (watched: the old guard passed that version).
            r = subprocess.run(["bash", "-c", 'ulimit -f 0; exec bash "$0"',
                                os.path.join(plugin, "hooks", "guard.sh")], cwd=root, env=env,
                               input=json.dumps(payload), capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, "refused %s with no temp space (%d): %s" % (
                payload["tool_name"], r.returncode, r.stderr[-300:])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


# ----------------------------------------------------------------- fix round (2026-10-12)

PLUGIN = os.path.join(REPO, "plugins", "agent-sync")


def _registries(home):
    """The session lists under a test HOME — `.json` files only (the directory also holds
    the lock the writers share)."""
    d = os.path.join(home, ".agent-sync", "session-repos")
    return sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def _clean_env(**kv):
    env = dict(os.environ)
    for k in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "AGENT_SYNC_RUN_ID",
              "AGENT_SYNC_NOTION_TOKEN", "AGENT_SYNC_VAULT_REEXEC", "AGENT_SYNC_BACKEND",
              "AGENT_SYNC_ENV", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    env.update({k: v for k, v in kv.items() if v is not None})
    return env


def _py(cwd, env, *args, timeout=120):
    return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _fake_observatory(mode):
    """A `project-observatory` on PATH whose use_secret.py answers `where` with 0 and `run`
    with `mode`: "refuse" exits 2 as a missing grant does; "allow" runs the child with a
    token in its environment. Every call is logged, one line each."""
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-obs-")
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "engine", "tools"))
    with open(os.path.join(d, "bin", "project-observatory"), "w") as fh:
        fh.write('#!/bin/sh\necho "$FAKE_OBS_LOG" >/dev/null\nprintf "obs\\n" >> "$FAKE_OBS_LOG"\n'
                 'echo "%s"\n' % os.path.join(d, "engine"))
    os.chmod(os.path.join(d, "bin", "project-observatory"), 0o755)
    with open(os.path.join(d, "engine", "tools", "use_secret.py"), "w") as fh:
        fh.write(
            "import os, subprocess, sys\n"
            "a = sys.argv[1:]\n"
            "open(os.environ['FAKE_OBS_LOG'], 'a').write(' '.join(a) + '\\n')\n"
            "if a[0] == 'where':\n    sys.exit(0)\n"
            "if os.environ.get('FAKE_VAULT_MODE', %r) == 'refuse':\n"
            "    print('use_secret: grant refused for this caller', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "i = a.index('--')\n"
            "env = dict(os.environ, AGENT_SYNC_NOTION_TOKEN='ntn_' + 'fake')\n"
            "sys.exit(subprocess.call(a[i + 1:], env=env))\n" % mode)
    return d


def _notion_repo():
    root = _repo()
    cfg = os.path.join(root, ".claude", "agent-sync.json")
    c = json.load(open(cfg))
    c["backend"] = "notion"
    json.dump(c, open(cfg, "w"))
    with open(os.path.join(root, ".env.agent-sync"), "w") as fh:
        fh.write("AGENT_SYNC_BACKEND=notion\n")
    return root


def a_refusing_vault_never_locks_the_guard():
    """Break 1: `where` answered 0, `run` refused, and every Sync() — the guard's included —
    exec'd into the refusal: exit 2 on every Edit/Write/commit in the repository."""
    root, obs = _notion_repo(), _fake_observatory("refuse")
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    log = os.path.join(obs, "calls.log")
    try:
        env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-vault", FAKE_OBS_LOG=log,
                         PATH=os.path.join(obs, "bin") + os.pathsep + os.environ["PATH"],
                         CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PROJECT_DIR=root)
        r = _py(root, env, "guard", os.path.join(root, "README.md"))
        assert r.returncode == 0, "guard under a refusing vault (%d): %s" % (r.returncode, r.stderr)
        payload = {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(root, "README.md")}}
        g = subprocess.run(["bash", os.path.join(PLUGIN, "hooks", "guard.sh")], cwd=root, env=env,
                           input=json.dumps(payload), capture_output=True, text=True, timeout=120)
        assert g.returncode == 0, "guard.sh under a refusing vault (%d): %s" % (g.returncode, g.stderr)
        calls = open(log).read() if os.path.exists(log) else ""
        assert "run" not in calls.split(), "the guard consulted the vault: %r" % calls
        # A record-plane command asks, is refused by the probe, and falls back — never exec'd.
        res = _py(root, env, "residue")
        assert res.returncode == 0, "residue under a refusing vault (%d): %s" % (res.returncode, res.stderr)
        calls = open(log).read()
        assert calls.count("run --vault-only") == 1, "expected exactly one probe: %r" % calls
    finally:
        for d in (root, obs, home):
            shutil.rmtree(d, ignore_errors=True)


def an_allowing_vault_reruns_with_the_same_argv_and_the_main_worktrees_project():
    """Break 1's other half and break 7's project name: the probe passes, the re-run carries
    this invocation's argv, and the project is the MAIN worktree's folder, not the linked
    worktree's (`_wt/agent-sync-w3` is no vault project)."""
    root, obs = _notion_repo(), _fake_observatory("allow")
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    wt = os.path.join(tempfile.mkdtemp(prefix="agent-sync-coordtest-wt-"), "some-linked-name")
    log = os.path.join(obs, "calls.log")
    try:
        subprocess.run(["git", "-C", root, "worktree", "add", "-q", "-b", "w", wt], check=True,
                       capture_output=True)
        shutil.copy(os.path.join(root, ".env.agent-sync"), wt)
        env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-vault", FAKE_OBS_LOG=log,
                         PATH=os.path.join(obs, "bin") + os.pathsep + os.environ["PATH"])
        res = _py(wt, env, "residue")
        assert res.returncode == 0, res.stdout + res.stderr
        runs = [l for l in open(log).read().splitlines() if l.startswith("run ")]
        assert len(runs) == 2, "expected the probe and the re-run: %r" % runs
        project = runs[-1].split()[2]
        assert project == os.path.basename(root), \
            "vault project %r, expected the main worktree's %r" % (project, os.path.basename(root))
        assert runs[-1].endswith("agent_sync.py residue"), "argv not passed through: %r" % runs[-1]
    finally:
        subprocess.run(["git", "-C", root, "worktree", "remove", "--force", wt], capture_output=True)
        for d in (root, obs, home, os.path.dirname(wt)):
            shutil.rmtree(d, ignore_errors=True)


def a_vault_slot_created_later_is_seen_and_the_engine_path_is_cached():
    """Break 8: a cached "no slot" hid a new slot for an hour while no file token remained,
    and `project-observatory full-path` was spawned by every record-plane command."""
    mod = _module()
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="agent-sync-coordtest-")
    try:
        fake = os.path.join(d, "use_secret.py")
        flag = os.path.join(d, "slot")
        open(fake, "w").write("import os, sys\nsys.exit(0 if os.path.exists(%r) else 2)\n" % flag)
        root = Path(d)
        assert mod._vault_slot_exists(root, fake, "p", file_has_token=False) is False
        open(flag, "w").write("")
        assert mod._vault_slot_exists(root, fake, "p", file_has_token=False) is True, \
            "a negative answer was reused although no file token stands in for the slot"
        os.unlink(flag)
        assert mod._vault_slot_exists(root, fake, "p", file_has_token=False) is True, \
            "a positive answer is the one worth caching"
        # The engine path: the second lookup must not spawn `project-observatory`.
        obs = _fake_observatory("allow")
        log = os.path.join(obs, "calls.log")
        with _env(PATH=os.path.join(obs, "bin") + os.pathsep + os.environ["PATH"], FAKE_OBS_LOG=log):
            a = mod._observatory_use_secret(root)
            b = mod._observatory_use_secret(root)
        assert a and a == b, (a, b)
        assert open(log).read().count("obs") == 1, "full-path asked twice: %r" % open(log).read()
        shutil.rmtree(obs, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _stub_outline():
    """An Outline stand-in that records each request and answers `ok: false` — no retry,
    no sleep: the request is the evidence."""
    import http.server
    import threading
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            seen.append((self.path, body, self.headers.get("Authorization")))
            out = json.dumps({"ok": False, "message": "stub"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def _outline_repo(port, collection, token):
    root = _repo()
    cfg = os.path.join(root, ".claude", "agent-sync.json")
    c = json.load(open(cfg))
    c["backend"] = "outline"
    json.dump(c, open(cfg, "w"))
    with open(os.path.join(root, ".env.agent-sync"), "w") as fh:
        fh.write("AGENT_SYNC_BACKEND=outline\nAGENT_SYNC_OUTLINE_URL=http://127.0.0.1:%d\n"
                 "AGENT_SYNC_OUTLINE_TOKEN=%s\nAGENT_SYNC_OUTLINE_COLLECTION=%s\n"
                 % (port, token, collection))
    return root


def a_second_repository_is_released_with_its_own_record_plane():
    """Break 2: every listed repository ran in ONE process, and `load_env_file` only sets
    absent keys — so repository X was released against P's URL, collection and token."""
    sp, seen_p = _stub_outline()
    sx, seen_x = _stub_outline()
    cp, cx = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    p = _outline_repo(sp.server_port, cp, "tok-p")
    x = _outline_repo(sx.server_port, cx, "tok-x")
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-planes")
        won = _py(x, env, "acquire", "X-1")
        assert won.returncode == 0, won.stdout + won.stderr
        del seen_p[:], seen_x[:]
        rel = _py(p, env, "release", "--held", "--session-repos")
        assert "X-1" in rel.stdout, rel.stdout + rel.stderr
        assert not seen_p, "X's release reached P's record plane: %r" % seen_p
        assert any(cx in body for _, body, _ in seen_x), \
            "X's release never reached X's own collection: %r" % seen_x
        assert all(auth == "Bearer tok-x" for _, _, auth in seen_x), "X used another token"
    finally:
        sp.shutdown()
        sx.shutdown()
        for d in (p, x, home):
            shutil.rmtree(d, ignore_errors=True)


def two_sessions_keep_their_own_lists_of_a_shared_repository():
    """Break 5: the list was keyed by the PROJECT, so the first session's SessionEnd deleted
    it while a second session in the same project still held a lease in Q."""
    p, q = _repo(), _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        a = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-session-a", CLAUDE_PROJECT_DIR=p)
        b = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-session-b", CLAUDE_PROJECT_DIR=p)
        assert _py(q, a, "acquire", "QA-1").returncode == 0
        assert _py(q, b, "acquire", "QB-1").returncode == 0
        assert len(_registries(home)) == 2, _registries(home)
        rel = _py(p, a, "release", "--held", "--session-repos")
        assert "QA-1" in rel.stdout, rel.stdout + rel.stderr
        assert len(_registries(home)) == 1, "A's release took B's list with it: %s" % _registries(home)
        who = _py(q, b, "whoami")
        assert "QB-1" in who.stdout, "B's lease in Q went with A's session: " + who.stdout
        rel = _py(p, b, "release", "--held", "--session-repos")
        assert "QB-1" in rel.stdout, rel.stdout + rel.stderr
        assert not _registries(home), _registries(home)
    finally:
        for d in (p, q, home):
            shutil.rmtree(d, ignore_errors=True)


def a_bash_acquire_in_a_second_repository_is_released_by_the_real_session_end_hook():
    """AS-07 end to end, the way Claude Code runs it: the Bash call carries
    CLAUDE_CODE_SESSION_ID and no CLAUDE_PROJECT_DIR; the SessionEnd hook carries neither
    variable and the session id in its payload. 1.22.0's first cut registered nothing (it
    keyed on CLAUDE_PROJECT_DIR, which a Bash call does not have)."""
    p, q = _repo(), _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    sid = "coordtest-0000-4000-8000-000000000001"
    try:
        shell = _clean_env(HOME=home, CLAUDE_CODE_SESSION_ID=sid)
        won = _py(q, shell, "acquire", "BASH-1")
        assert won.returncode == 0, won.stdout + won.stderr
        assert _registries(home), "a Bash-call acquire in a second repository registered nothing"
        hook = _clean_env(HOME=home, CLAUDE_PLUGIN_ROOT=PLUGIN, CLAUDE_PROJECT_DIR=p)
        r = subprocess.run(["bash", os.path.join(PLUGIN, "hooks", "session-end.sh")], cwd=p,
                           env=hook, input=json.dumps({"session_id": sid,
                                                       "hook_event_name": "SessionEnd"}),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        left = [f for f in os.listdir(os.path.join(q, ".agent-sync", "leases"))
                if f.endswith(".lock")]
        assert not left, "SessionEnd left the second repository's lease: %s" % left
        assert not _registries(home), _registries(home)
    finally:
        for d in (p, q, home):
            shutil.rmtree(d, ignore_errors=True)


def a_listed_repository_that_cannot_be_acted_on_never_strands_the_rest():
    p, q = _repo(), _repo()
    plain = tempfile.mkdtemp(prefix="agent-sync-coordtest-plain-")
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="coordtest-strand")
        assert _py(q, env, "acquire", "S-1").returncode == 0
        reg = os.path.join(home, ".agent-sync", "session-repos", _registries(home)[0])
        listed = json.load(open(reg))
        json.dump(listed + [plain, os.path.join(plain, "gone")], open(reg, "w"))
        rel = _py(p, env, "release", "--held", "--session-repos")
        assert "S-1" in rel.stdout, rel.stdout + rel.stderr
        assert not _registries(home), "unusable entries stayed listed: %s" % open(reg).read()
    finally:
        for d in (p, q, plain, home):
            shutil.rmtree(d, ignore_errors=True)


def the_session_list_name_agrees_between_bash_and_python():
    """Break 5's unit risk: the slug was the path with unsafe characters made `_`, cut at
    200 in Python and not in bash, and per CHARACTER in Python but per BYTE in bash under
    LC_ALL=C — a long or Cyrillic key named two different files. Now sha256 of the bytes."""
    mod = _module()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        for run in ("x" * 300, "задача-координация-" + "я" * 120, "plain"):
            with _env(HOME=home, AGENT_SYNC_RUN_ID=run, CLAUDE_SESSION_ID=None,
                      CLAUDE_CODE_SESSION_ID=None):
                f = mod.session_repos_file(mod.session_registry_key())
            f = os.path.join(home, ".agent-sync", "session-repos", os.path.basename(str(f)))
            os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f, "w").write("[]")
            for locale in ("C", "en_US.UTF-8"):
                env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID=run, LC_ALL=locale,
                                 CLAUDE_PLUGIN_ROOT=PLUGIN)
                r = subprocess.run(["bash", "-c", '. "$CLAUDE_PLUGIN_ROOT/hooks/_lib.sh"; '
                                    "agent_sync_has_session_repos"], env=env,
                                   capture_output=True, text=True, timeout=30)
                assert r.returncode == 0, "bash did not find %r's list under LC_ALL=%s" % (run[:20], locale)
            os.unlink(f)
        env = _clean_env(HOME=home, AGENT_SYNC_RUN_ID="nobody-listed", CLAUDE_PLUGIN_ROOT=PLUGIN)
        r = subprocess.run(["bash", "-c", '. "$CLAUDE_PLUGIN_ROOT/hooks/_lib.sh"; '
                            "agent_sync_has_session_repos"], env=env, capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 1, "a session with no list was found to have one"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def a_heredoc_body_is_never_read_as_commands():
    """Break 3: the parser split on newlines BEFORE tokenising, so a heredoc's text became
    commands and a harmless write to notes.md was refused for naming a guarded file."""
    f = _bash_writes().bash_write_targets
    cmd = "cat > notes.md <<'EOF'\n> docs/ROADMAP.md\ncp x docs/DECISIONS.md\nEOF"
    assert f(cmd, "/r") == ["/r/notes.md"], f(cmd, "/r")
    assert f("cat <<-EOF > t.md\n\tcp a docs/X.md\n\tEOF\necho done > after.md", "/r") == \
        ["/r/t.md", "/r/after.md"]
    root = _repo()
    home = tempfile.mkdtemp(prefix="agent-sync-coordtest-home-")
    try:
        cfg = os.path.join(root, ".claude", "agent-sync.json")
        c = json.load(open(cfg))
        c["guardedFiles"] = ["docs/ROADMAP.md"]
        json.dump(c, open(cfg, "w"))
        os.makedirs(os.path.join(root, "docs"))
        open(os.path.join(root, "docs", "ROADMAP.md"), "w").write("x\n")
        r = _run_guard(root, {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": root}, home)
        assert r.returncode == 0, "a heredoc mentioning a guarded file was refused: " + r.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


def what_the_parser_cannot_resolve_it_skips():
    f = _bash_writes().bash_write_targets
    assert f("echo x > $OUT", "/r") == []
    assert f("echo x > `pick`", "/r") == []
    assert f("echo x > *.md", "/r") == []
    assert f("cd - && echo y > lost.md", "/r") == [], "followed `cd -` to a directory it cannot know"
    assert f("cd $D && echo y > lost.md", "/r") == []
    assert f("cd - && echo y > /abs/kept.md", "/r") == ["/abs/kept.md"]
    assert f("echo 'unclosed > x", "/r") == []


def the_config_key_for_the_scheduled_interval_passes_check():
    """Break 4: `status` read `scheduledIntervalSeconds`, and `check` called it unknown."""
    root = _repo()
    try:
        cfg = os.path.join(root, ".claude", "agent-sync.json")
        c = json.load(open(cfg))
        c["scheduledIntervalSeconds"] = 3600
        json.dump(c, open(cfg, "w"))
        out = _cli(root, "check")
        assert "not in the schema" not in out.stdout, out.stdout
        schema = json.load(open(os.path.join(REPO, "agent-sync.schema.json")))["properties"]
        assert schema.get("scheduledIntervalSeconds", {}).get("type") == "integer", schema.get(
            "scheduledIntervalSeconds")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_sweep_by_age_shows_each_verdict_and_refuses_a_zero_threshold():
    """Break 6: `--older-than-days 0` silently became 7, and the listing hid the
    classifier's verdict an operator needs before deciding."""
    mod = _module()
    root = _repo()
    try:
        _lock(root, mod, "OLD-2", "r-deadrun", "2026-08-01T00:00:00Z")
        zero = _cli(root, "reap", "--long-expired", "--older-than-days", "0")
        assert zero.returncode == 2 and "refused" in zero.stderr, (zero.returncode, zero.stderr)
        stray = _cli(root, "reap", "--apply")
        assert stray.returncode == 2, "--apply without --long-expired was ignored silently"
        listed = _cli(root, "reap", "--long-expired")
        assert "verdict foreign" in listed.stdout, listed.stdout
        assert "OPERATOR" in listed.stdout, listed.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def a_keyless_lease_ref_on_the_remote_is_flagged():
    """Break 9: AS-14 was dropped on the premise that no ≤1.21.2 writer is alive, and the
    Fabric remote carried a keyless ref written 2026-10-08. The revisit condition is now a
    check: `residue` names such a ref."""
    bare = tempfile.mkdtemp(prefix="agent-sync-coordtest-bare-")
    root = _repo()
    try:
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        cfg = os.path.join(root, ".claude", "agent-sync.json")
        c = json.load(open(cfg))
        c.update({"leaseBackend": "git", "leaseRemote": "origin"})
        json.dump(c, open(cfg, "w"))
        subprocess.run(["git", "-C", root, "remote", "add", "origin", bare], check=True)
        tree = subprocess.run(["git", "-C", root, "hash-object", "-t", "tree", os.devnull],
                              capture_output=True, text=True, check=True).stdout.strip()
        body = json.dumps({"run": "r-old", "ts": "2026-10-08T22:24:36Z", "ttl": 2700,
                           "repo": "x", "host": "elsewhere.example"})
        sha = subprocess.run(["git", "-C", root, "commit-tree", tree, "-m", body],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", root, "push", "-q", "origin",
                        sha + ":refs/agent-sync/leases/docs-x.md"], check=True, capture_output=True)
        out = _cli(root, "residue")
        assert "carry no `key`" in out.stdout and "docs-x.md" in out.stdout, out.stdout + out.stderr
    finally:
        shutil.rmtree(bare, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)


def a_bash_call_and_its_hook_resolve_one_session_in_an_unstamped_repository():
    mod = _module()
    with _env(AGENT_SYNC_RUN_ID=None, CLAUDE_SESSION_ID=None, CLAUDE_CODE_SESSION_ID="abc-123"):
        assert mod._session_key()[0] == "session:abc-123", mod._session_key()


CASES = [
    a_refusing_vault_never_locks_the_guard,
    an_allowing_vault_reruns_with_the_same_argv_and_the_main_worktrees_project,
    a_vault_slot_created_later_is_seen_and_the_engine_path_is_cached,
    a_second_repository_is_released_with_its_own_record_plane,
    two_sessions_keep_their_own_lists_of_a_shared_repository,
    a_bash_acquire_in_a_second_repository_is_released_by_the_real_session_end_hook,
    a_listed_repository_that_cannot_be_acted_on_never_strands_the_rest,
    the_session_list_name_agrees_between_bash_and_python,
    a_bash_call_and_its_hook_resolve_one_session_in_an_unstamped_repository,
    a_heredoc_body_is_never_read_as_commands,
    what_the_parser_cannot_resolve_it_skips,
    the_config_key_for_the_scheduled_interval_passes_check,
    a_sweep_by_age_shows_each_verdict_and_refuses_a_zero_threshold,
    a_keyless_lease_ref_on_the_remote_is_flagged,
    the_guard_needs_no_temp_file_to_allow_a_harmless_command,
    the_parser_reads_the_write_shapes_agents_use,
    the_parser_ignores_what_is_not_a_file_write,
    a_bash_write_into_a_guarded_file_without_a_lease_is_refused,
    a_lease_in_a_second_repository_is_released_from_the_session_project,
    every_command_the_fabric_map_names_exists,
    the_vault_is_asked_before_the_plaintext_file,
    no_reexec_when_set_explicitly_already_rerun_no_slot_or_not_notion,
    a_token_from_the_file_is_reported_as_degraded,
    a_lease_held_past_the_scheduled_interval_is_reported,
    no_interval_means_nothing_to_report,
    a_long_expired_foreign_lock_is_listed_then_cleared_only_with_apply,
    a_live_lease_is_never_long_expired,
    a_three_digit_register_does_not_leak_ids_it_holds,
    an_id_really_missing_is_still_a_leak,
    a_reserved_id_is_printed_at_the_registers_width,
    two_keys_sharing_a_prefix_are_two_runs,
    a_short_key_keeps_its_readable_id,
    the_same_key_is_the_same_run_every_time,
]


def main():
    print("== coordination_test.py (%s)" % SCRIPT)
    for fn in CASES:
        case(fn.__name__.replace("_", " "), fn)
    if failures:
        print("FAIL: %d of %d case(s)" % (len(failures), cases))
        return 1
    print("PASS: %d case(s)" % cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
