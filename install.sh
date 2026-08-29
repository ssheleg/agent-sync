#!/bin/sh
# POSIX fallback installer. Prefer `npx agent-sync install` — it is cross-platform.
# This script is POSIX-only: on Windows use npx, the Claude Code plugin, or the
# skills CLI, never this file.
set -eu

REPO="ssheleg/agent-sync"
NAME="agent-sync"
SHADOW="$HOME/.claude/skills/$NAME"

FORCE=0
if [ "${1:-}" = "--force" ]; then
  FORCE=1
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [--force]" >&2
  exit 2
fi

echo "Installing $NAME"

if command -v claude >/dev/null 2>&1; then
  echo "  Claude Code — as a plugin"
  claude plugin marketplace add "$REPO" || true
  # The full <name>@<name> id is required.
  claude plugin install "$NAME@$NAME" || true
else
  echo "  claude CLI not found; skipping the plugin channel"
fi

if command -v npx >/dev/null 2>&1; then
  echo "  Other agents — via the skills CLI"
  npx --yes skills add "$REPO" --global --yes || true
else
  echo "  npx not found; skipping the skills-CLI channel"
fi

# The skills CLI recreates $SHADOW on its own — often as a symlink — even when
# claude-code was never targeted. What that copy IS depends on this home:
# beside an installed plugin it is a shadow that serves a stale skill forever;
# on a home with no plugin it is Claude Code's only channel, and the
# unconditional prune this script carried until v1.18.5 destroyed it and exited
# 0 — the fail-open class the family canon names (make-skill distribution.md
# §3). installed_plugins.json is the record of what is installed; the
# marketplaces/ dir is kept only as the fallback signal (it under-reports:
# directory-sourced marketplaces have no dir there, and plugin names differ
# from marketplace names). A missing or unparsable JSON reads as "no plugin".
INSTALLED_JSON="$HOME/.claude/plugins/installed_plugins.json"
MARKETPLACE="$HOME/.claude/plugins/marketplaces/$NAME"
SPEC=""
if [ -f "$INSTALLED_JSON" ]; then
  SPEC="$(sed -n "s/.*\"\\($NAME@[^\"]*\\)\".*/\\1/p" "$INSTALLED_JSON" 2>/dev/null | head -n 1)" || true
fi
if [ -e "$SHADOW" ] || [ -L "$SHADOW" ]; then
  if [ -n "$SPEC" ] || [ -e "$MARKETPLACE" ]; then
    if [ "$FORCE" -eq 1 ]; then
      echo "  kept $SHADOW beside the installed plugin (--force) — two channels"
      echo "  on one agent, and the stale plain copy is the one Claude Code reads"
    else
      rm -rf "$SHADOW"
      echo "  pruned $SHADOW — the ${SPEC:-$NAME@$NAME} plugin channel owns Claude Code;"
      echo "  a plain copy there would shadow the plugin and serve a frozen version"
      echo "  forever. The plugin channel owns updates:"
      echo "    claude plugin marketplace update $NAME"
      echo "    claude plugin update ${SPEC:-$NAME@$NAME}"
      echo "  Pass --force to keep the plain copy anyway."
    fi
  else
    echo "  kept $SHADOW — no $NAME plugin is installed in this home, so this"
    echo "  plain copy is Claude Code's only channel for the skill"
  fi
fi

echo
# How the next version arrives — an installer that never says has still chosen
# an update model: never.
echo "Updates: npx @ssheleg/$NAME@latest update — every channel, and it settles the"
echo "plain copy that would shadow the plugin. Whole family: npx --yes sshlg-skills@latest update."
echo
echo "Restart Claude Code, then run /agent-sync init in your project."
