#!/usr/bin/env python3
"""FIX-SY-06.01 — capability fields separated (sherlock audit, SY-06).

The finding: one boolean `gated` answered three different questions — is the
lease enforced, is it cross-machine, is there shared awareness — and the
adapter contract tied it to the record plane's capabilities on top. An
advisory host could read as enforced; a failed backend could read as active.

The fix under test (agent_sync.py):
* capabilities() returns five SEPARATE fields — lease_scope, enforcement_mode,
  awareness_scope, identity_strength, backend_health;
* an advisory (local) host reports enforcement_mode 'advisory', never
  'enforced';
* a failed backend collapses enforcement to advisory and is never
  active/green;
* legacy `gated` is a compatibility SUMMARY derived from enforcement_mode.

Standard library only; real project dirs.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, os.pardir, os.pardir, "plugins", "agent-sync", "skills",
                      "agent-sync", "scripts", "agent_sync.py")

_spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

failures = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def project(lease_backend="local", gated=True):
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    cfg = {"backend": "fs", "gated": gated, "leaseBackend": lease_backend,
           "leaseTtlSeconds": 600, "renewIntervalSeconds": 60, "idRegisters": {},
           "guardedFiles": [], "claimTags": {}, "gates": [],
           "mirror": {"enabled": False, "sources": []}}
    with open(os.path.join(d, ".claude", "agent-sync.json"), "w") as fh:
        json.dump(cfg, fh)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "seed"], check=True)
    return d


def sync_in(d):
    os.chdir(d)
    return A.Sync()


def t_five_fields_present():
    caps = sync_in(project()).capabilities()
    for f in ("lease_scope", "enforcement_mode", "awareness_scope",
              "identity_strength", "backend_health"):
        assert f in caps, f"capability field {f} missing"


def t_advisory_local_not_enforced():
    caps = sync_in(project(lease_backend="local")).capabilities()
    assert caps["lease_scope"] == "machine-local"
    assert caps["enforcement_mode"] == "advisory", \
        "a local advisory host reported enforced — the finding"
    assert caps["identity_strength"] == "weak"


def t_cross_machine_enforced():
    caps = sync_in(project(lease_backend="git")).capabilities()
    assert caps["lease_scope"] == "cross-machine"
    assert caps["enforcement_mode"] == "enforced"
    assert caps["identity_strength"] == "strong"


def t_failed_backend_not_green():
    s = sync_in(project(lease_backend="git"))
    # force the health probe to fail
    def boom():
        raise RuntimeError("backend unreachable")
    s.adapter.preflight = boom
    caps = s.capabilities()
    assert caps["backend_health"] == "failed", "a failed backend reported up"
    assert caps["enforcement_mode"] == "advisory", \
        "a failed backend still reported enforced — active green on a dead backend"
    assert caps["identity_strength"] == "weak"


def t_gated_is_derived_summary():
    enforced = sync_in(project(lease_backend="git"))
    assert enforced.gated is True, "an enforced host is not gated"
    advisory = sync_in(project(lease_backend="local"))
    assert advisory.gated is False, "an advisory host still summarised as gated"
    # gated tracks enforcement_mode exactly
    assert enforced.gated == (enforced.capabilities()["enforcement_mode"] == "enforced")
    assert advisory.gated == (advisory.capabilities()["enforcement_mode"] == "enforced")


def t_gated_false_when_config_off():
    caps = sync_in(project(lease_backend="git", gated=False)).capabilities()
    assert caps["enforcement_mode"] == "advisory", \
        "gated:false in config did not collapse enforcement"


def main():
    case("capabilities() returns five separate fields", t_five_fields_present)
    case("an advisory local host is not enforced", t_advisory_local_not_enforced)
    case("a cross-machine host is enforced and strong", t_cross_machine_enforced)
    case("a failed backend is never active/green", t_failed_backend_not_green)
    case("legacy gated is a derived compatibility summary", t_gated_is_derived_summary)
    case("gated:false in config collapses enforcement", t_gated_false_when_config_off)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
