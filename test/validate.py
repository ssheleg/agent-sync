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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Hosts a published file may legitimately name. Anything else is treated as an
# instance address that leaked out of someone's environment.
ALLOWED_HOSTS = {
    "github.com", "raw.githubusercontent.com", "www.github.com",
    "code.claude.com", "docs.claude.com", "agentskills.io", "www.agentskills.io",
    "www.getoutline.com", "getoutline.com", "app.getoutline.com",
    "json-schema.org", "npmjs.com", "www.npmjs.com", "img.shields.io",
    "x.com", "sshlg.me", "t.me",
    "localhost", "127.0.0.1", "example.com", "wiki.example.com",
}

PUBLISHED = ["plugins", "bin", "test", "agent-sync.example.json", "agent-sync.schema.json"]

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
    if len(body) // 4 > 5000:
        err(f"{rel(md)}: body is ~{len(body)//4} tokens, budget is < 5000")

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


def main() -> int:
    check_manifests()
    check_no_stray_skills()
    ok, version = check_version_sync()
    check_example_against_schema()
    check_public_floor()
    check_npm_excludes()
    check_no_host_identity()
    check_no_credentials()
    check_scripts_run()
    check_hooks_manifest()
    check_hooks_noop_without_config()
    check_lease_report_agrees()
    check_repo_slug_consistent()
    check_branch_claim_discipline()
    check_merge_refuses_conflicts()
    check_lease_held_is_visible()
    check_release_refuses_other_runs()
    check_reserve_respects_the_register()

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
        "stray SKILL.md": (None, None),
    }
    original_root = ROOT
    failures = []
    for label, (target, mutate) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(original_root, work,
                            ignore=shutil.ignore_patterns(".git", "node_modules"))
            if target is None:
                (work / "templates").mkdir(exist_ok=True)
                (work / "templates" / "SKILL.md").write_text("---\nname: x\n---\n")
            else:
                p = work / target
                p.write_text(mutate(p.read_text()))
            ROOT = work
            errors, notes = [], []
            rc = main()
            if rc == 0:
                failures.append(label)
            print(f"  self-test [{label}]: {'detected' if rc else 'MISSED'}")
    ROOT = original_root
    if failures:
        print(f"\nSELF-TEST FAILED — undetected: {failures}")
        return 1
    print("\nSELF-TEST PASS: every injected defect was caught")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
