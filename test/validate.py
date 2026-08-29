#!/usr/bin/env python3
"""agent-sync repository validator. Stdlib only.

Checks the Agent Skills spec floor, this repo's house rules, and the two rules
that exist because breaking them ships a secret: no host identity and no
credential in anything that gets published.

Run:  python3 test/validate.py
      python3 test/validate.py --self-test   # corrupt a copy, expect failure
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Hosts a published file may legitimately name. Anything else is treated as an
# instance address that leaked out of someone's environment.
ALLOWED_HOSTS = {
    "github.com", "raw.githubusercontent.com", "www.github.com",
    "code.claude.com", "docs.claude.com", "agentskills.io", "www.agentskills.io",
    "www.getoutline.com", "getoutline.com", "app.getoutline.com",
    # Notion is single-tenant SaaS: `api.notion.com` is a constant every client hard-codes,
    # not somebody's private address. Outline is self-hostable, which is why ITS instance
    # address stays in the environment and only the vendor's own site is listed here.
    "api.notion.com", "developers.notion.com", "www.notion.so",
    "json-schema.org", "npmjs.com", "www.npmjs.com", "img.shields.io",
    "x.com", "sshlg.me", "t.me",
    "localhost", "127.0.0.1", "example.com", "wiki.example.com",
}

PUBLISHED = ["plugins", "bin", "test", "agent-sync.example.json", "agent-sync.schema.json"]
SCRIPT_PATH = "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"

errors: list[str] = []
notes: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ------------------------------------------------------------------ front matter

def parse_front_matter(path: Path) -> dict[str, object]:
    text = path.read_text()
    if not text.startswith("---\n"):
        err(f"{rel(path)}: missing YAML front matter")
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        err(f"{rel(path)}: unterminated front matter")
        return {}
    block = text[4:end]
    out: dict[str, object] = {}
    key = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and key:
            sub = line.strip()
            if ":" in sub:
                k, v = sub.split(":", 1)
                out.setdefault(key, {})
                if isinstance(out[key], dict):
                    out[key][k.strip()] = v.strip().strip('"').strip("'")  # type: ignore[index]
            continue
        if ":" not in line:
            err(f"{rel(path)}: unparseable front-matter line: {line!r}")
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        v = v.strip()
        out[key] = v.strip('"') if v else {}
    return out


def check_skill(skill_dir: Path) -> str | None:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        err(f"{rel(skill_dir)}: no SKILL.md")
        return None
    fm = parse_front_matter(md)

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        err(f"{rel(md)}: name missing")
    else:
        if name != skill_dir.name:
            err(f"{rel(md)}: name '{name}' != directory '{skill_dir.name}'")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            err(f"{rel(md)}: name '{name}' must be lowercase a-z0-9 with single hyphens")
        if len(name) > 64:
            err(f"{rel(md)}: name longer than 64 chars")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc:
        err(f"{rel(md)}: description missing")
    else:
        if len(desc) > 1024:
            err(f"{rel(md)}: description is {len(desc)} chars, cap is 1024")
        if not desc.startswith("Use when"):
            err(f"{rel(md)}: description must start with 'Use when'")
        if not re.search(r"[а-яА-ЯёЁ]", desc):
            err(f"{rel(md)}: description has no Russian trigger phrases")
        if not re.search(r"[a-zA-Z]", desc):
            err(f"{rel(md)}: description has no English trigger phrases")

    compat = fm.get("compatibility")
    if isinstance(compat, str) and len(compat) > 500:
        err(f"{rel(md)}: compatibility is {len(compat)} chars, cap is 500")

    meta = fm.get("metadata")
    if isinstance(meta, dict):
        for k, v in meta.items():
            if not isinstance(v, str):
                err(f"{rel(md)}: metadata.{k} must be a string")

    allowed = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    for k in fm:
        if k not in allowed:
            err(f"{rel(md)}: unknown front-matter key '{k}'")

    body = md.read_text().split("\n---", 1)[-1]
    lines = len(body.strip().splitlines())
    if lines >= 500:
        err(f"{rel(md)}: body is {lines} lines, budget is < 500")
    # 3.9 chars/token, not 4, and the divisor is not a preference. make-skill's shipped
    # auditor — the family's authority on skill budgets — measures 3.9 (`CHARS_PER_TOKEN`
    # in `scripts/audit_skill.py`, tokenised against real bundles at 3.78-4.47). This file
    # divided by 4 and passed this body at 4957 tokens while that auditor refused it at
    # 5084: two verdicts on one budget, and the permissive one was the local one.
    est = int(len(body) / 3.9)
    if est > 5000:
        err(f"{rel(md)}: body is ~{est} tokens ({len(body)} chars / 3.9), budget is < 5000")

    # references / scripts: one level deep, each with a stated load trigger
    for sub in ("references", "scripts", "assets"):
        d = skill_dir / sub
        if not d.exists():
            continue
        for f in d.rglob("*"):
            # Bytecode is a local artefact of importing the module, not a shipped file.
            # The rule's purpose — never publish junk — is enforced by .npmignore, and
            # that guarantee is asserted below rather than waived here.
            if "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            if f.is_file() and f.parent != d:
                err(f"{rel(f)}: must be one level deep under {sub}/")
    refs = skill_dir / "references"
    if refs.exists():
        body_text = md.read_text()
        for f in sorted(refs.glob("*.md")):
            if f"references/{f.name}" not in body_text:
                err(f"{rel(md)}: references/{f.name} ships but the body never says when to read it")

    for m in re.finditer(r"\]\((\.\.?/[^)]+)\)", md.read_text()):
        target = (skill_dir / m.group(1)).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            err(f"{rel(md)}: relative link escapes the skill directory: {m.group(1)}")
        elif not target.exists():
            err(f"{rel(md)}: relative link does not resolve: {m.group(1)}")

    if isinstance(meta, dict):
        v = meta.get("version")
        return v if isinstance(v, str) else None
    return None


# ------------------------------------------------------------------ repo rules

def check_no_stray_skills() -> None:
    for md in ROOT.rglob("SKILL.md"):
        if ".git" in md.parts:
            continue
        parts = md.relative_to(ROOT).parts
        legal = (len(parts) == 5 and parts[0] == "plugins"
                 and parts[2] == "skills" and parts[4] == "SKILL.md")
        if not legal:
            err(f"{rel(md)}: a SKILL.md outside plugins/*/skills/*/ ships as a real skill")


def published_files() -> list[Path]:
    out: list[Path] = []
    for entry in PUBLISHED:
        p = ROOT / entry
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file()]
    return out


def check_no_host_identity() -> None:
    url_re = re.compile(r"https?://([A-Za-z0-9._-]+)")
    for f in published_files():
        if f.suffix in {".png", ".jpg", ".gz"}:
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for host in set(url_re.findall(text)):
            bare = host.lower()
            if bare in ALLOWED_HOSTS:
                continue
            if bare.startswith("<") or "your-instance" in bare or "instance" == bare:
                continue
            err(f"{rel(f)}: names host '{host}' — a published file must not carry an "
                f"instance address; put it in the environment")


def check_no_credentials() -> None:
    """A credential in argv is readable by every process on the machine."""
    header_bearer = re.compile(r"-H[ \t]+[\"'][^\"']*Bearer", re.I)
    long_secret = re.compile(r"(?:token|secret|api[_-]?key)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{24,}", re.I)
    for f in published_files():
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if header_bearer.search(line) and "--config" not in text:
                err(f"{rel(f)}:{i}: Bearer token passed via -H puts the credential in argv")
            if long_secret.search(line):
                err(f"{rel(f)}:{i}: looks like a hardcoded credential")


def check_version_sync() -> tuple[bool, str]:
    versions: dict[str, str] = {}
    mk = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    versions["marketplace.json"] = mk["plugins"][0]["version"]
    pj = json.loads((ROOT / "plugins" / "agent-sync" / ".claude-plugin" / "plugin.json").read_text())
    versions["plugin.json"] = pj["version"]
    versions["package.json"] = json.loads((ROOT / "package.json").read_text())["version"]

    changelog = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(r"^##\s*\[?v?(\d+\.\d+\.\d+)\]?", changelog, re.M)
    versions["CHANGELOG.md"] = m.group(1) if m else "MISSING"

    # The number the tool prints about itself. Left out of this check until 1.2.4, it
    # drifted a release behind and every `status` header reported the wrong version —
    # the exact number the README tells an operator to compare when hunting a stale
    # install channel.
    script = (ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py").read_text()
    m = re.search(r'^VERSION\s*=\s*"(\d+\.\d+\.\d+)"', script, re.M)
    versions["agent_sync.py"] = m.group(1) if m else "MISSING"

    for skill in sorted((ROOT / "plugins" / "agent-sync" / "skills").glob("*")):
        if skill.is_dir():
            v = check_skill(skill)
            versions[f"{skill.name}/SKILL.md"] = v or "MISSING"

    distinct = set(versions.values())
    if len(distinct) != 1:
        err(f"version sync broken: {versions}")
        return False, ""
    return True, distinct.pop()


def check_manifests() -> None:
    mk = ROOT / ".claude-plugin" / "marketplace.json"
    pj = ROOT / "plugins" / "agent-sync" / ".claude-plugin" / "plugin.json"
    for p in (mk, pj):
        if not p.exists():
            err(f"{rel(p)}: missing")
            return
    m = json.loads(mk.read_text())
    for field in ("name", "owner", "plugins"):
        if field not in m:
            err(f"{rel(mk)}: missing '{field}'")
    src = m["plugins"][0]["source"]
    if not (ROOT / src).is_dir():
        err(f"{rel(mk)}: plugins[0].source '{src}' does not exist")


def check_example_against_schema() -> None:
    schema = json.loads((ROOT / "agent-sync.schema.json").read_text())
    example = json.loads((ROOT / "agent-sync.example.json").read_text())
    allowed = set(schema["properties"])
    for k in example:
        if k not in allowed:
            err(f"agent-sync.example.json: '{k}' is not in the schema")
    for k in schema.get("required", []):
        if k not in example:
            err(f"agent-sync.example.json: missing required '{k}'")
    backend = example.get("backend")
    if backend not in schema["properties"]["backend"]["enum"]:
        err(f"agent-sync.example.json: backend '{backend}' not in the schema enum")


def check_npm_excludes() -> None:
    """package.json ships plugins/ wholesale, so the exclusions must be explicit."""
    p = ROOT / ".npmignore"
    if not p.exists():
        err(".npmignore: missing — plugins/ is shipped wholesale, so bytecode and local "
            "state would be published with it")
        return
    body = p.read_text()
    for needed in ("__pycache__", "*.pyc"):
        if needed not in body:
            err(f".npmignore: does not exclude {needed}")


def check_public_floor() -> None:
    for f in ("README.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md"):
        if not (ROOT / f).exists():
            err(f"{f}: missing — required for a public repository")
    readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
    if "shields.io" not in readme:
        err("README.md: no badges")
    # A count in prose is a fact with a decay rate. This one was stale by two.
    refs = sorted((ROOT / "plugins" / "agent-sync" / "skills" / "agent-sync"
                   / "references").glob("*.md"))
    words = {8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
    stated = re.search(r"and (\w+) reference contracts", readme)
    if stated and stated.group(1) != words.get(len(refs), "?"):
        err(f"README.md says '{stated.group(1)} reference contracts' and {len(refs)} ship")
    for name in sorted((ROOT / "plugins" / "agent-sync" / "skills" / "agent-sync"
                        / "references").glob("*.md")):
        if name.name not in readme:
            notes.append(f"README.md does not list bundled reference {name.name}")


def check_scripts_run() -> None:
    py = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    if not py.exists():
        err("scripts/agent_sync.py: missing")
        return
    r = subprocess.run([sys.executable, str(py), "--version"], capture_output=True, text=True)
    if r.returncode != 0:
        err(f"scripts/agent_sync.py: does not run ({r.stderr.strip()})")
    for sh in sorted((ROOT / "plugins/agent-sync/hooks").glob("*.sh")):
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        if r.returncode != 0:
            err(f"{rel(sh)}: bash syntax error: {r.stderr.strip()}")
        # A leading underscore marks a sourced library, not a hook entry point.
        # Requiring +x on it would be cargo-culting the rule past its reason.
        if not sh.name.startswith("_") and not os.access(sh, os.X_OK):
            err(f"{rel(sh)}: not executable")
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--check", str(ROOT / "bin/agent-sync.js")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            err(f"bin/agent-sync.js: syntax error: {r.stderr.strip()}")


def check_hooks_manifest() -> None:
    p = ROOT / "plugins" / "agent-sync" / "hooks" / "hooks.json"
    if not p.exists():
        err("plugins/agent-sync/hooks/hooks.json: missing")
        return
    data = json.loads(p.read_text())
    hooks = data.get("hooks", {})
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"):
        if event not in hooks:
            err(f"hooks.json: no {event} entry")
    for event, entries in hooks.items():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"']+)", cmd)
                if not m:
                    err(f"hooks.json/{event}: command does not use ${{CLAUDE_PLUGIN_ROOT}}")
                    continue
                target = ROOT / "plugins" / "agent-sync" / m.group(1)
                if not target.exists():
                    err(f"hooks.json/{event}: command target {m.group(1)} does not exist")


def _branch_fixture(project: str, script: Path) -> dict[str, str]:
    """A repository with a roadmap row, a claim mapping, and a feature branch."""
    env = {**os.environ, "AGENT_SYNC_RUN_ID": "validator"}
    g = lambda *a: subprocess.run(["git", *a], cwd=project, capture_output=True, text=True)  # noqa: E731
    g("init", "-q", "-b", "main")
    g("config", "user.email", "v@e")
    g("config", "user.name", "v")
    (Path(project) / "ROADMAP.md").write_text(
        "| Task | State |\n|---|---|\n| T-1 | todo |\n| T-2 | todo |\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    subprocess.run([sys.executable, str(script), "init", "--backend", "fs"],
                   cwd=project, env=env, capture_output=True)
    cfg_path = Path(project) / ".claude" / "agent-sync.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["claimTags"] = {"ROADMAP.md": {"mode": "cell", "cell": -1,
                                       "held": "{prev} (claimed: {holder})"}}
    cfg_path.write_text(json.dumps(cfg, indent=2))
    g("add", "-A")
    g("commit", "-q", "-m", "cfg")
    return env


def check_branch_claim_discipline() -> None:
    """A claim is written through on the integration branch and nowhere else.

    Committed on a feature branch, a claim is invisible to every other agent until the
    merge — while turning the one shared file that exists to prevent collisions into a
    file two branches both edit. The holder belongs in the coordination plane until the
    work lands.
    """
    if not shutil.which("git"):
        notes.append("git not found — branch discipline check skipped")
        return
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    with tempfile.TemporaryDirectory() as project:
        env = _branch_fixture(project, script)
        roadmap = Path(project) / "ROADMAP.md"
        before = roadmap.read_text()

        subprocess.run(["git", "checkout", "-q", "-b", "feature/x"], cwd=project,
                       capture_output=True)
        r = subprocess.run([sys.executable, str(script), "acquire", "T-1"], cwd=project,
                           env=env, capture_output=True, text=True, timeout=60)
        if roadmap.read_text() != before:
            err("branch discipline: `acquire` wrote the claim into the roadmap while on a "
                "feature branch — that edit is invisible until the merge and conflicts there")
        if "coordination plane" not in r.stdout:
            err("branch discipline: `acquire` on a branch does not say where the claim lives; "
                "silence reads as 'no claim was recorded'")

        subprocess.run([sys.executable, str(script), "release", "T-1"], cwd=project,
                       env=env, capture_output=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=project, capture_output=True)
        subprocess.run([sys.executable, str(script), "acquire", "T-2"], cwd=project,
                       env=env, capture_output=True, timeout=60)
        if "claimed: " not in roadmap.read_text():
            err("branch discipline: `acquire` on the integration branch did not write the "
                "claim through — the rule is 'only there', not 'nowhere'")


def check_merge_refuses_conflicts() -> None:
    """A conflicting merge is refused before anything is touched, and leaves no record.

    A merge that starts and aborts leaves the operator in a repository they did not ask
    for, and a log entry for a merge that did not happen is worse than no log at all.
    """
    if not shutil.which("git"):
        notes.append("git not found — merge refusal check skipped")
        return
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    with tempfile.TemporaryDirectory() as project:
        env = _branch_fixture(project, script)
        g = lambda *a: subprocess.run(["git", *a], cwd=project, capture_output=True, text=True)  # noqa: E731
        (Path(project) / "f.txt").write_text("base\n")
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        g("checkout", "-q", "-b", "feature/y")
        (Path(project) / "f.txt").write_text("branch\n")
        g("commit", "-q", "-am", "branch")
        g("checkout", "-q", "main")
        (Path(project) / "f.txt").write_text("integration\n")
        g("commit", "-q", "-am", "integration")
        g("checkout", "-q", "feature/y")

        r = subprocess.run([sys.executable, str(script), "merge"], cwd=project, env=env,
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            err("merge: a conflicting merge reported success")
        if "f.txt" not in r.stdout:
            err("merge: the conflict was refused without naming the file that conflicts")
        on = subprocess.run(["git", "branch", "--show-current"], cwd=project,
                            capture_output=True, text=True).stdout.strip()
        if on != "feature/y":
            err(f"merge: refused, but left the repository on '{on}' instead of the branch "
                "it was run from")
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=project,
                               capture_output=True, text=True).stdout
        if [l for l in dirty.splitlines() if ".agent-sync" not in l]:
            err("merge: refused, but left the working tree dirty")
        if (Path(project) / "docs" / "MERGES.md").exists():
            err("merge: refused, and still wrote a merge-log entry for a merge that "
                "never happened")


def check_repo_slug_consistent() -> None:
    """One repository, one address. A rename that reaches some files and not others
    leaves the installer cloning one repo while the README, the badge and the schema
    `$id` point at another — and the redirect that hides it disappears the moment the
    old name is taken by someone else.

    `CHANGELOG.md` and `docs/` are exempt: they record what shipped and what was
    designed at the time, and correcting history is a different kind of lie.
    """
    pkg = json.loads((ROOT / "package.json").read_text())
    m = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$",
                  pkg.get("repository", {}).get("url", ""))
    if not m:
        err("package.json: repository.url is not a github.com slug — nothing to check against")
        return
    owner, repo = m.group(1), m.group(2)

    # Two shapes. A URL or an install argument, and — because that is what the installers
    # actually clone — a bare slug in quotes. The quoted form must close right after the
    # repository name, or every "bin/agent-sync.js" path in the tree reads as a slug.
    refs = (
        re.compile(r"(?:github\.com/|github:|raw\.githubusercontent\.com/|marketplace add )"
                   rf"([A-Za-z0-9_.-]+)/{re.escape(repo)}\b"),
        re.compile(rf"""["']([A-Za-z0-9_.-]+)/{re.escape(repo)}["']"""),
    )
    skip_dirs = {".git", "node_modules", "docs", ".agent-sync"}
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file() or f.name == "CHANGELOG.md":
            continue
        if set(f.relative_to(ROOT).parts) & skip_dirs:
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for found in {o for r in refs for o in r.findall(text)}:
            if found != owner:
                err(f"{rel(f)}: points at '{found}/{repo}', but the package repository is "
                    f"'{owner}/{repo}' — a half-finished rename sends users to two repositories")


