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
        assert "long expired (> 7d)" in applied.stdout, applied.stdout
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
            "AGENT_SYNC_BACKEND=fs\nAGENT_SYNC_NOTION_TOKEN=placeholder-not-a-secret\n")
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


CASES = [
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
