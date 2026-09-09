#!/usr/bin/env python3
"""FIX-SY-08.01 — pipeline detection across every host layout (sherlock audit,
SY-08).

The finding: pipeline_installed checked only ~/.claude/plugins/cache and the
hub / ~/.claude/skills — the native ~/.codex/plugins/cache was not covered, so
a copy under one host could mask the dependency's absence under the host you
run, and a real Codex copy could be missed.

The fix under test (scripts/agent_sync.py + SKILL.md):
* pipeline_installed(home, cfg) is injectable and searches every host layout
  (Claude/Codex/Gemini caches + plain dirs + the hub);
* an explicit cfg pipelinePath wins over discovery;
* synthetic HOMEs — only Codex cache / only Claude cache / only direct hub /
  fully absent — resolve correctly, with no side effects and no install;
* the low-level lease ops are documented as independent of the binding.

Standard library only.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync",
                      "scripts", "agent_sync.py")
SKILL = os.path.join(ROOT, "plugins", "agent-sync", "skills", "agent-sync", "SKILL.md")

_spec = importlib.util.spec_from_file_location("agent_sync", SCRIPT)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

failures = []


def case(name, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except AssertionError as e:
        failures.append(f"{name}: {e}")
        print(f"FAIL  {name}: {e}")


def home_with(layout):
    h = tempfile.mkdtemp()
    if layout == "codex":
        d = os.path.join(h, ".codex", "plugins", "cache", "task-pipeline", "1.0",
                         "skills", "task-pipeline")
    elif layout == "claude":
        d = os.path.join(h, ".claude", "plugins", "cache", "task-pipeline", "1.0",
                         "skills", "task-pipeline")
    elif layout == "hub":
        d = os.path.join(h, ".agents", "skills", "task-pipeline")
    elif layout == "codex-plain":
        d = os.path.join(h, ".codex", "skills", "task-pipeline")
    else:
        return Path(h)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as fh:
        fh.write("x")
    return Path(h)


def t_only_codex_cache_detected():
    assert M.pipeline_installed(home=home_with("codex")) is True, \
        "a Codex-only install was reported absent — the finding"


def t_only_claude_cache_detected():
    assert M.pipeline_installed(home=home_with("claude")) is True


def t_only_hub_and_codex_plain_detected():
    assert M.pipeline_installed(home=home_with("hub")) is True
    assert M.pipeline_installed(home=home_with("codex-plain")) is True


def t_fully_absent_is_false():
    assert M.pipeline_installed(home=home_with("absent")) is False


def t_explicit_path_wins():
    h = tempfile.mkdtemp()
    tp = os.path.join(h, "custom", "SKILL.md")
    os.makedirs(os.path.dirname(tp))
    open(tp, "w").write("x")
    assert M.pipeline_installed(home=Path(h), cfg={"pipelinePath": "custom/SKILL.md"}) is True
    # an explicit path that does NOT exist is honestly absent, not overridden by discovery
    hub_home = home_with("hub")
    assert M.pipeline_installed(home=hub_home, cfg={"pipelinePath": "nope/SKILL.md"}) is False, \
        "an explicit missing path was overruled by a filesystem guess"


def t_no_side_effects():
    h = home_with("absent")
    before = set(os.listdir(h))
    M.pipeline_installed(home=h)
    assert set(os.listdir(h)) == before, "the detector wrote to the home"


def t_skill_documents_hostwide_and_optional_core():
    with open(SKILL, encoding="utf-8") as fh:
        d = " ".join(fh.read().split())
    assert "checked across every host layout" in d
    assert "pipelinePath" in d
    assert "The lease core (`acquire`/`renew`/`release`) needs a backend + lease, not the binding." in d


def main():
    case("a Codex-only cache is detected", t_only_codex_cache_detected)
    case("a Claude-only cache is detected", t_only_claude_cache_detected)
    case("the hub and a Codex plain dir are detected", t_only_hub_and_codex_plain_detected)
    case("a fully absent dependency is false", t_fully_absent_is_false)
    case("an explicit pipelinePath wins over discovery", t_explicit_path_wins)
    case("the detector has no side effects", t_no_side_effects)
    case("SKILL documents host-wide detection and the optional core",
         t_skill_documents_hostwide_and_optional_core)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