def check_lease_report_agrees() -> None:
    """Every surface that reports what a lease is worth must say the same thing.

    `status` called the *record* adapter the "lease authority" — a role the knowledge
    base has not held since 1.0.0, when exclusion moved to a lock the store cannot
    lose — while `acquire` and `check` described the lease mode. One guarantee phrased
    two ways reads as two guarantees, and an operator acts on the weaker one. So the
    wording lives in one table and this exercises all three commands against it.
    """
    if not shutil.which("git"):
        notes.append("git not found — lease reporting check skipped")
        return
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    spec = importlib.util.spec_from_file_location("agent_sync_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                                   # noqa: BLE001
        err(f"scripts/agent_sync.py: cannot be imported ({exc})")
        return
    if not hasattr(mod, "lease_guarantee"):
        err("scripts/agent_sync.py: no lease_guarantee() — the wording has no single home")
        return

    for mode in ("local", "git"):
        phrase = mod.lease_guarantee(mode)[0]
        with tempfile.TemporaryDirectory() as project:
            env = {**os.environ, "AGENT_SYNC_RUN_ID": "validator"}
            run = lambda *a, **kw: subprocess.run(  # noqa: E731
                [sys.executable, str(script), *a], cwd=project, env=env,
                capture_output=True, text=True, timeout=60, **kw)
            for argv in (["git", "init", "-q"],
                         ["git", "-c", "user.email=v@e", "-c", "user.name=v",
                          "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(argv, cwd=project, capture_output=True)
            if mode == "git":
                remote = Path(project) / ".remote.git"
                subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                               capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", str(remote)],
                               cwd=project, capture_output=True)
            if run("init", "--backend", "fs").returncode != 0:
                err(f"lease reporting [{mode}]: init failed")
                continue
            cfg_path = Path(project) / ".claude" / "agent-sync.json"
            cfg = json.loads(cfg_path.read_text())
            cfg["leaseBackend"] = mode
            cfg_path.write_text(json.dumps(cfg, indent=2))

            for command in (["status"], ["acquire", "VAL-1"], ["check"]):
                out = run(*command)
                text = out.stdout + out.stderr
                if phrase not in text:
                    err(f"lease reporting [{mode}]: `{command[0]}` does not state the "
                        f"guarantee '{phrase}' — surfaces disagree about what a lease is worth")
                if command[0] == "status" and "lease authority" in text:
                    err("lease reporting: `status` still calls the record backend the "
                        "lease authority; it has not decided a lease since 1.0.0")


def check_lease_held_is_visible() -> None:
    """A lease this run won must be visible to `whoami` and to the guard — in EVERY mode.

    This existed only for the local mode, and the git mode shipped broken because of it: the
    winner pushed a ref and `held()` read a lock directory that nothing in that path wrote, so
    `acquire` said "won", `whoami` said "holds: nothing", and the PreToolUse guard denied the very
    run holding the lease. Every guarded register was unwritable under the mode the tool
    recommends. The bug was invisible because the mode-agnostic assertion did not exist, so this
    check runs the same three steps against both modes and is the reason to keep it that way.
    """
    if not shutil.which("git"):
        notes.append("git not found — lease visibility check skipped")
        return
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"

    for mode in ("local", "git"):
        with tempfile.TemporaryDirectory() as project:
            env = {**os.environ, "AGENT_SYNC_RUN_ID": "validator"}
            run = lambda *a: subprocess.run(  # noqa: E731
                [sys.executable, str(script), *a], cwd=project, env=env,
                capture_output=True, text=True, timeout=60)
            for argv in (["git", "init", "-q"],
                         ["git", "-c", "user.email=v@e", "-c", "user.name=v",
                          "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(argv, cwd=project, capture_output=True)
            if mode == "git":
                remote = Path(project) / ".remote.git"
                subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", str(remote)],
                               cwd=project, capture_output=True)
            if run("init", "--backend", "fs").returncode != 0:
                err(f"lease visibility [{mode}]: init failed")
                continue
            cfg_path = Path(project) / ".claude" / "agent-sync.json"
            cfg = json.loads(cfg_path.read_text())
            cfg["leaseBackend"] = mode
            cfg["guardedFiles"] = ["docs/GUARDED.md"]
            cfg_path.write_text(json.dumps(cfg, indent=2))

            acquired = run("acquire", "VIS-1")
            if "won" not in (acquired.stdout + acquired.stderr):
                err(f"lease visibility [{mode}]: acquire did not report a win")
                continue
            if "VIS-1" not in run("whoami").stdout:
                err(f"lease visibility [{mode}]: acquire won VIS-1 but `whoami` does not hold it — "
                    "the path that writes the lease and the path that reads it disagree")
            if run("guard", "docs/GUARDED.md").returncode != 0:
                err(f"lease visibility [{mode}]: the guard denies a guarded file to the run that "
                    "holds its lease — every guarded write is impossible in this mode")
            run("release", "VIS-1")
            if "VIS-1" in run("whoami").stdout:
                err(f"lease visibility [{mode}]: `whoami` still holds VIS-1 after release")


def check_release_refuses_other_runs() -> None:
    """`release` must refuse a lease another run holds — and say so with a non-zero exit.

    It used to blank the board's claim cell, print "released" and exit 0 while the lease plane
    correctly refused underneath. The board then advertised the task as free while the lease still
    held it, which is the collision the lease exists to prevent, produced by the tool itself. The
    check runs both modes because the refusal lives in a different place in each.
    """
    if not shutil.which("git"):
        notes.append("git not found — release-refusal check skipped")
        return
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"

    for mode in ("local", "git"):
        with tempfile.TemporaryDirectory() as project:
            def run(*a, run_id="owner"):
                return subprocess.run(
                    [sys.executable, str(script), *a], cwd=project,
                    env={**os.environ, "AGENT_SYNC_RUN_ID": run_id},
                    capture_output=True, text=True, timeout=60)
            for argv in (["git", "init", "-q"],
                         ["git", "-c", "user.email=v@e", "-c", "user.name=v",
                          "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(argv, cwd=project, capture_output=True)
            if mode == "git":
                remote = Path(project) / ".remote.git"
                subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True)
                subprocess.run(["git", "remote", "add", "origin", str(remote)],
                               cwd=project, capture_output=True)
            if run("init", "--backend", "fs").returncode != 0:
                err(f"release refusal [{mode}]: init failed")
                continue
            cfg_path = Path(project) / ".claude" / "agent-sync.json"
            cfg = json.loads(cfg_path.read_text())
            cfg["leaseBackend"] = mode
            cfg_path.write_text(json.dumps(cfg, indent=2))

            if "won" not in run("acquire", "REL-1").stdout:
                err(f"release refusal [{mode}]: the owner could not acquire REL-1")
                continue

            stolen = run("release", "REL-1", run_id="intruder")
            if stolen.returncode == 0:
                err(f"release refusal [{mode}]: a run that does not hold REL-1 released it and "
                    "reported success — the board and the lease now disagree")
            if "REL-1" not in run("whoami").stdout:
                err(f"release refusal [{mode}]: the owner lost REL-1 to a run that did not hold it")
            if run("release", "REL-1").returncode != 0:
                err(f"release refusal [{mode}]: the owner could not release its own lease")


def check_hooks_noop_without_config() -> None:
    """Installed globally, every hook must do nothing in an unconfigured project.

    `guard.sh` shipped for eleven versions with the config check on only one of
    its two branches: the `git commit` branch called `agent_sync.py guard` per
    staged path, read that command's "no .claude/agent-sync.json" exit 2 as
    "no lease held", and blocked every commit in every repo that had never run
    `init`. Syntax checks cannot see that, so the invariant is exercised.
    """
    hooks = ROOT / "plugins" / "agent-sync" / "hooks"
    if not shutil.which("git"):
        notes.append("git not found — hook no-op check skipped")
        return
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        # The exact shape that regressed: a commit, no file_path.
        "tool_input": {"command": "git " + "commit -m wip"},
    })
    with tempfile.TemporaryDirectory() as unconfigured:
        # A real repo with a staged path, or the guard's commit branch reads an
        # empty index and exits 0 for the wrong reason — the bug would pass.
        (Path(unconfigured) / "f.txt").write_text("x\n")
        for argv in (["git", "init", "-q"], ["git", "add", "f.txt"]):
            subprocess.run(argv, cwd=unconfigured, capture_output=True)
        env = {**os.environ,
               "CLAUDE_PLUGIN_ROOT": str(ROOT / "plugins" / "agent-sync"),
               "CLAUDE_PROJECT_DIR": unconfigured}
        for sh in sorted(hooks.glob("*.sh")):
            if sh.name.startswith("_"):
                continue
            try:
                r = subprocess.run(["bash", str(sh)], input=payload, env=env,
                                   cwd=unconfigured, capture_output=True,
                                   text=True, timeout=30)
            except subprocess.TimeoutExpired:
                err(f"{rel(sh)}: hung in a project without .claude/agent-sync.json")
                continue
            if r.returncode != 0:
                err(f"{rel(sh)}: exit {r.returncode} in a project without "
                    f"agent-sync.json — hooks must be a no-op there "
                    f"({r.stderr.strip() or 'no stderr'})")


def check_reserve_respects_the_register() -> None:
    """`reserve` handed out ids that were already written. Found in nicegram-business, 2026-08-09.

    `reserve DEC` returned DEC-0270, 0271, 0272 while the register's max heading was DEC-0281. The
    counter was live but 11 behind: the `base` event is seeded once, and every id written by a path
    other than this tool — a person editing the file, another session's Doc Loop, a merge — moves
    the register without moving the log.

    That inverts the mechanism. An agent following the protocol exactly (reserve, never trust the
    register's own "next free" line) is the one that writes a duplicate, silently; an agent that
    ignored the tool and read the line would have been right.

    The fix consults the register on every reserve and treats it as a floor, which means re-basing
    mid-log — so these assert the allocator survives that, since a re-base that skipped or recycled
    ids would trade one collision for another.
    """
    sys.path.insert(0, str(ROOT / "plugins/agent-sync/skills/agent-sync/scripts"))
    try:
        from agent_sync import resolve_reservations
    except Exception as exc:  # pragma: no cover - import failure is itself the finding
        errors.append(f"reserve: cannot import the allocator to test it ({exc})")
        return

    def ev(op, run="r1", value=""):
        return {"op": op, "key": "DEC", "run": run, "value": value}

    # A re-base restarts the count. Before the fix `served` survived the new base, so the next id
    # was `new_base + 2` and the two ids at the new base were skipped forever.
    log = [ev("base", value="0100"), ev("reserve"), ev("reserve"),
           ev("base", value="0200"), ev("reserve")]
    _b, _f, assign = resolve_reservations(log, "DEC")
    if assign[-1][1] != 200:
        errors.append(
            f"reserve: after a re-base the next id is {assign[-1][1]}, expected 200 — the count "
            "did not restart, so a re-base skips as many ids as were served under the old base")

    # Ids freed below a new base are gone, not recycled: the register moved past them, so a heading
    # exists there now and handing one back is the same collision by the other door.
    log = [ev("base", value="0100"), ev("reserve"), ev("release_id", value="0100"),
           ev("base", value="0200"), ev("reserve")]
    _b, _f, assign = resolve_reservations(log, "DEC")
    if assign[-1][1] != 200:
        errors.append(
            f"reserve: a freed id below the new base was handed back ({assign[-1][1]}) — the "
            "register has moved past it, so something is written there")

    # A freed id at or above the base is still legitimately reusable; nothing was written to it.
    log = [ev("base", value="0100"), ev("reserve"), ev("reserve"),
           ev("release_id", value="0100"), ev("reserve")]
    _b, _f, assign = resolve_reservations(log, "DEC")
    if assign[-1][1] != 100:
        errors.append(
            f"reserve: a genuinely free id was not reused ({assign[-1][1]}, expected 100) — the "
            "fix must not turn every release into a leak")


# ------------------------------------------------------- composition, not units
#
# Every defect the 2026-08-10 audit found lived in the gap between two things this
# file already tested. `resolve_reservations` was correct and `reserve` handed three
# runs one id; `lease_guarantee` was quoted by every surface and `renew` refreshed
# nothing; the schema listed `mergeLog` and `check` called it unknown. A unit test
# proves a function; only a composition test proves the promise.


def _load_script(name: str = "agent_sync_under_test"):
    """A private copy of the coordinator, so a check may monkeypatch it freely."""
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    spec = importlib.util.spec_from_file_location(name, script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_project(path: str, *, branch: str = "main") -> None:
    """A repository shaped like a real one — including an identity.

    The identity used to be passed per-commit with `-c`, which works on a workstation
    with a global `.gitconfig` and fails on a CI runner without one: anything the tool
    itself commits (a merge, the merge-log entry) has no author. `merge` passed here and
    failed there. A fixture must not depend on the developer's own machine being
    configured — that is the whole class standing instruction 4 exists for.
    """
    for argv in (["git", "init", "-q", "-b", branch],
                 ["git", "config", "user.email", "v@e"],
                 ["git", "config", "user.name", "v"],
                 ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(argv, cwd=path, capture_output=True)


def _run_script(project: str, *args: str, run_id: str = "validator",
                env: dict[str, str] | None = None):
    script = ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py"
    return subprocess.run([sys.executable, str(script), *args], cwd=project,
                          env={**os.environ, "AGENT_SYNC_RUN_ID": run_id, **(env or {})},
                          capture_output=True, text=True, timeout=120)


def _write_cfg(project: str, **keys) -> None:
    p = Path(project) / ".claude" / "agent-sync.json"
    cfg = json.loads(p.read_text())
    cfg.update(keys)
    p.write_text(json.dumps(cfg, indent=2))


def _recording_plane(mod, base: Path):
    """A record plane that CAN order writes — what `outline` claims to be.

    `fs` declares atomicAppend false, so it refuses to allocate ids at all and the
    allocation path ships untested. This stands in for a plane that accepts the job,
    which is the only configuration in which the id protocol actually runs.
    """

    class Plane(mod.Adapter):
        name = "recording"
        capabilities = {"atomicAppend": True, "totalOrderRead": True,
                        "search": False, "exclusiveLease": False}

        def configured(self):
            return True

        def _p(self, path):
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-").lower()
            base.mkdir(parents=True, exist_ok=True)
            return base / f"{safe}.md"

        def tree_ensure(self, path):
            p = self._p(path)
            if not p.exists():
                p.write_text("")
            return str(p)

        def log_append(self, oid, line):
            with open(oid, "a") as fh:
                fh.write(line.rstrip("\n") + "\n")

        def log_read(self, oid):
            p = Path(oid)
            return p.read_text() if p.exists() else ""

        def doc_put(self, oid, text):
            Path(oid).write_text(text)

        def doc_get(self, oid):
            p = Path(oid)
            return p.read_text() if p.exists() else ""

        def log_shards(self, prefix):
            stem = re.sub(r"[^A-Za-z0-9._-]+", "-", prefix).strip("-").lower()
            return [str(q) for q in sorted(base.glob(f"{stem}*.md"))]

    return Plane()


def check_reserve_is_race_free() -> None:
    """Two runs must never be handed one id — the headline promise of this tool.

    It was broken and nothing here could see it: `reserve` read `log_id(...)`, which is
    THIS run's own shard, and never merged the others. Three runs each seeded their own
    `base` from the register and each returned the same number. The pure allocator was
    tested and correct; the caller never consulted it with the whole log. So the check
    has to drive `Sync.reserve` itself, from more than one identity.
    """
    cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as project:
            _git_project(project)
            docs = Path(project) / "docs"
            docs.mkdir()
            (docs / "DECISIONS.md").write_text(
                "# Decisions\n\n**Next free ID:** `DEC-0007`\n\n### DEC-0001 — x\n")
            if _run_script(project, "init", "--backend", "fs").returncode != 0:
                err("reserve race: init failed")
                return
            _write_cfg(project, idRegisters={"DEC": {
                "file": "docs/DECISIONS.md",
                "nextFreeIdPattern": r"\*\*Next free ID:\*\* `DEC-(\d{4})`"}})

            mod = _load_script("agent_sync_reserve_test")
            plane = _recording_plane(mod, Path(project) / ".agent-sync" / "plane")
            mod.make_adapter = lambda cfg, root: plane
            os.chdir(project)          # Sync() resolves the project from its own cwd

            handed: list[int] = []
            for rid in ("alpha", "beta", "gamma"):
                os.environ["AGENT_SYNC_RUN_ID"] = rid
                handed.append(mod.Sync().reserve("DEC"))
            os.environ.pop("AGENT_SYNC_RUN_ID", None)

            if len(set(handed)) != len(handed):
                err(f"reserve: three runs were handed {sorted(handed)} — ids collide, which is "
                    "the one failure this tool exists to prevent")
            if handed and min(handed) < 7:
                err(f"reserve: handed {min(handed)}, below the register's next free id (7) — "
                    "an id that already has a heading")
    finally:
        os.chdir(cwd)
        os.environ.pop("AGENT_SYNC_RUN_ID", None)


def _lock_age(project: str, key: str) -> float:
    """Seconds since the timestamp the lease authority would expire this lock by."""
    mod = _load_script("agent_sync_age_probe")
    lock = Path(project) / ".agent-sync" / "leases" / f"{key}.lock"
    held = json.loads(lock.read_text())
    return time.time() - mod.parse_iso(held.get("ts", ""))


def check_renew_extends_the_lease() -> None:
    """`renew` must move the timestamp the lease is expired by. It did not.

    It appended `op=renew` to the record plane — which has not decided a lease since
    1.0.0 — and touched a throttle file. The lock's own `ts` was written once, by
    `acquire`. So a run holding a lease simply lost it at TTL while working, its own
    guard began denying it, and another run took the task. The `PostToolUse` hook made
    no difference because there was nothing for it to refresh.
    """
    if not shutil.which("git"):
        notes.append("git not found — renew check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("renew: init failed")
            return
        _write_cfg(project, leaseTtlSeconds=600, renewIntervalSeconds=300)

        if "won" not in _run_script(project, "acquire", "REN-1").stdout:
            err("renew: could not acquire the lease to renew")
            return

        # Age the lease to nearly expired, exactly as forty minutes of work would, and
        # clear the throttle the way the interval elapsing does.
        lock = Path(project) / ".agent-sync" / "leases" / "REN-1.lock"
        held = json.loads(lock.read_text())
        mod = _load_script("agent_sync_renew_probe")
        aged = datetime_minus(mod, 590)
        held["ts"] = aged
        lock.write_text(json.dumps(held))
        (Path(project) / ".agent-sync" / "last-renew").unlink(missing_ok=True)

        _run_script(project, "renew", "REN-1")

        if json.loads(lock.read_text()).get("ts") == aged:
            err("renew: the lease timestamp did not move — `renew` refreshes nothing, so a "
                "run loses its own lease at TTL while it is still working")
        if _lock_age(project, "REN-1") > 60:
            err("renew: the lease is still older than a minute after a renew")
        if "REN-1" not in _run_script(project, "whoami").stdout:
            err("renew: the run does not hold its lease after renewing it")


def datetime_minus(mod, seconds: int) -> str:
    """An ISO stamp `seconds` in the past, in the script's own format."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


def check_config_round_trip() -> None:
    """The config `init` writes must pass `check`, and so must the shipped example.

    They did not. `check` carried its own literal list of legal keys, and `mergeLog`
    (written by `init` itself) and `integrationBranch` (in the schema, in the example,
    read by the code) were absent from it. `check` called them unknown and said they
    "will be ignored" — false twice over, and an instruction: an agent making `check`
    green deletes working configuration.
    """
    if not shutil.which("git"):
        notes.append("git not found — config round-trip check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("config round-trip: init failed")
            return

        out = _run_script(project, "check")
        if "is not in the schema" in out.stdout:
            bad = [l.strip() for l in out.stdout.splitlines() if "is not in the schema" in l]
            err(f"config round-trip: `check` rejects the config `init` just wrote: {bad}")

        # And the shipped example, whose every key the schema declares.
        example = json.loads((ROOT / "agent-sync.example.json").read_text())
        example["backend"] = "fs"
        example.pop("$schema", None)
        (Path(project) / ".claude" / "agent-sync.json").write_text(json.dumps(example, indent=2))
        out = _run_script(project, "check")
        if "is not in the schema" in out.stdout:
            bad = [l.strip() for l in out.stdout.splitlines() if "is not in the schema" in l]
            err(f"config round-trip: `check` rejects keys of the shipped example: {bad}")

        # And the two lists must be one list. The schema cannot be read at runtime — the
        # plugin ships without it — so the coordinator names the keys itself and this
        # asserts the equality that `check` used to get wrong silently.
        schema = set(json.loads((ROOT / "agent-sync.schema.json").read_text())["properties"])
        mod = _load_script("agent_sync_config_keys")
        declared = set(getattr(mod, "CONFIG_KEYS", ()))
        if not declared:
            err("config round-trip: the coordinator declares no CONFIG_KEYS — the legal-key "
                "list is inline in `check` again, where it drifted from the schema before")
        elif declared != schema:
            err(f"config round-trip: CONFIG_KEYS and the schema disagree — "
                f"only in the code: {sorted(declared - schema)}; "
                f"only in the schema: {sorted(schema - declared)}")


def check_no_success_on_failed_publish() -> None:
    """A command must not print success it did not achieve, or exit 0 having failed.

    Three surfaces did. `release-id` printed "released" on a backend that cannot record
    one and returned 0. `record` printed "recorded" and returned 0 while stderr said the
    entry was never published. `journal` crashed with a Python traceback when the state
    directory was not writable, because adapter `OSError` was never turned into the
    tool's own failure type.
    """
    if not shutil.which("git"):
        notes.append("git not found — false-success check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("false success: init failed")
            return

        out = _run_script(project, "release-id", "DEC", "0007")
        if out.returncode == 0:
            err("release-id: reported success on a backend that records nothing — the id was "
                "not returned to anybody, and the caller was told it was")

        # An unreachable plane: the record must fail loudly, not quietly.
        unreachable = {"AGENT_SYNC_BACKEND": "outline",
                       "AGENT_SYNC_OUTLINE_URL": "http://127.0.0.1:1",
                       "AGENT_SYNC_OUTLINE_TOKEN": "x",
                       "AGENT_SYNC_OUTLINE_COLLECTION":
                           "00000000-0000-4000-8000-000000000000"}
        _write_cfg(project, backend="outline")
        out = _run_script(project, "record", "a thing", env=unreachable)
        if out.returncode == 0:
            err("record: exited 0 with the plane unreachable — the as-built record has a gap "
                "and the caller was told it was written")
        if "recorded" in out.stdout:
            err("record: printed 'recorded' for an entry that was never published")

        # An unwritable local plane is the same contract reached the other way, and it is
        # the path that used to produce a Python traceback: an adapter `OSError` walked
        # past every `except Fail` into `main`, and the agent was handed a stack trace as
        # the state of the coordination plane. Deterministic and instant, so the rest of
        # the surfaces are exercised here rather than against a socket.
        _write_cfg(project, backend="fs")
        state = Path(project) / ".agent-sync"
        state.mkdir(exist_ok=True)
        mode = state.stat().st_mode
        os.chmod(state, 0o500)
        try:
            for argv, label in (
                    (("record", "a thing"), "record"),
                    (("signal", "DEP-001", "filed"), "signal"),
                    (("journal", "a note"), "journal")):
                out = _run_script(project, *argv, run_id="unwritable")
                if "Traceback" in out.stderr:
                    err(f"{label}: an unwritable local plane raises a Python traceback instead "
                        "of the tool's own failure, with a sentence a reader can act on")
                if out.returncode == 0:
                    err(f"{label}: exited 0 with the plane unwritable — nothing was recorded "
                        "and the caller was told it was")
        finally:
            os.chmod(state, mode)


def check_guard_denial_names_only_what_it_knows() -> None:
    """The denial must not name a holder of some other key as the holder of this file.

    It reported "<path> is a guarded registry file and this run holds no lease — r-x
    holds a lease right now", where r-x held an unrelated task. An agent repeats that
    sentence, and the transcript then contains a fact nobody can find a source for.
    """
    if not shutil.which("git"):
        notes.append("git not found — guard message check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        docs = Path(project) / "docs"
        docs.mkdir()
        (docs / "DECISIONS.md").write_text("# Decisions\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("guard message: init failed")
            return
        _write_cfg(project, guardedFiles=["docs/DECISIONS.md"])

        _run_script(project, "acquire", "OTHER-1", run_id="holder")
        out = _run_script(project, "guard", "docs/DECISIONS.md", run_id="asker")
        text = out.stdout + out.stderr
        if out.returncode != 2:
            err("guard message: a run holding nothing was allowed to write a guarded file")
        if "holds a lease right now" in text and "OTHER-1" not in text:
            err("guard message: names a holder without naming what they hold — the reader "
                "concludes that run holds this file, which is not what was checked")


def check_doctrine_is_current() -> None:
    """No published surface may still sell a design this tool measured and rejected.

    `marketplace.json` is the first thing a person reads, and it described leases as
    "decided by replaying one append-only log so no backend needs compare-and-swap" —
    the exact belief 1.0.0 refuted and SKILL.md's first trap forbids.
    """
    refuted = (
        "no backend needs compare-and-swap",
        "no backend requires compare-and-swap",
    )
    surfaces = [ROOT / ".claude-plugin" / "marketplace.json",
                ROOT / "plugins" / "agent-sync" / ".claude-plugin" / "plugin.json",
                ROOT / "README.md",
                ROOT / "package.json"]
    for f in surfaces:
        if not f.exists():
            continue
        text = f.read_text().lower()
        for phrase in refuted:
            if phrase in text:
                err(f"{rel(f)}: still claims '{phrase}' — leases have been decided by an "
                    "atomic primitive since 1.0.0, and the knowledge base never decides one")


def check_steal_is_atomic() -> None:
    """Stealing an expired lease must not have a window two runs can both pass through.

    `unlink` then `O_EXCL create` are two operations. A second stealer that has already
    seen the lock expired can remove the lock the first one just created, and both then
    hold what each believes is an exclusive lease. Twelve racing processes did not expose
    it — with a 300 ms gap injected between the two calls, two of two won.

    A race is the wrong shape for a regression test, so the invariant is checked directly:
    the reap-and-create is one critical section, and a run that finds another stealer
    inside it loses rather than proceeding. That names an internal file, deliberately —
    the alternative is a test that passes by luck on the broken code, which is what the
    twelve-process version did.
    """
    if not shutil.which("git"):
        notes.append("git not found — steal atomicity check skipped")
        return
    mod = _load_script("agent_sync_steal_probe")
    expired = json.dumps({"run": "r-dead", "ttl": 60,
                          "ts": datetime_minus(mod, 9999), "repo": "x"})

    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("steal atomicity: init failed")
            return
        leases = Path(project) / ".agent-sync" / "leases"
        leases.mkdir(parents=True, exist_ok=True)
        lock = leases / "STEAL-1.lock"
        guard = leases / "STEAL-1.lock.steal"

        # 1. Another stealer is inside the section: this run must lose, not steal.
        lock.write_text(expired)
        guard.write_text("someone-else")
        out = _run_script(project, "acquire", "STEAL-1", run_id="contender")
        if "won" in out.stdout:
            err("steal atomicity: a run stole an expired lease while another stealer held "
                "the section — the reap and the create are not one operation, so two runs "
                "can both end up holding it")
        guard.unlink(missing_ok=True)

        # 2. A section abandoned by a crash must not block stealing forever.
        lock.write_text(expired)
        guard.write_text("crashed")
        old = time.time() - 3600
        os.utime(guard, (old, old))
        out = _run_script(project, "acquire", "STEAL-1", run_id="later")
        if "won" not in out.stdout:
            err("steal atomicity: an abandoned steal section blocks the lease permanently — "
                "the mutex needs its own expiry, or a crash costs the key until a human "
                "deletes a file nobody documents")
        guard.unlink(missing_ok=True)

        # 3. And the ordinary property still holds: many racers, exactly one winner.
        lock.write_text(expired)
        outs = []
        with tempfile.TemporaryDirectory() as _:
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(max_workers=12) as pool:
                futures = [pool.submit(_run_script, project, "acquire", "STEAL-1",
                                       run_id=f"racer{i}") for i in range(12)]
                outs = [f.result() for f in futures]
        winners = [o for o in outs if "won" in o.stdout]
        if len(winners) != 1:
            err(f"steal atomicity: {len(winners)} of 12 racing runs won one expired lease")


def check_merge_refuses_stale_target() -> None:
    """A merge into a stale integration branch must not be reported as landed.

    `merge` computed conflicts and the diff against `origin/<target>` and then merged into
    the LOCAL `<target>`, which nothing fast-forwards. It printed the staleness it had just
    measured — "main moved: 1 commit(s)" — then "✓ merged", wrote a merge-log entry, and
    released the lease. The push was rejected as non-fast-forward, so the work had not
    landed, the log said it had, and the task was free for somebody else to take.
    """
    if not shutil.which("git"):
        notes.append("git not found — stale-merge check skipped")
        return
    with tempfile.TemporaryDirectory() as tmp:
        remote, work, other = (Path(tmp) / n for n in ("remote.git", "work", "other"))
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                       capture_output=True)
        _git_project(str(work.parent), branch="main") if False else None
        work.mkdir()
        g = lambda *a, **kw: subprocess.run(  # noqa: E731
            ["git", *a], cwd=kw.get("cwd", str(work)), capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "v@e")
        g("config", "user.name", "v")
        g("remote", "add", "origin", str(remote))
        (work / "f.txt").write_text("base\n")
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        if _run_script(str(work), "init", "--backend", "fs").returncode != 0:
            err("stale merge: init failed")
            return
        g("add", "-A")
        g("commit", "-q", "-m", "cfg")
        g("push", "-q", "-u", "origin", "main")

        # A second agent lands something on the integration branch.
        subprocess.run(["git", "clone", "-q", str(remote), str(other)], capture_output=True)
        for argv in (["git", "config", "user.email", "o@e"], ["git", "config", "user.name", "o"]):
            subprocess.run(argv, cwd=str(other), capture_output=True)
        (other / "theirs.txt").write_text("theirs\n")
        subprocess.run(["git", "add", "-A"], cwd=str(other), capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "landed-by-another-agent"],
                       cwd=str(other), capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=str(other),
                       capture_output=True)
        theirs = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(other),
                                capture_output=True, text=True).stdout.strip()

        g("checkout", "-q", "-b", "feature/x")
        (work / "mine.txt").write_text("mine\n")
        g("add", "-A")
        g("commit", "-q", "-m", "mine")

        out = _run_script(str(work), "merge", "--key", "T-1", "--summary", "landed")
        merged = g("log", "--format=%H", "main").stdout.split()
        log_written = (work / "docs" / "MERGES.md").exists()

        if out.returncode == 0 and theirs not in merged:
            err("merge: reported success while merging into a stale local integration "
                "branch — the other agent's commit is not in it, the push will be rejected, "
                "and the merge log already says the work landed")
        if out.returncode == 0 and not log_written:
            err("merge: reported success and wrote no merge-log entry")
        if out.returncode != 0 and log_written:
            err("merge: refused, and still recorded a merge that never happened")


def check_guard_and_check_agree_on_globs() -> None:
    """One pattern, one meaning. The guard and `check` read them differently.

    The guard used `Path.match`, which anchors at the RIGHT: `docs/DECISIONS.md` matched
    `vendor/docs/DECISIONS.md`, a file `check` — which enumerates with `glob` — never saw
    and never validated. In the other direction `Path.match` does not walk `**` before
    3.13, so `docs/**/*.md` guarded less than `check` reported. A pattern that means two
    things has no meaning.
    """
    if not shutil.which("git"):
        notes.append("git not found — glob agreement check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        root = Path(project)
        for rel in ("docs/DECISIONS.md", "docs/deep/NOTES.md", "vendor/docs/DECISIONS.md"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("glob agreement: init failed")
            return
        _write_cfg(project, guardedFiles=["docs/DECISIONS.md", "docs/**/*.md"])

        cases = {
            "docs/DECISIONS.md": True,        # named exactly
            "docs/deep/NOTES.md": True,       # under the recursive pattern
            "vendor/docs/DECISIONS.md": False,  # NOT the file the config names
        }
        for rel, guarded in cases.items():
            out = _run_script(project, "guard", rel, run_id="nobody")
            denied = out.returncode == 2
            if denied != guarded:
                err(f"glob agreement: `{rel}` is "
                    f"{'' if guarded else 'not '}guarded by the config, but the guard "
                    f"{'allowed' if guarded else 'denied'} it — the guard and `check` do not "
                    "read one pattern the same way")


def check_unparseable_log_fails_loudly() -> None:
    """Past 2%, a log is refused rather than replayed. Three documents promised this.

    `MAX_UNPARSEABLE` was declared and never read. The only trace of the rule was a line
    on the board that printed a warning and returned 0, while `SKILL.md`,
    `lease-protocol.md` and the README all stated that a log this broken stops the run.
    A replay of a log that mostly does not parse reports holders who do not exist and
    silence where the holders really are.
    """
    if not shutil.which("git"):
        notes.append("git not found — unparseable-log check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("unparseable log: init failed")
            return
        shard = Path(project) / ".agent-sync" / "30-claims-r-corrupt.md"
        shard.parent.mkdir(parents=True, exist_ok=True)
        good = ("- `2026-08-01T10:00:00Z` `op=acquire` `key=K-{}` `run=r-a` `ttl=60`\n")
        shard.write_text("".join(good.format(i) for i in range(8))
                         + "- `not-an-entry at all\n- `also broken\n")

        out = _run_script(project, "board")
        if out.returncode == 0:
            err("unparseable log: `board` regenerated the board from a log that is 20% "
                "unreadable and exited 0 — the page now states holdings nobody can verify")
        if "unparseable" not in (out.stdout + out.stderr).lower():
            err("unparseable log: `board` failed without naming the reason")

        out = _run_script(project, "check")
        if "unparseable" not in out.stdout.lower():
            err("unparseable log: `check` calls the setup healthy with an unreadable log")

        out = _run_script(project, "status")
        if out.returncode == 0:
            err("unparseable log: `status` reported normally over an unreadable plane")


STAGE_MARKER = re.compile(r"agent-sync:stages\s+rules=([\d,]+)\s+wired=([\d,]+)")


def check_stage_binding_agrees() -> None:
    """The stage binding is stated once, and the other surfaces quote it.

    Three documents gave three answers. `SKILL.md` said "four of the eleven stages" and
    then listed five (0, 1, 3, 9, 10); the README said 0, 3, 4, 5, 9 and 10;
    `pipeline-binding.md` agreed with the README and also called stage 1 "nothing shared
    to coordinate" — the stage `SKILL.md` puts `reconcile` on, and the stage the tool's
    own doctrine says must resolve every divergence before code is written. An agent
    wiring `pipeline.json` from one of the three gets a pipeline missing a rule.
    """
    binding = ROOT / "plugins/agent-sync/skills/agent-sync/references/pipeline-binding.md"
    text = binding.read_text() if binding.exists() else ""
    m = STAGE_MARKER.search(text)
    if not m:
        err("stage binding: pipeline-binding.md carries no `agent-sync:stages` marker — "
            "the numbers live in prose in three files, and they have already disagreed")
        return
    rules = [s for s in m.group(1).split(",") if s]
    wired = [s for s in m.group(2).split(",") if s]

    for stage in wired:
        if f'"id": {stage},' not in text:
            err(f"stage binding: stage {stage} is declared wired but the pipeline.json "
                f"example in pipeline-binding.md has no `\"id\": {stage}` entry")

    skill = (ROOT / "plugins/agent-sync/skills/agent-sync/SKILL.md").read_text()
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven"}
    if f"{words.get(len(rules), '?')} of the eleven stages" not in skill:
        err(f"stage binding: SKILL.md does not say '{words.get(len(rules))} of the eleven "
            f"stages' — the marker declares {len(rules)} stages carrying a rule")
    for stage in rules:
        if f"**{stage}**" not in skill:
            err(f"stage binding: SKILL.md's binding paragraph never names stage {stage}")

    readme = (ROOT / "README.md").read_text()
    spelled = ", ".join(wired[:-1]) + " and " + wired[-1]
    if spelled not in readme:
        err(f"stage binding: README does not name the wired stages as '{spelled}'")


def check_status_reports_the_setup_verdict() -> None:
    """`status` and `check` must not disagree about whether this project is healthy.

    They did: `status` printed "NEXT: acquire a lease" and exited 0 on a project `check`
    called NOT healthy. `status` is the command every session runs and the only one most
    agents ever see, so a defect it stays quiet about is a defect nobody hears.
    """
    if not shutil.which("git"):
        notes.append("git not found — status verdict check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("status verdict: init failed")
            return
        # A guard pattern that matches nothing: `check` calls this a problem.
        _write_cfg(project, guardedFiles=["docs/NOTHING_HERE.md"])
        if _run_script(project, "check").returncode == 0:
            err("status verdict: the fixture is wrong — `check` considers this setup healthy")
            return
        out = _run_script(project, "status")
        text = out.stdout + out.stderr
        # Assert the PROBLEM is named, not merely that the exit code is non-zero: on a
        # machine without task-pipeline `status` already exited 1 for an unrelated reason,
        # and a check that only reads the code passes while the project defect stays
        # invisible. That is how the ordering bug reached CI.
        if out.returncode == 0 or "guards nothing" not in text:
            err("status verdict: `status` reported normally on a setup `check` fails — two "
                "commands, two answers about one project, and the agent only reads the first")


def check_env_discovery_is_bounded() -> None:
    """Credentials are not picked up from an unrelated directory above the project.

    `find_env_file` walked every parent until it happened to find one, so a stray
    `.env.agent-sync` in a home or work directory silently configured every project
    beneath it — pointing them all at one collection, which is a coordination plane
    shared by projects that have nothing to do with each other.
    """
    if not shutil.which("git"):
        notes.append("git not found — env discovery check skipped")
        return
    with tempfile.TemporaryDirectory() as outer:
        stray = Path(outer) / ".env.agent-sync"
        stray.write_text("AGENT_SYNC_BACKEND=outline\n"
                         "AGENT_SYNC_OUTLINE_URL=http://127.0.0.1:1\n"
                         "AGENT_SYNC_OUTLINE_TOKEN=stray\n")
        project = Path(outer) / "nested" / "project"
        project.mkdir(parents=True)
        _git_project(str(project))
        if _run_script(str(project), "init", "--backend", "fs").returncode != 0:
            err("env discovery: init failed")
            return
        (project / ".env.agent-sync").unlink(missing_ok=True)

        out = _run_script(str(project), "check")
        text = out.stdout + out.stderr
        if str(stray) in text and "outside" not in text.lower():
            err("env discovery: a credentials file from outside the project tree was adopted "
                "without a word — every project under that directory shares one plane")

        # And the explicit override must win, because deterministic beats discovered.
        named = Path(outer) / "named.env"
        named.write_text("AGENT_SYNC_BACKEND=fs\n")
        out = _run_script(str(project), "check", env={"AGENT_SYNC_ENV": str(named)})
        if "named.env" not in (out.stdout + out.stderr):
            err("env discovery: AGENT_SYNC_ENV names a credentials file and the tool ignored "
                "it — there is no way to be explicit about which one is in force")


def check_watermark_survives_a_late_entry() -> None:
    """"New since you last looked" must not lose an entry that arrives out of order.

    The watermark was an INDEX into a list re-sorted on every read. An entry appended by
    another run with an earlier timestamp — clock skew, or a shard that was unreachable a
    moment ago — lands before the mark, shifts everything after it, and is never reported:
    the slice returns an entry this run has already seen instead. The one section of
    `status` that exists to say "something changed while you were away" then goes quiet
    about exactly the change that arrived late.
    """
    if not shutil.which("git"):
        notes.append("git not found — watermark check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("watermark: init failed")
            return
        state = Path(project) / ".agent-sync"
        state.mkdir(exist_ok=True)
        line = ("- `2026-08-0{d}T10:00:00Z` `op=signal` `key=DEP-00{d}` `run=r-early` "
                "`state=filed` `repo=x`\n")
        (state / "50-signals-r-early.md").write_text("".join(line.format(d=d) for d in (5, 6, 7)))

        first = _run_script(project, "status")
        if "DEP-005" not in first.stdout and "New since" not in first.stdout:
            notes.append("watermark: the fixture produced no first-look report")

        # A fourth signal, timestamped BEFORE the three already seen.
        (state / "50-signals-r-late.md").write_text(
            "- `2026-08-01T09:00:00Z` `op=signal` `key=DEP-999` `run=r-late` "
            "`state=delivered` `repo=x`\n")
        second = _run_script(project, "status")
        if "DEP-999" not in second.stdout:
            err("watermark: an entry that arrived out of order was never reported as new — "
                "the awareness section is silent about the change it exists to announce")


def check_no_orphan_logs() -> None:
    """Every log the tool names must have something that writes it.

    `LOGS` carried a `blockers` document for five versions. Nothing ever appended to it and
    nothing ever read it, so a reader looking for blockers found an empty page and
    concluded there were none.
    """
    mod = _load_script("agent_sync_logs_probe")
    script = (ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py").read_text()
    for key in getattr(mod, "LOGS", {}):
        if f'log_id("{key}")' not in script and f'_publish("{key}"' not in script:
            err(f"LOGS['{key}'] names a document nothing writes — an empty page reads as "
                "'nothing to report', which is a different statement")


def check_no_dead_declarations() -> None:
    """A declared knob or helper that nothing uses is a promise the code does not keep."""
    script = (ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py").read_text()
    for symbol in ("_held_legacy", "is_exclusive", "self.settle"):
        if symbol in script:
            err(f"{symbol} survives in the coordinator but nothing calls it — remove it or "
                "give it work; a reader assumes it is load-bearing")
    # Portability: the coordinator is imported and run wherever python3 is.
    for token in ("os.uname(", '"/dev/null"'):
        if token in script:
            err(f"{token} is POSIX-only and the compatibility line does not say so")


def check_claim_round_trip_is_byte_exact() -> None:
    """`acquire` then `release` must leave the registry file exactly as it was.

    SKILL.md promises `git diff` empty after a round-trip. The row was rebuilt from its
    cells, so indentation and the original line ending were dropped — a diff on a shared
    file that nobody made, on the one file agents are told never to touch casually.
    """
    if not shutil.which("git"):
        notes.append("git not found — claim round-trip check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        roadmap = Path(project) / "ROADMAP.md"
        # An indented row, and a final line with no trailing newline.
        roadmap.write_text("| Task | State |\n|---|---|\n  | T-1 | todo |\n| T-2 | todo |")
        before = roadmap.read_bytes()
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("claim round-trip: init failed")
            return
        _write_cfg(project, claimTags={"ROADMAP.md": {"mode": "cell", "cell": -1,
                                                     "held": "{prev} (claimed: {holder})"}})
        _run_script(project, "acquire", "T-1", run_id="rt")
        if roadmap.read_bytes() == before:
            err("claim round-trip: the fixture never wrote the claim through, so the "
                "restore proves nothing")
        _run_script(project, "release", "T-1", run_id="rt")
        if roadmap.read_bytes() != before:
            err("claim round-trip: release did not restore the file byte for byte — "
                f"before {before!r}, after {roadmap.read_bytes()!r}")


def check_claim_lands_on_the_row_whose_id_cell_matches() -> None:
    """A row that CITES another id must not defeat that id's claim tag.

    Boards cross-reference constantly — "closed by B-12", "supersedes B-07" — and the
    row selector matched the id anywhere in the line, so every cited id had two or more
    candidate rows and the tag was refused. Measured on the family's own board on
    2026-08-14: 14 rows cited others, so **19 of 41 ids could never be tagged**.

    The refusal itself was right; what was wrong is that `acquire` still returned `won`.
    The lease was granted while the registry silently carried no claim, which is the
    state the claim exists to make visible.

    A markdown board says which row is which in its FIRST cell. That is the answer, and
    it needs no configuration.
    """
    if not shutil.which("git"):
        notes.append("git not found — claim id-cell check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        roadmap = Path(project) / "ROADMAP.md"
        # T-1 appears twice: its own row, and T-2 citing it — the ordinary shape of a board.
        roadmap.write_text(
            "| Task | State |\n|---|---|\n"
            "| T-1 | todo |\n"
            "| T-2 | blocked until T-1 ships |\n"
        )
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("claim id-cell: init failed")
            return
        _write_cfg(project, claimTags={"ROADMAP.md": {"mode": "cell", "cell": -1,
                                                     "held": "{prev} (claimed: {holder})"}})
        before = roadmap.read_text()
        _run_script(project, "acquire", "T-1", run_id="idcell")
        after = roadmap.read_text()
        if after == before:
            err("claim id-cell: acquire wrote no claim — a row citing T-1 defeated the "
                "selector, and the lease was granted with the registry unmarked")
            return
        rows = [l for l in after.splitlines() if l.startswith("| T-")]
        tagged = [l for l in rows if "(claimed:" in l]
        if len(tagged) != 1:
            err(f"claim id-cell: {len(tagged)} rows carry the tag, expected exactly 1")
            return
        if not tagged[0].split("|")[1].strip() == "T-1":
            err(f"claim id-cell: the tag landed on {tagged[0].split('|')[1].strip()!r}, "
                "not on the row whose id cell is T-1")
            return
        _run_script(project, "release", "T-1", run_id="idcell")
        if roadmap.read_text() != before:
            err("claim id-cell: release did not restore the file — "
                f"before {before!r}, after {roadmap.read_text()!r}")


def check_release_notes_are_extractable() -> None:
    """The release workflow must be able to find this version's CHANGELOG section.

    It could not, for three releases. The CHANGELOG writes `## v1.7.0`; the extraction
    matched only `## 1.7.0`. So v1.5.0, v1.5.1 and v1.5.2 each pushed a tag, failed at
    that step, and never published — npm sat three releases behind while every tag looked
    delivered, and the failure lived in the one place CI does not run on main.

    This runs the workflow's OWN awk program, lifted out of the YAML, so the check cannot
    drift from the thing it checks.
    """
    if not shutil.which("awk"):
        notes.append("awk not found — release-notes check skipped")
        return
    wf = ROOT / ".github" / "workflows" / "release.yml"
    if not wf.exists():
        err(".github/workflows/release.yml: missing — nothing releases this package")
        return
    m = re.search(r"awk -v v=\"[^\"]*\" '\n(.*?)\n\s*' CHANGELOG\.md", wf.read_text(), re.S)
    if not m:
        err("release notes: cannot find the extraction program in release.yml — this check "
            "exists to run the real one, and a rewritten step must keep it findable")
        return
    program = "\n".join(line.strip() for line in m.group(1).splitlines())
    version = json.loads((ROOT / "package.json").read_text())["version"]

    out = subprocess.run(["awk", "-v", f"v={version}", program, str(ROOT / "CHANGELOG.md")],
                         capture_output=True, text=True)
    if out.returncode != 0:
        err(f"release notes: the extraction program failed ({out.stderr.strip()[:120]})")
        return
    if not out.stdout.strip():
        err(f"release notes: no CHANGELOG section found for {version} — the tag will be "
            "pushed, the workflow will fail at that step, and nothing will publish")
    # And it must STOP at the next heading, or the notes carry the whole history.
    # The pattern accepts the bracketed form too: the extraction's own terminator
    # stops at `## [1.9.0]`, so a spill check blind to brackets could not see the
    # one case the terminator was widened to handle.
    headings = [l for l in out.stdout.splitlines() if re.match(r"^## \[?v?\d", l)]
    if headings:
        err(f"release notes: the extracted section runs past its own version into "
            f"{headings[0]!r} — the stop pattern does not match the heading style")


def check_every_advertised_verb_exists() -> None:
    """A command an agent is told to run must be a command the CLI has.

    The slash command's `argument-hint` offered `claim <KEY>` and the README's everyday
    table showed `/agent-sync claim ASC-072`. The CLI has `acquire`; `claim` is an
    `invalid choice`. It is the first thing an agent reads and the first thing it types.
    """
    mod = _load_script("agent_sync_verbs")
    real = set(mod.build_parser()._subparsers._group_actions[0].choices)  # noqa: SLF001
    surfaces = {
        "commands/agent-sync.md": ROOT / "plugins" / "agent-sync" / "commands" / "agent-sync.md",
    }
    for label, path in surfaces.items():
        if not path.exists():
            continue
        hint = re.search(r"argument-hint:\s*\"\[([^\]]+)\]\"", path.read_text())
        if not hint:
            continue
        for token in hint.group(1).split("|"):
            verb = token.strip().split()[0] if token.strip() else ""
            if verb and verb not in real:
                err(f"{label}: advertises `{verb}`, which is not a CLI command "
                    f"(closest real ones: {', '.join(sorted(real)[:6])}…)")

    # The slash command only — the character before it must not be a word character, or
    # `npx @ssheleg/agent-sync install` reads as an invocation of a verb called `install`.
    readme = (ROOT / "README.md").read_text()
    for m in re.finditer(r"(?<![\w@/-])/agent-sync (\w[\w-]*)", readme):
        verb = m.group(1)
        if verb not in real:
            err(f"README.md: shows `/agent-sync {verb}`, which is not a CLI command")


def check_generated_docs_carry_current_doctrine() -> None:
    """What the tool WRITES into a project must not lag what the skill teaches.

    `setup` generates the snapshot every agent is told to read first — "it states how
    documentation and coordination work here" — and `scaffold` seeds `AGENTS.md`. Since
    1.4.0 the doctrine is: work on a branch, land it with `merge`, and the claim is
    written through **only** on the integration branch. Neither generated document
    mentioned a branch or `merge` even once, and the snapshot stated the claim tag is
    written through as an unconditional fact. An agent that does exactly what the skill
    tells it — trust the generated snapshot — gets the workflow from two versions ago,
    and regenerating does not help, because the generator is what is stale.
    """
    script = (ROOT / "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py").read_text()
    for marker, label in (("def setup_snapshot", "the setup snapshot"),
                          ("AGENTS_SEED = ", "the seeded AGENTS.md")):
        block = script.split(marker, 1)[1] if marker in script else ""
        block = block[:6000]
        if "merge" not in block.lower():
            err(f"{label} never mentions `merge` — it prescribes a cycle ending in "
                "`release` while the skill's branch discipline says work lands with "
                "`merge`, and it is the document agents are told to read first")


def check_registers_need_a_backend_that_can_reserve() -> None:
    """A register nobody can reserve from is a rule that protects nothing.

    `check`'s own promise is that it refuses to call a setup healthy on a rule pointing at
    what is not there. A project declaring `idRegisters` on a backend whose `reserve`
    always raises passed as healthy — and the snapshot it generates then tells every agent
    to run `agent_sync.py reserve DEC`, which cannot succeed in that project.
    """
    if not shutil.which("git"):
        notes.append("git not found — register/backend check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        (Path(project) / "DECISIONS.md").write_text(
            "# Decisions\n\n**Next free ID:** `DEC-0001`\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("register/backend: init failed")
            return
        _write_cfg(project, idRegisters={"DEC": {
            "file": "DECISIONS.md",
            "nextFreeIdPattern": r"\*\*Next free ID:\*\* `DEC-(\d{4})`"}})

        reserve = _run_script(project, "reserve", "DEC")
        if reserve.returncode == 0:
            notes.append("register/backend: this backend can reserve; the check is moot here")
            return
        out = _run_script(project, "check")
        if "cannot reserve" not in out.stdout:
            err("check: called a setup healthy in which the declared register can never be "
                "reserved — `reserve DEC` fails every time, and the generated snapshot "
                "instructs every agent to run it")


def check_skill_gives_a_resolvable_script_path() -> None:
    """`$SKILL_DIR` appears in every command example and is defined nowhere.

    Six invocations tell the agent to run `python3 "$SKILL_DIR/scripts/agent_sync.py"`, and
    the only explanation is the prose "this skill's own directory". Nothing gives a value:
    not `${CLAUDE_PLUGIN_ROOT}`, not a discovery command. The Cursor rule names a concrete
    path; the skill body does not, so the agent guesses or searches the filesystem at the
    point of its very first command.
    """
    md = (ROOT / "plugins/agent-sync/skills/agent-sync/SKILL.md").read_text()
    if "$SKILL_DIR" not in md:
        return
    if not re.search(r"CLAUDE_PLUGIN_ROOT|\.agents/skills/agent-sync|"
                     r"resolve .{0,40}\$SKILL_DIR|SKILL_DIR=", md):
        err("SKILL.md: uses $SKILL_DIR in every command example without giving one "
            "resolvable value or a way to find it — the agent guesses at the first step")


def check_commands_work_without_the_family_installed() -> None:
    """Run the commands as a machine that has none of this skill family.

    That is every CI runner, and it is where `status` was found reporting the
    task-pipeline gate *before* the project's own problems — so on any such machine a
    defect in the repository was invisible behind a fact about the box. The class is
    broader than that one bug: this development machine has every dependency, so any
    behaviour that only appears without them ships unseen. `HOME` is redirected, which is
    what `pipeline_installed()` reads.
    """
    if not shutil.which("git"):
        notes.append("git not found — bare-machine check skipped")
        return
    with tempfile.TemporaryDirectory() as bare:
        home = Path(bare) / "home"
        home.mkdir()
        project = Path(bare) / "project"
        project.mkdir()
        _git_project(str(project))
        isolated = {"HOME": str(home)}
        if _run_script(str(project), "init", "--backend", "fs",
                       env=isolated).returncode != 0:
            err("bare machine: init failed without the family installed")
            return

        # First prove the isolation itself, on a project with nothing else wrong: a
        # healthy setup reaches the machine gate, so the message is the receipt that
        # `HOME` really was redirected. Asserting it on the broken project below would be
        # a false alarm — `status` returns at the project verdict and never gets there.
        _run_script(str(project), "setup", env=isolated)
        (project / "AGENTS.md").write_text("See AGENT_SYNC.md\n")
        healthy = _run_script(str(project), "status", env=isolated)
        if "task-pipeline is not installed" not in (healthy.stdout + healthy.stderr):
            notes.append("bare machine: HOME was redirected but the family was still "
                         "found — this check is not proving what it claims")

        # Now the assertion: a project defect must be reported even though the machine is
        # missing a dependency, because otherwise no CI runner can ever see one.
        _write_cfg(str(project), guardedFiles=["docs/NOTHING_HERE.md"])
        out = _run_script(str(project), "status", env=isolated)
        text = out.stdout + out.stderr
        if "guards nothing" not in text:
            err("bare machine: `status` never reported the project's own problem — it is "
                "behind a gate about the machine, so on any runner the defect is invisible")
        if out.returncode == 0:
            err("bare machine: `status` exited 0 on a project it should have failed")


def check_two_agents_cannot_share_one_task() -> None:
    """The whole purpose of this tool, driven end to end by two identities.

    Every part of it was verified by hand in the 2026-08-10 audits and none of it had a
    check that fails on its own — which is the exact state in which that audit found six
    shipped defects. "It worked when I tried it" is evidence about that afternoon.
    """
    if not shutil.which("git"):
        notes.append("git not found — two-agent scenario skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        (Path(project) / "DECISIONS.md").write_text("# Decisions\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("two agents: init failed")
            return
        _write_cfg(project, guardedFiles=["DECISIONS.md"])

        first = _run_script(project, "acquire", "TASK-1", run_id="alice")
        if "won" not in first.stdout:
            err("two agents: the first run could not take the task")
            return

        second = _run_script(project, "acquire", "TASK-1", run_id="bob")
        if second.returncode == 0 or "lost" not in second.stdout:
            err("two agents: the second run also took a task the first one holds — the "
                "lease is decoration")
        if "alice" not in second.stdout:
            err("two agents: the loser is not told who holds it, so it knows it is blocked "
                "and nothing about by whom")

        aware = _run_script(project, "status", run_id="bob")
        if "TASK-1" not in aware.stdout or "alice" not in aware.stdout:
            err("two agents: `status` does not show the other run's holding — awareness is "
                "the half of coordination that is not exclusion")

        denied = _run_script(project, "guard", "DECISIONS.md", run_id="bob")
        if denied.returncode != 2:
            err("two agents: the run holding nothing may write the guarded registry")

        allowed = _run_script(project, "guard", "DECISIONS.md", run_id="alice")
        if allowed.returncode != 0:
            err("two agents: the holder is denied its own guarded file")

        stolen = _run_script(project, "release", "TASK-1", run_id="bob")
        if stolen.returncode == 0:
            err("two agents: a run released a lease it never held")
        if "TASK-1" not in _run_script(project, "whoami", run_id="alice").stdout:
            err("two agents: the holder lost its lease to a run that did not hold it")

        if _run_script(project, "release", "TASK-1", run_id="alice").returncode != 0:
            err("two agents: the holder could not release its own lease")


GUARD_SHAPES = [
    # (label, payload, expect_blocked)
    ("Edit", '{"tool_name":"Edit","tool_input":{"file_path":"DECISIONS.md"}}', True),
    ("Write", '{"tool_name":"Write","tool_input":{"file_path":"DECISIONS.md"}}', True),
    ("NotebookEdit",
     '{"tool_name":"NotebookEdit","tool_input":{"notebook_path":"DECISIONS.md"}}', True),
    ("an unguarded file", '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}', False),
    ("git commit", '{"tool_name":"Bash","tool_input":{"command":"git commit -m wip"}}', True),
    ("git -C <dir> commit",
     '{"tool_name":"Bash","tool_input":{"command":"git -C %(dir)s commit -m wip"}}', True),
    ("cd <dir> && git commit",
     '{"tool_name":"Bash","tool_input":{"command":"cd %(dir)s && git commit -m wip"}}', True),
    ("git log --grep=commit",
     '{"tool_name":"Bash","tool_input":{"command":"git log --grep=commit"}}', False),
    # ASY-05: the tokenizer's own comment claimed `|` while only `||` was consumed, so a
    # commit fed through a pipe was one segment starting with `echo` and skipped the
    # guard entirely. A pipe is the ordinary way to commit a generated message.
    ("echo msg | git commit -F -",
     '{"tool_name":"Bash","tool_input":{"command":"echo msg | git commit -F -"}}', True),
    ("echo msg |& git commit -F -",
     '{"tool_name":"Bash","tool_input":{"command":"echo msg |& git commit -F -"}}', True),
    # And the pipe that is NOT a commit must stay unblocked, or the fix trades a bypass
    # for a guard that refuses ordinary reads.
    ("git log | grep commit",
     '{"tool_name":"Bash","tool_input":{"command":"git log | grep commit"}}', False),
    ("malformed input", "not json at all", False),
]


def check_guard_covers_every_write_shape() -> None:
    """The enforcement path agents actually hit, in every shape it arrives in.

    `guard.sh` shipped for eleven versions blocking every commit in unconfigured
    repositories, and separately missed `git -C <dir> commit` entirely for a full day of
    commits to guarded registers — with the Edit half refusing correctly the whole time,
    so the protection looked present. Shapes are cheap to add and expensive to miss.
    """
    hooks = ROOT / "plugins" / "agent-sync" / "hooks"
    if not shutil.which("git") or not (hooks / "guard.sh").exists():
        notes.append("git or guard.sh not found — guard shape check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        (Path(project) / "DECISIONS.md").write_text("# Decisions\n")
        (Path(project) / "README.md").write_text("# readme\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("guard shapes: init failed")
            return
        _write_cfg(project, guardedFiles=["DECISIONS.md"])
        subprocess.run(["git", "add", "DECISIONS.md"], cwd=project, capture_output=True)

        env = {**os.environ,
               "CLAUDE_PLUGIN_ROOT": str(ROOT / "plugins" / "agent-sync"),
               "CLAUDE_PROJECT_DIR": project,
               "AGENT_SYNC_RUN_ID": "nobody"}
        for label, payload, blocked in GUARD_SHAPES:
            r = subprocess.run(["bash", str(hooks / "guard.sh")],
                               input=payload % {"dir": project} if "%(dir)s" in payload
                               else payload,
                               cwd=project, env=env, capture_output=True, text=True,
                               timeout=60)
            if blocked and r.returncode != 2:
                err(f"guard shapes: `{label}` reached a guarded file with no lease "
                    f"(exit {r.returncode}) — anything but 2 is non-blocking")
            if not blocked and r.returncode != 0:
                err(f"guard shapes: `{label}` was blocked and should not be "
                    f"(exit {r.returncode})")

        # And with the lease held, the same writes go through.
        _run_script(project, "acquire", "SHAPE-1", run_id="holder")
        env["AGENT_SYNC_RUN_ID"] = "holder"
        for label, payload, blocked in GUARD_SHAPES:
            if not blocked:
                continue
            r = subprocess.run(["bash", str(hooks / "guard.sh")],
                               input=payload % {"dir": project} if "%(dir)s" in payload
                               else payload,
                               cwd=project, env=env, capture_output=True, text=True,
                               timeout=60)
            if r.returncode != 0:
                err(f"guard shapes: `{label}` is denied to the run that holds the lease — "
                    "the guarded files are unwritable by anyone")


def check_merge_refuses_without_an_identity() -> None:
    """No committer identity is a preflight failure, not a mid-merge abort.

    Found by CI: a runner has no global `.gitconfig`, so `git merge` refuses at the commit
    and `merge` took the abort path. Recoverable, and still not what a command whose whole
    doctrine is "every check before anything is touched" promises.
    """
    if not shutil.which("git"):
        notes.append("git not found — merge identity check skipped")
        return
    with tempfile.TemporaryDirectory() as box:
        project = str(Path(box) / "project")
        Path(project).mkdir()
        # A machine with no identity anywhere — not the repository, not a global file,
        # not the system one. Unsetting the repo keys is not enough: this developer's
        # `~/.gitconfig` supplied one and the fixture passed while proving nothing.
        bare = {"HOME": str(Path(box) / "home"),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1"}
        (Path(box) / "home").mkdir()
        genv = {**os.environ, **bare}
        for argv in (["git", "init", "-q", "-b", "main"],
                     ["git", "config", "user.useConfigOnly", "true"],
                     ["git", "-c", "user.email=v@e", "-c", "user.name=v",
                      "commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(argv, cwd=project, env=genv, capture_output=True)
        if _run_script(project, "init", "--backend", "fs", env=bare).returncode != 0:
            err("merge identity: init failed")
            return
        subprocess.run(["git", "checkout", "-q", "-b", "feature/id"], cwd=project,
                       env=genv, capture_output=True)
        (Path(project) / "x.txt").write_text("x\n")
        subprocess.run(["git", "add", "-A"], cwd=project, env=genv, capture_output=True)
        subprocess.run(["git", "-c", "user.email=v@e", "-c", "user.name=v",
                        "commit", "-q", "-m", "work"], cwd=project, env=genv,
                       capture_output=True)

        out = _run_script(project, "merge", "--key", "ID-1", "--summary", "s", env=bare)
        text = out.stdout + out.stderr
        if out.returncode == 0:
            err("merge identity: merged with no committer identity configured")
        if "identity" not in text.lower():
            err("merge identity: refused without naming the reason or the fix")
        on = subprocess.run(["git", "branch", "--show-current"], cwd=project,
                            env=genv, capture_output=True, text=True).stdout.strip()
        if on != "feature/id":
            err(f"merge identity: left the repository on '{on}' — the refusal must touch "
                "nothing, including which branch you are standing on")
        if (Path(project) / "docs" / "MERGES.md").exists():
            err("merge identity: recorded a merge that never happened")


def check_merge_releases_only_its_key() -> None:
    """`merge --key` releases that lease and leaves the others held.

    It used to release every lease the run held, which is a different statement from the
    one the documentation makes and quietly frees work that has not landed.
    """
    if not shutil.which("git"):
        notes.append("git not found — merge key-release check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        (Path(project) / "f.txt").write_text("base\n")
        subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True)
        subprocess.run(["git", "-c", "user.email=v@e", "-c", "user.name=v",
                        "commit", "-q", "-m", "base"], cwd=project, capture_output=True)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("merge key-release: init failed")
            return
        subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True)
        subprocess.run(["git", "-c", "user.email=v@e", "-c", "user.name=v",
                        "commit", "-q", "-m", "cfg"], cwd=project, capture_output=True)

        for key in ("LANDS-1", "STAYS-2"):
            if "won" not in _run_script(project, "acquire", key).stdout:
                err(f"merge key-release: could not acquire {key}")
                return

        subprocess.run(["git", "checkout", "-q", "-b", "feature/z"], cwd=project,
                       capture_output=True)
        (Path(project) / "g.txt").write_text("mine\n")
        subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True)
        subprocess.run(["git", "-c", "user.email=v@e", "-c", "user.name=v",
                        "commit", "-q", "-m", "mine"], cwd=project, capture_output=True)

        out = _run_script(project, "merge", "--key", "LANDS-1", "--summary", "landed")
        if out.returncode != 0:
            # stderr first: the reason lives there, and printing the tail of stdout gave
            # "…1 file changed, 1 insertion(+)" as the explanation for a failure.
            why = (out.stderr.strip() or out.stdout.strip()).splitlines()
            err(f"merge key-release: the merge itself failed — {why[-1] if why else '(silent)'}")
            return
        held = _run_script(project, "whoami").stdout
        if "LANDS-1" in held:
            err("merge key-release: the lease the merge landed is still held")
        if "STAYS-2" not in held:
            err("merge key-release: a lease this merge did not land was released — work "
                "that has not landed is now advertised as free")


def check_the_tarball_carries_no_bytecode():
    """`files` in package.json overrides .gitignore AND .npmignore. Both said no.

    B-72, 2026-08-16: the published 1.11.1 carried
    `plugins/agent-sync/skills/agent-sync/scripts/__pycache__/agent_sync.cpython-312.pyc`
    — 245.8 kB against 175.7 kB of source beside it, 40% of the tarball, and built
    by whatever interpreter the publisher happened to run. `.gitignore:3-4` and
    `.npmignore:1-2` both exclude it; neither is consulted once `files` names a
    directory, so the intent was recorded twice and enforced nowhere. Every `npx
    @ssheleg/agent-sync` downloaded an unreviewed binary.

    Asking npm what it would ship is the only honest form of this check: a
    filesystem walk answers a different question, because the whole defect is the
    gap between what is on disk and what the packer selects. When npm is absent
    this discloses rather than passing — a check that cannot look must never read
    as one that looked.
    """
    npm = shutil.which("npm")
    if not npm:
        notes.append("tarball contents — npm is not on PATH here")
        return
    try:
        proc = subprocess.run([npm, "pack", "--dry-run", "--json"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"tarball contents — could not run `npm pack` ({exc})")
        return
    if proc.returncode != 0:
        notes.append("tarball contents — `npm pack --dry-run` exited "
                     f"{proc.returncode}")
        return
    try:
        entries = json.loads(proc.stdout)[0]["files"]
    except (ValueError, KeyError, IndexError) as exc:
        notes.append(f"tarball contents — could not read npm's file list ({exc})")
        return
    bad = [e["path"] for e in entries
           if e["path"].endswith(".pyc") or "__pycache__" in e["path"].split("/")]
    if bad:
        err("package.json files[]: the tarball would ship generated bytecode — "
            + ", ".join(sorted(bad)[:5])
            + " — .gitignore and .npmignore both exclude it and neither is consulted "
              "once files[] names a directory. Add `!plugins/**/__pycache__` and "
              "`!plugins/**/*.pyc` to files[].")


def check_routed_triggers_still_advertised():
    """The family's routing hook fires on words this description has to keep.

    B-54, 2026-08-16: `sheleg-design` 1.37.0 shipped green on its own gate having dropped
    a phrase from its description that was a live trigger in the umbrella's
    `lib/triggers.js`. This repository has no way to know that table exists, and it
    releases BEFORE the umbrella re-pins, so the umbrella found out minutes after the tag.
    A hook firing on a promise nobody made is the defect; a patch release was the cost.

    **The table is not copied here.** The umbrella's own checker is asked, reading the
    module the hook itself calls, so there is no duplicate to drift. When no umbrella sits
    above this checkout — the ordinary state of a standalone clone, and of CI — this
    discloses instead of passing, because a check that cannot look must never read as one
    that looked.
    """
    script = os.path.join(str(ROOT), "..", "..", "test", "advertised_check.js")
    if not os.path.isfile(script):
        notes.append("routed triggers — no sshlg-skills umbrella above this checkout")
        return
    try:
        proc = subprocess.run(["node", script, "--member", "agent-sync", "--root", str(ROOT)],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        notes.append(f"routed triggers — could not run the umbrella's checker ({exc})")
        return
    if proc.returncode == 1:
        err((proc.stdout + proc.stderr).strip())
    elif proc.returncode != 0:
        notes.append(f"routed triggers — {(proc.stderr or 'the checker could not look').strip()}")


# ---------------------------------------------------------------- residue (AS-01)
#
# A run produces more than a diff. What it leaves on disk is part of the result, and until
# 1.13.0 nothing here could see it: every reader of lease state folds the TTL into the read,
# so `held()`, `_lease_holder()` and `all_holdings()` each answer *none* for an expired lock
# and *none* for a lock that is not there. Measured across the nine repositories of one
# family on 2026-08-19: seventeen lock files, all seventeen expired, the oldest by three
# days — and `status` printed `leases held: none` in a checkout holding three of them while
# `finish` printed "✓ no lease left held" beside a two-day-expired one.


def _plant_lock(project: str, key: str, **payload) -> Path:
    """A lock file exactly as `acquire` writes one, with whatever fields a case needs."""
    leases = Path(project) / ".agent-sync" / "leases"
    leases.mkdir(parents=True, exist_ok=True)
    p = leases / f"{key}.lock"
    p.write_text(payload.pop("raw", None) or json.dumps(payload))
    return p


def _rid_of(project: str, run_id: str) -> str:
    """Ask the tool who it is. Recomputing the rule here would make this fixture a second
    home for it, and the two would drift the first time identity changes shape."""
    out = _run_script(project, "whoami", run_id=run_id).stdout.split()
    return out[1] if len(out) > 1 else ""


def check_status_reports_expired_locks_as_residue() -> None:
    """`status` must not report `none` over a directory of expired locks.

    This is the defect as it shipped: the command every session runs read the lease
    directory, applied the TTL, found nothing *held* and said so — which is true and reads
    as "the directory is empty". Nothing else enumerated, so seventeen corpses across one
    family were reported by no command at all.
    """
    if not shutil.which("git"):
        notes.append("git not found — residue/status check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("residue/status: init failed")
            return
        rid = _rid_of(project, "resident")
        old = "2026-01-01T00:00:00Z"
        _plant_lock(project, "OWN-1", run=rid, ts=old, ttl=60)
        _plant_lock(project, "OTHER-1", run="r-somebodyelse", ts=old, ttl=60)
        out = _run_script(project, "status", run_id="resident").stdout
        if "leases held    : none" not in out:
            err("residue/status: the fixture holds no live lease, so `status` should still "
                "report `leases held: none` — the assertion below proves nothing otherwise")
        for key in ("OWN-1", "OTHER-1"):
            if key not in out:
                err(f"status: {key}.lock is expired and on disk and `status` never names it "
                    "— an expired lease is residue the next agent has to reason about, and "
                    "`leases held: none` reads as an empty directory")
        if "expired locks  : none" in out:
            err("status: reports `expired locks: none` with two expired locks in the "
                "directory it just read")


def check_residue_ownership_must_be_provable() -> None:
    """Reapable means PROVABLY this run's and PROVABLY spent. Everything else is reported.

    The manifesto requirement this closes (M-49) splits residue in two, and the split is the
    whole mechanism: state a run can prove it owns and has spent may be cleared; foreign or
    ambiguously owned state is reported and left alone. A classifier that resolves doubt by
    deleting is worse than no classifier, because it deletes under a claim of authority.
    """
    mod = _load_script("agent_sync_residue")
    now = 1_000_000.0            # 1970-01-12T13:46:40Z, so every date below is explicit

    def verdict(raw=None, identity_is_strong=True, **over):
        payload = {"run": "r-mine", "ts": "1970-01-01T00:10:00Z", "ttl": 60,
                   "host": "thisbox"}
        payload.update(over)
        payload = {k: v for k, v in payload.items() if v is not None}
        return mod.classify_lock(
            "K", raw if raw is not None else json.dumps(payload), rid="r-mine",
            identity_is_strong=identity_is_strong, repo="here", host="thisbox",
            default_ttl=2700, at=now)

    cases = [
        ("a live lease of this run", dict(ts="1970-01-12T13:46:40Z", ttl=2700), "live"),
        ("this run's expired lock", dict(), "reapable"),
        ("another run's expired lock", dict(run="r-theirs"), "foreign"),
        ("an expired lock from another machine", dict(host="otherbox"), "foreign"),
        ("this run's id under the shared identity", dict(identity_is_strong=False), "ambiguous"),
        ("a matching run in another repository", dict(repo="elsewhere"), "ambiguous"),
        ("an expired lock naming no run", dict(run=None), "ambiguous"),
        ("a lock that is not JSON", dict(raw="{not json"), "ambiguous"),
        ("a lock whose timestamp is not one", dict(ts="yesterday"), "ambiguous"),
        ("a lock whose ttl is not a number", dict(ttl="soon"), "ambiguous"),
    ]
    for label, over, want in cases:
        got = verdict(**over)
        if got["state"] != want:
            err(f"residue ownership: {label} classified `{got['state']}`, must be `{want}` "
                f"— {got['why']}")
        if want != "live" and not got["why"]:
            err(f"residue ownership: {label} is reported with no reason, so an operator "
                "cannot tell why it was left alone")

    # And the same rule where it actually costs something: through the command, on disk.
    if not shutil.which("git"):
        notes.append("git not found — residue/reap disk check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("residue/reap: init failed")
            return
        rid = _rid_of(project, "resident")
        old = "2026-01-01T00:00:00Z"
        mine = _plant_lock(project, "MINE", run=rid, ts=old, ttl=60)
        theirs = _plant_lock(project, "THEIRS", run="r-somebodyelse", ts=old, ttl=60)
        nameless = _plant_lock(project, "NAMELESS", ts=old, ttl=60)
        broken = _plant_lock(project, "BROKEN", raw="{ not json")
        live = _plant_lock(project, "LIVE", run="r-somebodyelse",
                           ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ttl=2700)

        r = _run_script(project, "reap", run_id="resident")
        if r.returncode != 0:
            err(f"reap: exited {r.returncode} clearing one lock it owns: {r.stdout}{r.stderr}")
        if mine.exists():
            err("reap: left this run's own spent lock in place — the reapable half of the "
                "split does nothing")
        for p, why in ((theirs, "another run's"), (nameless, "an owner-less"),
                       (broken, "an unreadable"), (live, "a LIVE")):
            if not p.exists():
                err(f"reap: deleted {why} lock — foreign and ambiguously owned state is "
                    "reported and left alone, and a live lease is not residue at all")
        for key in ("THEIRS", "NAMELESS", "BROKEN"):
            if key not in r.stdout:
                err(f"reap: left {key} alone without reporting it — silence about residue "
                    "is the defect this mechanism exists to close")

        # Naming a lock this run cannot prove it owns must be refused out loud, not obeyed.
        r2 = _run_script(project, "reap", "THEIRS", run_id="resident")
        if r2.returncode == 0:
            err("reap THEIRS: exited 0 on a lock it refused to touch — an operator reads "
                "that as done")
        if not theirs.exists():
            err("reap THEIRS: deleted another run's lock when asked by name")


def check_reap_verifies_teardown_by_re_reading() -> None:
    """Teardown is verified by re-reading state, never by trusting the delete's return.

    `unlink` returns nothing and raises nothing on a filesystem where the entry survives the
    call — a read-only mount, an NFS write that never lands, another process recreating the
    name. So the fixture makes the delete *succeed* and the state not change, which is the
    exact shape no return-value check can see.
    """
    if not shutil.which("git"):
        notes.append("git not found — reap verification check skipped")
        return
    mod = _load_script("agent_sync_teardown")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("reap verification: init failed")
            return
        rid = _rid_of(project, "resident")
        _plant_lock(project, "GHOST", run=rid, ts="2026-01-01T00:00:00Z", ttl=60)
        original = mod.Path.unlink
        try:
            os.chdir(project)
            os.environ["AGENT_SYNC_RUN_ID"] = "resident"
            s = mod.Sync()
            if [e["state"] for e in s.stale()] != ["reapable"]:
                err("reap verification: the fixture's own lock is not reapable, so the "
                    "teardown below proves nothing")
                return
            mod.Path.unlink = lambda self, *a, **k: None       # a delete that changes nothing
            result = s.reap()
            if result["reaped"]:
                err("reap: reported "
                    f"{[e['key'] for e in result['reaped']]} cleared while the lock is still "
                    "on disk — the teardown was verified by the call's return value, which "
                    "is the wish rather than the state")
            if [e["key"] for e in result["remaining"]] != ["GHOST"]:
                err("reap: a lock still present after the delete must come back as "
                    f"remaining; got {result}")
            mod.Path.unlink = original
            r = s.reap()
            if [e["key"] for e in r["reaped"]] != ["GHOST"] or r["remaining"]:
                err(f"reap: a delete that does land must be confirmed by the re-read; got {r}")
        finally:
            mod.Path.unlink = original
            os.environ.pop("AGENT_SYNC_RUN_ID", None)
            os.chdir(cwd)


def check_finish_reports_what_the_run_leaves_behind() -> None:
    """`finish` printed "✓ no lease left held" beside a two-day-expired lock.

    Proof of Done records what remains. It does not grant authority to delete all of it, so
    what this run owns is a problem to fix and the rest is reported under its own heading.
    """
    if not shutil.which("git"):
        notes.append("git not found — finish residue check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("finish residue: init failed")
            return
        clean = _run_script(project, "finish", run_id="resident").stdout
        if "no expired lock left behind" not in clean:
            err("finish: says nothing about residue on a project that has none — the "
                "positive statement is what makes its absence readable")
        rid = _rid_of(project, "resident")
        old = "2026-01-01T00:00:00Z"
        _plant_lock(project, "LEFT-MINE", run=rid, ts=old, ttl=60)
        _plant_lock(project, "LEFT-THEIRS", run="r-somebodyelse", ts=old, ttl=60)
        out = _run_script(project, "finish", run_id="resident").stdout
        if "LEFT-MINE" not in out:
            err("finish: this run's own expired lock is on disk and `finish` never names it "
                "— it printed the same ✓ it prints for a clean tree")
        if "LEFT-THEIRS" not in out:
            err("finish: another run's expired lock is not reported — Proof of Done records "
                "what remains, including what it must not touch")
        if "no expired lock left behind" in out:
            err("finish: claims nothing was left behind with two expired locks on disk")


def check_a_claim_tag_cannot_outlive_every_command() -> None:
    """ssheleg/agent-sync#5. A claim tag with no lease behind it was unreachable.

    Two lines made it so, and each removed one half of the answer: `write_claim`'s
    `if saved is None: continue` turned `release` into a no-op that still printed
    `released <KEY>` and exited 0, and `claim_divergence`'s `if not held: return out`
    gated divergence reporting on holding a lease — so the one shape that needs
    reporting most, a tag nobody holds, could be reported to nobody.

    Both directions are asserted, because the fix is worthless if it clears a tag whose
    lease is live: that is the collision a lease exists to prevent, performed by the
    tool that exists to prevent it.
    """
    if not shutil.which("git"):
        notes.append("git not found — orphan claim tag check skipped")
        return
    head = "| id | What | Status |\n|---|---|---|\n"
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("orphan claim: init failed")
            return
        board = Path(project) / "docs" / "board.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text(head
                         + "| B-77 | orphaned | open (claimed: r-ghost1234) |\n"
                         + "| B-88 | live | open (claimed: r-alive) |\n")
        _write_cfg(project, guardedFiles=["docs/board.md"],
                   claimTags={"docs/board.md": {"mode": "cell", "cell": -1,
                                                "held": "{prev} (claimed: {holder})"}})
        _plant_lock(project, "B-88", run="r-alive", ttl=2700,
                    ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

        # Reported at all — by every command that reports state, not only to a holder.
        for cmd in ("status", "residue", "reconcile"):
            out = (lambda r: r.stdout + r.stderr)(_run_script(project, cmd))
            if "B-77" not in out:
                err(f"{cmd}: a claim tag with no lease behind it is never named — the tag "
                    "outlives the lease and no command reaches it (ssheleg#5)")
            elif "orphan" not in out.lower():
                err(f"{cmd}: names the stale tag without saying what it is")

        # Cleared by `release`, and the report says whose it was.
        r = _run_script(project, "release", "B-77")
        cell = _cell_of(board, "B-77")
        if "claimed:" in cell:
            err(f"release: left the orphaned claim tag in place ({cell!r}) and exited "
                f"{r.returncode} — `release` printed success and changed nothing")
        if "r-ghost1234" not in r.stdout:
            err("release: cleared the orphaned tag without naming the run it named — an "
                "operator cannot tell whose claim was just removed")

        # And the direction that must not move: a live lease is somebody working.
        r2 = _run_script(project, "release", "B-88")
        if r2.returncode == 0:
            err("release B-88: exited 0 on a lease held by another run")
        if _cell_of(board, "B-88") != "open (claimed: r-alive)":
            err(f"release B-88: edited a claim whose lease is live — got "
                f"{_cell_of(board, 'B-88')!r}")


def _cell_of(board: Path, key: str) -> str:
    for line in board.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == key:
            return cells[-1]
    return ""


def check_local_locks_record_their_host() -> None:
    """AS-03. `classify_lock` consumes `host`; only the git mode used to write it.

    So in `local` mode the classifier had one fewer way to refuse, on every lock this
    family had on disk. The `local` lease is machine-local by construction — the FILE is
    not: a checkout on a synced directory is read by two machines, and without `host` a
    lock written on the other one matched on run and repo and became this run's to delete.
    """
    if not shutil.which("git"):
        notes.append("git not found — local host check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("local host: init failed")
            return
        if _run_script(project, "acquire", "K-1").returncode != 0:
            err("local host: acquire failed")
            return
        lock = json.loads((Path(project) / ".agent-sync" / "leases" / "K-1.lock").read_text())
        if not lock.get("host"):
            err("a lock taken in `local` mode records no host, so `classify_lock` cannot "
                "tell a lock written on another machine from one written here (AS-03)")
        elif lock["host"] != platform.node():
            err(f"the lock names host {lock['host']!r}, not this machine")


def check_ledger_names_the_shipped_version() -> None:
    """The newest ledger section must be about the tree it is committed into.

    It was not. `docs/evidence/verification.md` headed its newest section "(in tree,
    unreleased)" and quoted `PASS: agent-sync v1.13.0` while v1.14.0 was tagged, in
    `package.json` and in the CHANGELOG — the ledger is the document read as the record of
    what is true, and its newest section was a version behind the release it described.

    Three mechanical rules, and none of them forbids the honest pre-release wording — a
    section written before the tag says "in tree at vX, release pending", names vX, and
    passes:

    1. a quoted `PASS: agent-sync vX` is a quoted **command output**, so X must be the
       version that command prints today. This is the restated-number defect exactly;
    2. the newest section must NAME the version at HEAD, whatever it says about it. That
       is the half a stale section fails: it named v1.13.0 and nothing else;
    3. a section claiming `shipped in vX` must have a `## vX` heading in the CHANGELOG —
       no ledger may announce a release that did not happen.

    `git describe --tags` is the authority where there is a git directory; the self-test
    copies the tree without one, so `package.json` — tied to every other manifest by
    `check_version_sync` — is the fallback, and the two are compared when both read.
    """
    pkg = json.loads((ROOT / "package.json").read_text())["version"]
    described = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT,
                               capture_output=True, text=True)
    tag = described.stdout.strip().lstrip("v") if described.returncode == 0 else ""
    def _parts(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)[:3])

    if tag and tag != pkg:
        if _parts(pkg) > _parts(tag):
            # AHEAD of the newest tag is a release being PREPARED, and refusing it made
            # the bump commit uncommittable: the tag cannot exist before the commit that
            # bumps to it. Four members of this family hit that in one release pass. The
            # ledger is checked against `package.json` in that state — what is about to
            # ship — and the release workflow is where a tag that never arrives fails.
            notes.append(f"version — package.json says {pkg} and the newest tag is "
                         f"v{tag}: a release in preparation, so the ledger is read "
                         f"against {pkg}")
            tag = ""
        else:
            err(f"`git describe --tags` prints v{tag} while package.json says {pkg} — the "
                "release and the manifests disagree, so nothing below can be checked "
                "against either. package.json is BEHIND the tag, which is a manifest that "
                "was not bumped rather than a release being prepared")
            return
    shipped = tag or pkg

    ledger = ROOT / "docs" / "evidence" / "verification.md"
    if not ledger.exists():
        err("docs/evidence/verification.md is missing — the ledger is the record of what "
            "is verified, and its absence cannot be checked around")
        return
    text = ledger.read_text()

    sections = re.split(r"(?m)^## ", text)[1:]

    # 1. A quoted command output is not prose -- IN THE SECTION THAT DESCRIBES THIS TREE.
    #
    # Scanning the whole ledger made every past release's quote a claim about now, so each
    # release rewrote them to match, and the record stopped being one: measured 2026-08-25,
    # the rows dated 2026-08-19 and 2026-08-20 both quoted `PASS: agent-sync v1.16.0` while
    # git says those trees shipped 1.14.0 and 1.15.0. A guard that can only be satisfied by
    # falsifying a dated record is not protecting the ledger, and this family says so about
    # its own dated rows one repository up: they cite past states on purpose, and are counted
    # rather than gated. Both rows are restored, and this now reads the newest section only.
    for quoted in sorted(set(re.findall(r"PASS: agent-sync v(\d+\.\d+\.\d+)",
                                        sections[0] if sections else ""))):
        if quoted != shipped:
            err(f"verification.md's newest section quotes `PASS: agent-sync v{quoted}` while "
                f"the suite prints v{shipped} — a restated command output, and the one thing "
                "a ledger may never carry")

    if not sections:
        err("verification.md carries no `## ` section, so it states nothing about any "
            "shipped requirement")
        return
    newest = sections[0]
    heading = newest.splitlines()[0]

    # 2. Say which tree this is about.
    if shipped not in newest:
        err(f"verification.md's newest section ({heading!r}) never names v{shipped}, the "
            "version at HEAD — a ledger section that does not say which tree it describes "
            "is read as describing this one")

    # 3. No announced release that did not happen — anywhere in the file. Scoping this to
    #    the newest section would leave every older one free to claim a version that was
    #    never cut, and an older row is read as settled rather than as unchecked.
    changelog = (ROOT / "CHANGELOG.md").read_text()
    for claimed in sorted(set(re.findall(r"shipped in v(\d+\.\d+\.\d+)", text))):
        if not re.search(rf"(?m)^##+\s+\[?v?{re.escape(claimed)}\b", changelog):
            err(f"verification.md says work shipped in v{claimed} and the CHANGELOG has no "
                "such release — the ledger announces a version that does not exist")


class _Resp(io.BytesIO):
    """A urlopen result: a context manager over bytes, which is all `_call` uses."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _notion_env(**over) -> dict[str, str]:
    """Deterministic Notion credentials for a fixture: present, and worthless.

    The values must be non-empty (so `configured()` is true) and obviously fake (so a
    check can never accidentally reach the real API with the developer's own token
    inherited from the shell)."""
    base = {"AGENT_SYNC_NOTION_TOKEN": "not-a-token",
            "AGENT_SYNC_NOTION_PARENT": "0" * 32,
            "AGENT_SYNC_NOTION_COLLECTION": "0" * 32}
    base.update(over)
    return base


def check_notion_retries_only_what_can_succeed() -> None:
    """A credential does not become valid on retry, and a 429 is not a failure.

    Both halves are in `references/adapter-contract.md`'s error table and both are
    easy to get backwards. Retrying a 401 spends the rate limit on an answer that
    cannot change; failing on the first 429 turns a limit shared with every other
    connection in the workspace — spent by somebody else's script — into this run's
    error. Driven with a stubbed transport, so it needs no network and no credentials.
    """
    sys.path.insert(0, str(ROOT / "plugins/agent-sync/skills/agent-sync/scripts"))
    import io
    import urllib.error
    import urllib.request
    try:
        import agent_sync as mod
    except Exception as exc:                    # pragma: no cover
        err(f"notion: cannot import the adapter to test it ({exc})")
        return

    calls = {"n": 0}

    def transport(code: int):
        def _open(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                req.full_url, code, "planted", {"Retry-After": "0"},
                io.BytesIO(b'{"code":"planted","message":"planted"}'))
        return _open

    saved_open, saved_env = urllib.request.urlopen, dict(os.environ)
    # The backoff is real and this check does not measure it — it measures the number of
    # attempts. Left alone it sleeps ~3s, and the self-test runs the whole validator once
    # per planted defect, so three seconds here is three minutes there.
    saved_sleep, mod.time.sleep = mod.time.sleep, lambda _s: None
    os.environ.update(_notion_env())
    try:
        for code, expected, label in ((401, 1, "a rejected token"),
                                      (429, 5, "a rate limit"),
                                      (409, 5, "a write conflict")):
            calls["n"] = 0
            urllib.request.urlopen = transport(code)
            try:
                mod.NotionAdapter()._call("GET", "users/me")
                err(f"notion: {label} was reported as success")
            except mod.Fail:
                pass
            except Exception as exc:            # pragma: no cover
                err(f"notion: {label} escaped as {type(exc).__name__}, not the tool's "
                    "own failure type — the caller gets a traceback for a network answer")
            if calls["n"] != expected:
                err(f"notion: {label} was attempted {calls['n']} times, expected "
                    f"{expected} — " + ("a credential does not become valid on retry"
                                        if expected == 1 else
                                        "a retryable answer must be retried, not raised"))
        # And the half that had never been exercised: a retryable answer followed by
        # a real one. A retry loop that cannot SUCCEED after retrying is a loop that
        # only ever fails slower — and a read timeout is the case that found this, by
        # killing a writer mid-measurement because TimeoutError is an OSError and fell
        # through to the catch-all instead of being retried.
        for planted, label in ((urllib.error.HTTPError("u", 429, "x", {"Retry-After": "0"},
                                                       io.BytesIO(b"{}")), "a rate limit"),
                               (TimeoutError("The read operation timed out"),
                                "a read timeout")):
            state = {"n": 0}

            def _open(req, timeout=None, _p=planted, _s=state):
                _s["n"] += 1
                if _s["n"] == 1:
                    raise _p
                return _Resp(b'{"ok": 1}')

            urllib.request.urlopen = _open
            try:
                mod.NotionAdapter()._call("GET", "users/me")
            except Exception as exc:
                err(f"notion: {label} followed by a good answer still failed "
                    f"({type(exc).__name__}: {exc}) — the retry loop cannot succeed, "
                    "only fail more slowly")
            if state["n"] != 2:
                err(f"notion: {label} was not retried into its answer "
                    f"({state['n']} attempt(s))")
    finally:
        urllib.request.urlopen = saved_open
        mod.time.sleep = saved_sleep
        os.environ.clear()
        os.environ.update(saved_env)


def check_init_notion_writes_its_env_keys() -> None:
    """`init` writes the shape and never the secret.

    The whole security model of this tool is that the token is the operator's: created
    by them, pasted by them. An `init` that writes a value into that line — or that
    omits the line, so they paste it somewhere of their own choosing — breaks it from
    opposite directions.
    """
    if not shutil.which("git"):
        notes.append("git not found — notion init check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        r = _run_script(project, "init", "--backend", "notion")
        if r.returncode != 0:
            err(f"init --backend notion failed: {r.stderr.strip()[:200]}")
            return
        env = (Path(project) / ".env.agent-sync").read_text()
        for key in ("AGENT_SYNC_NOTION_TOKEN", "AGENT_SYNC_NOTION_PARENT",
                    "AGENT_SYNC_NOTION_COLLECTION"):
            if f"{key}=" not in env:
                err(f"init --backend notion: {key} is missing from the env file, so the "
                    "operator has nowhere named to paste it")
        if re.search(r"AGENT_SYNC_NOTION_TOKEN=\S", env):
            err("init --backend notion wrote a VALUE into the token line — the token is "
                "the operator's to create and paste, and nothing else may place one")
        if "bootstrap" not in r.stdout:
            err("init --backend notion did not name `bootstrap` as the next step, so the "
                "container is never created and every later command reports it missing")


def check_bootstrap_follows_the_configured_backend() -> None:
    """`bootstrap` built an Outline collection whatever the project was configured for.

    It read `OutlineAdapter()` directly. On a `notion` project it demanded Outline
    credentials; on `fs` it demanded them too, for a backend with no container at all.
    A command that ignores the configuration is a command that answers a question
    nobody asked.
    """
    if not shutil.which("git"):
        notes.append("git not found — bootstrap dispatch check skipped")
        return
    # AGENT_SYNC_BACKEND is in here on purpose: it OVERRIDES the config, so a check
    # that does not state it is a check whose answer depends on what ran before it.
    blank = {k: "" for k in ("AGENT_SYNC_BACKEND",
                             "AGENT_SYNC_OUTLINE_URL", "AGENT_SYNC_OUTLINE_TOKEN",
                             "AGENT_SYNC_NOTION_TOKEN", "AGENT_SYNC_NOTION_PARENT",
                             "AGENT_SYNC_NOTION_COLLECTION")}
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        _run_script(project, "init", "--backend", "fs")
        out = _run_script(project, "bootstrap", env=blank)
        said = out.stdout + out.stderr
        if out.returncode == 0 or "no container" not in said:
            err("bootstrap on an `fs` project did not say that this backend has no "
                f"container to create (said: {said.strip()[:160]!r})")
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        started = _run_script(project, "init", "--backend", "notion")
        if started.returncode != 0:
            err("bootstrap dispatch: init --backend notion failed, so the rest of this "
                f"check measured nothing: {(started.stderr or started.stdout).strip()[:200]}")
            return
        out = _run_script(project, "bootstrap", env=blank)
        said = out.stdout + out.stderr
        if "AGENT_SYNC_NOTION_TOKEN" not in said:
            err("bootstrap on a `notion` project did not name the Notion credential it "
                f"needs (said: {said.strip()[:160]!r})")
        if "OUTLINE" in said:
            err("bootstrap on a `notion` project asked for Outline credentials — it is "
                "reading the adapter class rather than the configuration")


def check_check_accepts_every_shipped_backend() -> None:
    """One list of backends, or the tool ships one it refuses to validate.

    `init`'s argument parser, `check`'s known-adapter test and `bootstrap`'s dispatch
    each used to carry their own copy of the pair. A third backend added to one copy
    and missed in another is a backend that half exists — accepted at `init` and
    rejected by `check` an hour later, in a project already configured with it.
    """
    if not shutil.which("git"):
        notes.append("git not found — backend list check skipped")
        return
    sys.path.insert(0, str(ROOT / "plugins/agent-sync/skills/agent-sync/scripts"))
    try:
        from agent_sync import BACKENDS
    except Exception as exc:                    # pragma: no cover
        err(f"backends: cannot import the registry to test it ({exc})")
        return
    blank = {k: "" for k in ("AGENT_SYNC_BACKEND",
                             "AGENT_SYNC_OUTLINE_URL", "AGENT_SYNC_OUTLINE_TOKEN",
                             "AGENT_SYNC_NOTION_TOKEN", "AGENT_SYNC_NOTION_COLLECTION")}
    for backend in BACKENDS:
        with tempfile.TemporaryDirectory() as project:
            _git_project(project)
            argv = ["init", "--backend", backend]
            if backend == "outline":
                argv += ["--url", "https://wiki.example.com"]
            r = _run_script(project, *argv, env=blank)
            if r.returncode != 0:
                err(f"init refuses backend '{backend}', which the registry ships: "
                    f"{r.stderr.strip()[:160]}")
                continue
            out = _run_script(project, "check", env=blank)
            if "is not a known adapter" in out.stdout:
                err(f"check calls '{backend}' unknown while `init` writes it — the two "
                    "are reading different lists")


def check_baseline_is_not_poisoned_by_the_next_free_line() -> None:
    """The baseline counted the id nobody had written yet. B-34's other half.

    B-34 fixed the crash when the register's pattern lives under `pattern` rather than
    `nextFreeIdPattern`, by reading both through `id_pattern()`. `_allocated_ids` kept
    reading the raw key — so with a modern config the *next free* line was counted as
    an allocated id, and `--set-baseline` stamped one higher than reality. Every id at
    the true top then reads as pre-baseline and is never asked for an as-built record.
    Reproduced in `fabric`, 2026-08-25.
    """
    if not shutil.which("git"):
        notes.append("git not found — baseline check skipped")
        return
    with tempfile.TemporaryDirectory() as project:
        _git_project(project)
        (Path(project) / "DECISIONS.md").write_text(
            "# Decisions\n\nDEC-0001\nDEC-0002\nDEC-0003\n\n"
            "**Next free ID:** `DEC-0004`\n")
        if _run_script(project, "init", "--backend", "fs").returncode != 0:
            err("baseline: init failed")
            return
        _write_cfg(project, idRegisters={"DEC": {
            "file": "DECISIONS.md",
            "pattern": r"\*\*Next free ID:\*\* `DEC-(\d{4})`"}})
        out = _run_script(project, "reconcile", "--set-baseline")
        if "DEC-0003" not in out.stdout:
            err("reconcile --set-baseline did not stamp DEC-0003, the highest id actually "
                f"written — it counted the `Next free ID` line as allocated (said: "
                f"{out.stdout.strip()[:160]!r})")


def check_no_adapter_claims_a_lease_it_cannot_decide() -> None:
    """`exclusiveLease` is the one flag that can make the tool lie about safety.

    Trap 1 of the skill is that a knowledge base never decides a lease — none of the
    document stores has compare-and-swap. An adapter declaring otherwise makes every
    surface report exclusion the store cannot deliver, and the other agent stops
    checking. The second half is the degradation path: with `atomicAppend` false the
    coordinator must refuse lease authority, and that must hold for every adapter
    rather than for the one whose reference file happens to mention it.
    """
    sys.path.insert(0, str(ROOT / "plugins/agent-sync/skills/agent-sync/scripts"))
    try:
        import agent_sync as mod
    except Exception as exc:                    # pragma: no cover
        err(f"adapters: cannot import them to test them ({exc})")
        return
    for name, cls in sorted(mod.CLOUD_ADAPTERS.items()):
        if cls.capabilities.get("exclusiveLease"):
            err(f"adapter '{name}' declares exclusiveLease — no document store has "
                "compare-and-swap, and a lease that is not exclusive is worse than none")
        saved = cls.capabilities
        try:
            cls.capabilities = {**saved, "atomicAppend": False}
            if cls().is_lease_authority:
                err(f"adapter '{name}': with atomicAppend false it still claims lease "
                    "authority — the degradation path the contract requires is absent")
        finally:
            cls.capabilities = saved


def _guarded(fn):
    """Run one check without letting it change the world the next one runs in.

    `Sync()` calls `load_env_file`, which writes the project's credentials into
    `os.environ` — correct for the CLI, and a leak in-process: a check that built a
    temporary `fs` project left `AGENT_SYNC_BACKEND=fs` behind, and that variable
    OVERRIDES the configured backend. The next check to depend on it read the previous
    check's project. Two of them also `os.chdir` and never return.

    Found 2026-08-25 while adding the third backend: the new dispatch check passed
    alone and failed in the suite, which is the signature of this class and not of the
    code under test.
    """
    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    try:
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        try:
            os.chdir(saved_cwd)
        except OSError:                      # the cwd itself was a temporary directory
            pass


def main() -> int:
    _guarded(check_manifests)
    _guarded(check_no_stray_skills)
    ok, version = _guarded(check_version_sync)
    _guarded(check_example_against_schema)
    _guarded(check_public_floor)
    _guarded(check_npm_excludes)
    _guarded(check_no_host_identity)
    _guarded(check_no_credentials)
    _guarded(check_scripts_run)
    _guarded(check_hooks_manifest)
    _guarded(check_hooks_noop_without_config)
    _guarded(check_lease_report_agrees)
    _guarded(check_repo_slug_consistent)
    _guarded(check_branch_claim_discipline)
    _guarded(check_merge_refuses_conflicts)
    _guarded(check_lease_held_is_visible)
    _guarded(check_release_refuses_other_runs)
    _guarded(check_reserve_respects_the_register)
    _guarded(check_reserve_is_race_free)
    _guarded(check_renew_extends_the_lease)
    _guarded(check_config_round_trip)
    _guarded(check_no_success_on_failed_publish)
    _guarded(check_guard_denial_names_only_what_it_knows)
    _guarded(check_doctrine_is_current)
    _guarded(check_steal_is_atomic)
    _guarded(check_merge_refuses_stale_target)
    _guarded(check_guard_and_check_agree_on_globs)
    _guarded(check_unparseable_log_fails_loudly)
    _guarded(check_stage_binding_agrees)
    _guarded(check_status_reports_the_setup_verdict)
    _guarded(check_env_discovery_is_bounded)
    _guarded(check_watermark_survives_a_late_entry)
    _guarded(check_no_orphan_logs)
    _guarded(check_no_dead_declarations)
    _guarded(check_claim_round_trip_is_byte_exact)
    _guarded(check_claim_lands_on_the_row_whose_id_cell_matches)
    _guarded(check_release_notes_are_extractable)
    _guarded(check_every_advertised_verb_exists)
    _guarded(check_generated_docs_carry_current_doctrine)
    _guarded(check_registers_need_a_backend_that_can_reserve)
    _guarded(check_skill_gives_a_resolvable_script_path)
    _guarded(check_commands_work_without_the_family_installed)
    _guarded(check_two_agents_cannot_share_one_task)
    _guarded(check_guard_covers_every_write_shape)
    _guarded(check_merge_refuses_without_an_identity)
    _guarded(check_merge_releases_only_its_key)

    _guarded(check_routed_triggers_still_advertised)
    _guarded(check_the_tarball_carries_no_bytecode)

    _guarded(check_status_reports_expired_locks_as_residue)
    _guarded(check_residue_ownership_must_be_provable)
    _guarded(check_reap_verifies_teardown_by_re_reading)
    _guarded(check_finish_reports_what_the_run_leaves_behind)

    _guarded(check_a_claim_tag_cannot_outlive_every_command)
    _guarded(check_local_locks_record_their_host)
    _guarded(check_ledger_names_the_shipped_version)

    _guarded(check_no_adapter_claims_a_lease_it_cannot_decide)
    _guarded(check_notion_retries_only_what_can_succeed)
    _guarded(check_init_notion_writes_its_env_keys)
    _guarded(check_bootstrap_follows_the_configured_backend)
    _guarded(check_check_accepts_every_shipped_backend)
    _guarded(check_baseline_is_not_poisoned_by_the_next_free_line)

    for n in notes:
        print(f"note: {n}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"\n{len(errors)} problem(s)")
        return 1
    print(f"PASS: agent-sync v{version} — all checks green")
    return 0


def self_test() -> int:
    """A validator that cannot fail is decoration. Corrupt a copy, expect failure."""
    global ROOT, errors, notes
    cases = {
        # B-72: the tarball carried 245.8 kB of someone else's bytecode because
        # `files` overrides both ignore files. Dropping the negations is the exact
        # state that shipped, so the plant is a deletion rather than an injection.
        "bytecode in the tarball": ("package.json",
                                    lambda t: t.replace('    "!plugins/**/__pycache__",\n', "")
                                               .replace('    "!plugins/**/*.pyc",\n', "")),
        "description over cap": ("plugins/agent-sync/skills/agent-sync/SKILL.md",
                                 lambda t: t.replace("description: \"Use when",
                                                     "description: \"" + "x" * 1100 + " Use when")),
        # Version-agnostic on purpose: a fixture pinned to a literal version stops
        # breaking anything the first time the real version moves, and then the
        # self-test reports "PASS" for a check that no longer runs.
        "version drift": ("package.json",
                          lambda t: re.sub(r'"version":\s*"\d+\.\d+\.\d+"',
                                           '"version": "9.9.9"', t, count=1)),
        # Built from parts on purpose: a literal instance address anywhere in a
        # published file is exactly what this rule forbids, including here.
        "leaked host": ("agent-sync.example.json",
                        lambda t: t.replace('"backend": "outline"',
                                            '"backend": "outline", "leak": "%s://%s.%s"'
                                            % ("https", "wiki", "internal-corp.example"))),
        "token in argv": ("plugins/agent-sync/skills/agent-sync/references/backend-fs.md",
                          lambda t: t + '\n```bash\ncurl -H "Authorization: Bearer $T" x\n```\n'),
        # The 1.2.3 defect itself: the manifests move, the tool keeps announcing the
        # version before them.
        "script version drift": ("plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
                                 lambda t: re.sub(r'^VERSION\s*=\s*"\d+\.\d+\.\d+"',
                                                  'VERSION = "9.9.9"', t, count=1, flags=re.M)),
        # And the other half: one surface quietly returns to describing the record
        # backend as the thing that decides a lease.
        "lease wording drift": ("plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
                                lambda t: t.replace(
                                    '    print(f"  lease          : {s.lease_mode} — {headline}")',
                                    '    print(f"  lease authority: '
                                    '{\'yes\' if ad.is_lease_authority else \'no\'}")')),
        # A rename that reached package.json and stopped there: the installer would
        # still clone the old owner, and only a redirect would hide it. The slug is
        # assembled from parts for the same reason the leaked host above is — written
        # whole, this fixture is itself a foreign slug in a published file, and the
        # check would fail on the validator that carries it.
        # --- the third backend, and the two defects fixed beside it (1.18.0) ---
        # The read timeout sent straight to a permanent failure, which is what killed a
        # writer mid-measurement on 2026-08-25 and made an atomicity result unreadable.
        "notion gives up on a read timeout": (
            SCRIPT_PATH,
            lambda t: t.replace(
                '''                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Fail(f"notion {method} {path}: the API did not answer within "''',
                '''                if False:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Fail(f"notion {method} {path}: the API did not answer within "''')),
        # The flag that makes the tool lie about safety, set on the newest adapter.
        "an adapter claims an exclusive lease": (
            SCRIPT_PATH,
            lambda t: t.replace(
                '''    capabilities = {"atomicAppend": True, "totalOrderRead": True, "search": True,
                    "exclusiveLease": False}

    def __init__(self) -> None:
        self.token = os.environ.get("AGENT_SYNC_NOTION_TOKEN")''',
                '''    capabilities = {"atomicAppend": True, "totalOrderRead": True, "search": True,
                    "exclusiveLease": True}

    def __init__(self) -> None:
        self.token = os.environ.get("AGENT_SYNC_NOTION_TOKEN")''')),
        # A rejected token sent back into the retry loop: five attempts spent on an
        # answer that cannot change, and the rate limit spent with it.
        "notion retries a rejected token": (
            SCRIPT_PATH,
            # Two edits, because one is not the defect: dropping 401 from the auth
            # branch alone still fails on the first attempt. The shipped defect is 401
            # reaching the RETRY set — five attempts, and the shared workspace limit
            # spent, on an answer that cannot change.
            lambda t: t.replace(
                '''if exc.code in (401, 403):
                    raise Fail(
                        f"notion ''',
                '''if exc.code in (403,):
                    raise Fail(
                        f"notion ''')
                       .replace("if exc.code in (409, 429, 500, 502, 503, 504, 529) and attempt < 4:",
                                "if exc.code in (401, 409, 429, 500, 502, 503, 504, 529) and attempt < 4:")),
        # The backend removed from the one registry: `init` refuses what `check` would
        # have accepted, which is the half-existing backend the registry exists to stop.
        "notion missing from the registry": (
            SCRIPT_PATH,
            lambda t: t.replace('    "notion": NotionAdapter,\n', "")),
        # `check` keeping its own copy of the backend list — the drift the registry
        # replaced, planted back.
        "check keeps its own backend list": (
            SCRIPT_PATH,
            lambda t: t.replace('if cfg.get("backend") not in BACKENDS:',
                                'if cfg.get("backend") not in ("outline", "fs"):')),
        # B-34's other half: the raw key again, so the next-free line counts as an
        # allocated id and the baseline lands one above reality.
        "baseline counts the next free id": (
            SCRIPT_PATH,
            lambda t: t.replace("        pattern = id_pattern(spec)",
                                '        pattern = spec.get("nextFreeIdPattern")')),
        # `init` placing a token value. The whole security model is that it cannot.
        "init writes the notion token itself": (
            SCRIPT_PATH,
            lambda t: t.replace('"AGENT_SYNC_NOTION_TOKEN=\\n"',
                                '"AGENT_SYNC_NOTION_TOKEN=ntn_planted_value\\n"')),
        "half-finished rename": ("bin/agent-sync.js",
                                 lambda t: re.sub(r"const REPO = '[^']+'",
                                                  "const REPO = '" + "previous-owner"
                                                  + "/agent-" + "sync'", t)),
        # The claim gate removed: acquire writes the roadmap from whatever branch it is on.
        "claim written from a branch": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("if holder is not None and not self.on_integration_branch:",
                                "if False:")),
        # The conflict preflight neutered: merge would start, hit the conflict and abort,
        # leaving the operator somewhere they did not ask to be.
        "merge without a conflict check": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("    conflicts = merge_conflicts(upstream, branch)",
                                "    conflicts = []")),
        # --- the 1.5.3 defects, each planted back exactly as it shipped ---
        # `reserve` replaying its own shard, and the unconditional re-base that made
        # merging alone insufficient. Both are needed: either one on its own still
        # separates three runs, which is why the second was easy to miss.
        "reserve reads only its own shard": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('        events, _ = self.events("reservations")\n'
                                '        base, _free, _assign',
                                '        events, _ = parse_log(self.adapter.log_read(oid))\n'
                                '        base, _free, _assign')
                       .replace("            if base is not None and value <= base + served:\n"
                                "                continue\n", "")),
        # `renew` back to logging a renewal it never performed.
        "renew moves no timestamp": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("        renewed = [k for k in keys if self._refresh_lease(k)]",
                                "        renewed = list(keys)")),
        # One key dropped from the legal list is the whole `mergeLog` defect.
        "config key list drifts from the schema": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('"settleSeconds", "integrationBranch", "mergeLog",',
                                '"settleSeconds", "integrationBranch",')),
        # Success printed over a publish that failed.
        "record reports success it did not achieve": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '    if not Sync().record(" ".join(args.text), decision=args.decision or "",\n'
                '                         files=args.files or ""):\n        return 1\n',
                '    Sync().record(" ".join(args.text), decision=args.decision or "",\n'
                '                  files=args.files or "")\n')),
        # A denial that names a run without naming what it holds.
        "guard names a holder without a key": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("holds {other[1]} — a different task, not this file.",
                                "holds a lease right now.")),
        # The refuted doctrine back on the listing everyone reads first. Assembled from
        # parts for the same reason the leaked host is: written whole, this fixture would
        # be the very phrase the check forbids, sitting in a published file.
        "refuted doctrine on the listing": (
            ".claude-plugin/marketplace.json",
            lambda t: t.replace("Coordination layer for multi-agent repositories.",
                                "Coordination layer for multi-agent repositories, so "
                                + "no backend needs " + "compare-and-swap.")),
        # --- the 1.6.0 defects ---
        # The steal section made per-process, which is the same as not having one.
        "steal section is not exclusive": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('        guard = lock.with_name(lock.name + ".steal")',
                                '        guard = lock.with_name(lock.name + ".steal."'
                                ' + str(os.getpid()))')),
        # The fast-forward removed: merge measures one base and merges into another.
        "merge into a stale integration branch": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('        if not local_exists or behind_local not in ("", "0"):',
                                "        if False:")),
        # Back to right-anchored matching, which guards files the config never named.
        "guard matches paths from the right": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("if not any(matches_glob(rel, p) for p in patterns):",
                                "if not any(Path(rel).match(p) for p in patterns):")),
        # The threshold declared and not read — exactly how it shipped for five versions.
        "unparseable logs are replayed anyway": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("        if total and bad / total > MAX_UNPARSEABLE:",
                                "        if False:")),
        # The stage numbers back to prose in three files.
        "stage binding loses its source": (
            "plugins/agent-sync/skills/agent-sync/references/pipeline-binding.md",
            lambda t: re.sub(r"<!-- agent-sync:stages[^>]*-->\n", "", t)),
        # --- the 1.7.0 defects ---
        "status hides the setup verdict": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("    if setup_problems:\n        print(f\"\\n✗ `check` reports",
                                "    if False:\n        print(f\"\\n✗ `check` reports")),
        "no way to name the credentials file": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('    explicit = os.environ.get("AGENT_SYNC_ENV")',
                                "    explicit = None")),
        "watermark is positional again": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "        fresh = [e for e in signals\n"
                '                 if self._fingerprint(e) not in seen and e.get("ts", "") >= floor]',
                "        fresh = signals[len(seen):] if len(signals) > len(seen) else []")),
        "a log nothing writes": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('    "signals": "50 Signals",\n',
                                '    "signals": "50 Signals",\n    "blockers": "60 Blockers",\n')),
        "posix-only spelling returns": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("platform.node()", "os.uname().nodename")),
        "claim round-trip rebuilds the row": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '            lines[i] = row_prefix + "|" + "|".join(cells) + row_suffix',
                '            lines[i] = "|" + "|".join(cells) + "|\\n"')),
        # The defect that kept three tagged releases off npm: the extraction matched
        # `## 1.5.2` while the CHANGELOG writes `## v1.5.2`.
        #
        # The source string tracks the workflow's current pattern and must be
        # updated with it. When the family canonicalised on one accepting shape
        # (B-11), this replace stopped matching, the defect stopped being planted,
        # and the self-test reported `undetected` — a guard that had been proving
        # nothing would have looked like a guard that passed.
        "release notes cannot be extracted": (
            ".github/workflows/release.yml",
            lambda t: t.replace('$0 ~ "^## \\\\[?v?" v "\\\\]?([^0-9]|$)"',
                                '$0 ~ "^## " v "([^0-9]|$)"')),
        # --- the 1.7.1 defects: how the skill reads to the agent using it ---
        "the slash command offers a verb the CLI lacks": (
            "plugins/agent-sync/commands/agent-sync.md",
            lambda t: t.replace("acquire <KEY>", "claim <KEY>")),
        "generated project docs lag the doctrine": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '            "merges      → what landed while you were away",\n', "")
             .replace('            "merge --key → land the branch: conflicts checked first, '
                      'the merge recorded,",\n',
                      '            "release ID  → on every path, including failure",\n')),
        "check blesses a register nobody can reserve": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace("        if not probe.is_lease_authority:",
                                "        if False:")),
        # Anchored on the two VALUES the check looks for, not on the sentence around them.
        # It used to quote three lines of prose verbatim and stopped planting the first time
        # the paragraph was reflowed — a plant that silently no-ops still reports `detected`,
        # for a check that never ran. Caught 2026-08-20 by the self-test, not by reading.
        "the script path is prose only": (
            "plugins/agent-sync/skills/agent-sync/SKILL.md",
            lambda t: re.sub(r"\$\{CLAUDE_PLUGIN_ROOT\}\S*", "the plugin directory",
                             t).replace("~/.agents/skills/agent-sync", "the skill hub")),
        # --- the scenarios that were only ever driven by hand ---
        # A live holder read as expired. BOTH guards have to go: `acquire` checks the
        # expiry, and `_steal_expired` re-checks it inside the critical section, so
        # breaking either one alone still refuses the steal. That the single-point
        # mutation was MISSED is the reassuring half of this fixture — the exclusion has
        # two independent layers — and the reason the fixture plants both.
        "a live lease can be taken by a second run": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '            if time.time() <= parse_iso(held.get("ts", "")) + int(held.get("ttl", self.ttl)):\n'
                '                return False, held.get("run")',
                "            if False:\n"
                '                return False, held.get("run")')
             .replace(
                '            if held and time.time() <= parse_iso(held.get("ts", "")) + int(\n'
                '                    held.get("ttl", self.ttl)):\n'
                "                return False",
                "            if False:\n"
                "                return False")),
        # The setup verdict back behind the machine gate — invisible on every runner.
        "the project verdict hides behind the machine gate": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "    try:\n        _ok, _warn, setup_problems = check_setup(root)",
                "    if not pipeline_installed():\n        return 1\n"
                "    try:\n        _ok, _warn, setup_problems = check_setup(root)")),
        # The commit branch of the guard stops recognising a commit — the shape of the
        # defect that let a full day of commits reach guarded registers.
        "the guard stops seeing commits": (
            "plugins/agent-sync/hooks/guard.sh",
            lambda t: t.replace('if k < len(toks) and toks[k] == "commit":',
                                'if k < len(toks) and toks[k] == "kommit":')),
        # ASY-05 planted back exactly as it shipped: the comment kept claiming `|` while
        # the chain consumed only `||`, so `echo msg | git commit -F -` was one segment
        # and never guarded. Anchored on the replace chain, the value the check drives.
        "a piped commit slips past the guard": (
            "plugins/agent-sync/hooks/guard.sh",
            lambda t: t.replace(
                'cmd.replace("&&", "\\n").replace("||", "\\n").replace("|&", "\\n")'
                '.replace(";", "\\n").replace("|", "\\n")',
                'cmd.replace("&&", "\\n").replace(";", "\\n").replace("||", "\\n")')),
        # merge back to releasing everything the run holds.
        "merge releases leases it did not land": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "        to_release = [args.key] if args.key in held else []",
                "        to_release = list(held)")),
        "merge starts without checking for an identity": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda s: s.replace(
                '    ident = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"],',
                '    ident = subprocess.run(["git", "var", "GIT_AUTHOR_DATE"],')),
        # --- AS-01: residue. Expiry ends a lease and leaves the file; these four are
        # the ways a report of what remains goes quiet or goes too far.
        "expired locks are not enumerated": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('    if not stale:\n        print("  expired locks  : none")',
                                '    if True:\n        print("  expired locks  : none")')),
        "a foreign expired lock is called this run's": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('    if out["run"] != rid:\n        out["state"] = FOREIGN',
                                '    if False:\n        out["state"] = FOREIGN')),
        "unprovable ownership defaults to reapable": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('out: dict[str, Any] = {"key": key, "state": AMBIGUOUS,',
                                'out: dict[str, Any] = {"key": key, "state": REAPABLE,')),
        "finish says nothing about what the run leaves behind": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '    stale = s.stale()\n'
                '    reapable = [e for e in stale if e["state"] == REAPABLE]\n'
                '    left_alone = [e for e in stale if e["state"] != REAPABLE]\n'
                '    if not stale:\n'
                '        ok.append(',
                "    stale = []\n"
                "    reapable = []\n"
                "    left_alone = []\n"
                "    if not stale:\n"
                "        ok.append(")),
        "teardown trusts the delete instead of re-reading": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace('        after = {e["key"]: e for e in self.residue()}',
                                "        after = {}")),
        # --- ssheleg#5: a claim tag that outlived its lease. Each plant removes one of
        # the paths that now reach it; the shipped state had all three missing at once.
        "an orphaned claim tag is unreachable": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "                if saved is None:\n"
                '                    # NOT "nothing to undo"',
                "                if saved is None:\n"
                "                    continue\n"
                '                    # NOT "nothing to undo"')),
        "orphan claim tags are reported to nobody": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "        out: list[dict[str, Any]] = []\n"
                "        for e in self.claim_tags_on_disk():",
                "        out: list[dict[str, Any]] = []\n"
                "        return out\n"
                "        for e in self.claim_tags_on_disk():")),
        "a claim whose lease is live is edited anyway": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                "        holder = self._lease_holder(key)\n"
                "        if holder is not None and holder != self.rid:",
                "        holder = self._lease_holder(key)\n"
                "        if False:")),
        # AS-03: the local payload back to carrying no host, so the classifier loses the
        # one field that separates two machines in that mode.
        "a local lock records no host": (
            "plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py",
            lambda t: t.replace(
                '        payload = json.dumps({"run": self.rid, "ts": now_iso(), "ttl": self.ttl,\n'
                '                              "repo": repo_name(), "host": platform.node()})\n\n'
                "        if lock.exists():",
                '        payload = json.dumps({"run": self.rid, "ts": now_iso(), "ttl": self.ttl,\n'
                '                              "repo": repo_name()})\n\n'
                "        if lock.exists():")),
        # The ledger back to naming a version that is not the one that shipped — the exact
        # state found at 1.14.0, where the newest section cited v1.13.0 under a v1.14.0 tag.
        # Planted in the NEWEST section, because that is the only place the check reads
        # since it stopped demanding that every dated row be rewritten on each release.
        # Anchored on `## ` rather than on a version, so a renamed heading cannot disarm it.
        "the ledger names a version that did not ship": (
            "docs/evidence/verification.md",
            lambda t: re.sub(r"(?s)(\n## .*?)PASS: agent-sync v",
                             r"\1PASS: agent-sync v0.0.0 not-v", t, count=1)),
        "the newest ledger section names no version": (
            "docs/evidence/verification.md",
            lambda t: t.replace("\n## ", "\n## A section that names no version\n\n"
                                          "nothing verified here.\n\n## ", 1)),
        "the ledger announces a release that did not happen": (
            "docs/evidence/verification.md",
            lambda t: re.sub(r"shipped in v\d+\.\d+\.\d+", "shipped in v99.0.0", t,
                             count=1)),
        # The body over its token budget, measured with the family auditor's divisor. A
        # plant on the divisor itself cannot be caught: loosening it makes the run pass.
        "skill body over the token budget": (
            "plugins/agent-sync/skills/agent-sync/SKILL.md",
            lambda t: t + ("\n" + "padding that costs tokens and buys nothing. " * 60)),
        "stray SKILL.md": (None, None),
    }
    # Each case runs as its own PROCESS, and they run concurrently.
    #
    # It used to be a loop that reassigned the module globals `ROOT`, `errors` and `notes`
    # and called `main()` in-process. That is a full validator run per case, one after
    # another: at 32 fixtures it took six minutes and had already blown a ten-minute
    # command budget once — and a suite people stop running is a suite that does not
    # exist. A subprocess per case also removes the global-state reset, which was the
    # reason parallelism was impossible.
    original_root = ROOT
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))

    def run_case(item: tuple[str, tuple]) -> tuple[str, int]:
        label, (target, mutate) = item
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(original_root, work,
                            ignore=shutil.ignore_patterns(".git", "node_modules",
                                                          "__pycache__", ".agent-sync"))
            if target is None:
                (work / "templates").mkdir(exist_ok=True)
                (work / "templates" / "SKILL.md").write_text("---\nname: x\n---\n")
            else:
                p = work / target
                p.write_text(mutate(p.read_text()))
            r = subprocess.run([sys.executable, str(work / "test" / "validate.py")],
                               capture_output=True, text=True, timeout=900)
            return label, r.returncode

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        results = dict(pool.map(run_case, cases.items()))

    # Reported in declaration order, whatever order they finished in: a suite whose
    # output reshuffles between runs cannot be diffed.
    failures = [label for label in cases if results.get(label, 0) == 0]
    for label in cases:
        print(f"  self-test [{label}]: "
              f"{'detected' if results.get(label, 0) else 'MISSED'}")
    if failures:
        print(f"\nSELF-TEST FAILED — undetected: {failures}")
        return 1
    print(f"\nSELF-TEST PASS: every injected defect was caught "
          f"({len(cases)} fixtures, {workers} at a time)")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
