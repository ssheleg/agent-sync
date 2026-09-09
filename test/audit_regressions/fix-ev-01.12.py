#!/usr/bin/env python3
"""FIX-EV-01.09 — the outcome corpus for agent-sync (sherlock audit, parent
FIX-EV-01; depends on the family harness of FIX-EV-01.01).

The corpus (evals/cases/agent-sync.json) holds a positive (a receipt per
claim), a negative (routing), an unsupported-claim case (prove "docs are in
sync" by an exit code or mark it unsupported), a chat-answer no-op, and the
ED-01 case (an unresolvable dependency prints capability unavailable, never
"all links resolve") — judged on ARTIFACTS through the family's outcome-case
contract, so agent-sync can no longer pass an eval by its name being picked.

Standard library only.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CASES = os.path.join(ROOT, "evals", "cases", "agent-sync.json")
HARNESS = os.path.expanduser("~/DATA/sshlg-skills/test/outcome_harness.py")

failures = []
not_run = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def manifest():
    with open(CASES, encoding="utf-8") as fh:
        return json.load(fh)


def t_cases_are_structurally_valid():
    m = manifest()
    ids = [c["id"] for c in m["cases"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 5
    for c in m["cases"]:
        assert c["schema_version"] == "outcome-case/1"
        assert c["skill"] == "agent-sync"
        assert c["environment"]["case_digest"] == \
            hashlib.sha256(c["prompt"]["text"].encode()).hexdigest(), \
            f"{c['id']}: case_digest does not pin the frozen prompt"
        assert c["checks"]["outcome"], f"{c['id']}: no outcome checks"


def t_negative_and_noop_forbid_loading():
    m = manifest()
    for cid in ("AS-OUT-002-negative-routing", "AS-OUT-004-noop-single-agent"):
        c = next(x for x in m["cases"] if x["id"] == cid)
        assert "agent-sync" in c["checks"]["load_trace"]["expect_not_loaded"], \
            f"{cid} does not forbid the skill from loading"


def t_exclusive_acquire_and_resource_and_git():
    m = manifest()
    acq = next(c for c in m["cases"] if "exclusive-acquire" in c["id"])
    assert any((o.get("expect") or "") == "exactly one" for o in acq["checks"]["outcome"]), \
        "the acquire case does not pin exactly-one-winner (SY-05)"
    res = next(c for c in m["cases"] if "resource-claim" in c["id"])
    assert any((o.get("expect") or "") == "resource claim" for o in res["checks"]["outcome"]), \
        "the resource-claim case does not pin the file guard (SY-04)"
    git = next(c for c in m["cases"] if "git-lease" in c["id"])
    assert "git --version" in git["checks"]["tool"][0]["command"], \
        "the git-lease case has no probe"
    flat = " ".join(json.dumps(m, ensure_ascii=False).split())
    for needle in ("actual output oracle", "raw result", "with/without-skill",
                   "NOT_RUN", "grader convenience"):
        assert needle in flat, f"the manifest no longer records {needle!r}"


def t_family_harness_validates_each_case_where_present():
    if not os.path.isfile(HARNESS):
        not_run.append("family harness absent — case validation NOT_RUN (never PASS)")
        return
    for c in manifest()["cases"]:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(c, fh)
            path = fh.name
        try:
            r = subprocess.run([sys.executable, HARNESS, path],
                               capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, f"{c['id']} rejected:\n{r.stdout}"
        finally:
            os.unlink(path)


def main():
    case("every case is structurally valid, none is name-picking",
         t_cases_are_structurally_valid)
    case("the negative and single-agent chat cases forbid loading",
         t_negative_and_noop_forbid_loading)
    case("the acquire/resource/git oracles are pinned",
         t_exclusive_acquire_and_resource_and_git)
    case("the family harness validates each case (where present)",
         t_family_harness_validates_each_case_where_present)
    for n in not_run:
        print(f"  NOT_RUN  {n}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
