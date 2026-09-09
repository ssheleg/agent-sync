#!/usr/bin/env python3
"""FIX-SY-06.02 — capability docs derive from one contract; unavailable is
unknown (sherlock audit, SY-06, second leaf).

The finding: SKILL.md, backend-fs.md and adapter-contract.md each described the
mode semantics in their own words and could contradict each other on one mode.

The fix under test:
* adapter-contract.md carries the ONE status capability contract — the five
  fields Sync.capabilities() reports (lease_scope, enforcement_mode,
  awareness_scope, identity_strength, backend_health);
* SKILL.md and backend-fs.md POINT at that section rather than restating it;
* an unavailable observation is unknown/failed, never asserted active;
* the documented field names match the code's capabilities() exactly.

Standard library only.
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKILL = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync", "SKILL.md")
FS = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync",
                  "references", "backend-fs.md")
CONTRACT = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync",
                        "references", "adapter-contract.md")
SCRIPT = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync",
                      "scripts", "agent_sync.py")

failures = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def flat(path):
    with open(path, encoding="utf-8") as fh:
        return " ".join(fh.read().split())


CANON_FIELDS = ["lease_scope", "enforcement_mode", "awareness_scope",
                "identity_strength", "backend_health"]


def t_contract_has_the_five_fields():
    d = flat(CONTRACT)
    assert "The status capability contract — one source, five fields" in d
    for f in CANON_FIELDS:
        assert f in d, f"the contract omits {f}"
    assert "ONE source every doc describes a mode from" in d


def t_fields_match_the_code():
    """The documented field names are exactly what capabilities() returns."""
    spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # build a Sync in a throwaway project to read the field set
    d = tempfile.mkdtemp()
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    os.makedirs(os.path.join(d, ".claude"))
    import json
    json.dump({"backend": "fs", "gated": True, "leaseBackend": "local",
               "leaseTtlSeconds": 600, "renewIntervalSeconds": 60, "idRegisters": {},
               "guardedFiles": [], "claimTags": {}, "gates": [],
               "mirror": {"enabled": False, "sources": []}},
              open(os.path.join(d, ".claude", "agent-sync.json"), "w"))
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "s"], check=True)
    os.chdir(d)
    caps = m.Sync().capabilities()
    assert set(caps.keys()) == set(CANON_FIELDS), \
        f"the code's fields {set(caps.keys())} differ from the documented contract"


def t_skill_and_fs_point_at_contract():
    s = flat(SKILL)
    assert "references/adapter-contract.md` → *The status capability contract*" in s
    assert "never restated here" in s, "SKILL does not say it never restates the contract"
    fs = flat(FS)
    assert "adapter-contract.md` → *The status capability\n   contract*".replace("\n   ", " ") in fs \
        or "the one source this file describes the mode from" in fs


def t_unavailable_is_unknown():
    d = flat(CONTRACT)
    assert "An unavailable observation is `unknown`/`failed`, never asserted" in d
    fs = flat(FS)
    assert "reported as `unknown`,\n   never as active green".replace("\n   ", " ") in fs \
        or "never as active green" in fs


def t_gated_is_derived_in_docs():
    d = flat(CONTRACT)
    assert "Legacy `gated` is a DERIVED summary" in d


def main():
    case("the adapter contract carries the five-field status contract",
         t_contract_has_the_five_fields)
    case("the documented fields match capabilities() exactly", t_fields_match_the_code)
    case("SKILL.md and backend-fs.md point at the one contract",
         t_skill_and_fs_point_at_contract)
    case("an unavailable observation is unknown/failed, never active", t_unavailable_is_unknown)
    case("gated is documented as a derived summary", t_gated_is_derived_in_docs)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
