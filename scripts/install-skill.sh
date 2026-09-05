#!/usr/bin/env bash
# Link (or copy) the sssf skill into a coding agent's skill directory.
#
# The skill is plain SKILL.md + scripts + templates, which is the format
# Claude Code, pi, Codex CLI and opencode all read. Only the directory each
# one scans differs, and that is the whole job of this script.
#
#   scripts/install-skill.sh --agent pi
#   scripts/install-skill.sh --agent codex --scope project --project ~/work/api
#   scripts/install-skill.sh --agent all --copy
#
# Symlinks by default, so `git pull` here updates every agent at once. Pass
# --copy when the target has to survive this checkout going away.
set -euo pipefail

SKILL_NAME="sssf"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/${SKILL_NAME}"

AGENT=""
SCOPE="user"
PROJECT="$PWD"
MODE="link"
FORCE=0

usage() {
    cat <<'USAGE'
usage: install-skill.sh --agent <name> [--scope user|project] [--project DIR]
                        [--copy] [--force]

  --agent    claude | pi | codex | opencode | agents | all
             `agents` is the vendor-neutral ~/.agents/skills directory, which
             pi and opencode both read — one install, two agents.
  --scope    user (default) installs for every repo; project installs into
             --project only.
  --project  target repo for --scope project (default: the current directory)
  --copy     copy instead of symlinking
  --force    replace an existing sssf entry in the target directory

Directories written, by agent:

  agent     user scope                      project scope
  claude    ~/.claude/skills                <project>/.claude/skills
  pi        ~/.pi/agent/skills              <project>/.pi/skills
  codex     ~/.codex/skills                 <project>/.codex/skills
  opencode  ~/.config/opencode/skills       <project>/.opencode/skills
  agents    ~/.agents/skills                <project>/.agents/skills
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --agent)   AGENT="${2:?--agent needs a value}"; shift 2 ;;
        --scope)   SCOPE="${2:?--scope needs a value}"; shift 2 ;;
        --project) PROJECT="${2:?--project needs a value}"; shift 2 ;;
        --copy)    MODE="copy"; shift ;;
        --force)   FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[ -n "$AGENT" ] || { echo "--agent is required" >&2; usage >&2; exit 2; }
[ -d "$SRC" ] || { echo "skill not found at $SRC" >&2; exit 1; }

case "$SCOPE" in
    user|project) ;;
    *) echo "--scope must be user or project" >&2; exit 2 ;;
esac

# One agent, one directory. Kept as a case rather than a map so the failure
# for an unknown agent names the ones that exist.
skills_dir() {
    case "$1" in
        claude)   [ "$SCOPE" = user ] && echo "$HOME/.claude/skills"           || echo "$PROJECT/.claude/skills" ;;
        pi)       [ "$SCOPE" = user ] && echo "$HOME/.pi/agent/skills"         || echo "$PROJECT/.pi/skills" ;;
        codex)    [ "$SCOPE" = user ] && echo "$HOME/.codex/skills"            || echo "$PROJECT/.codex/skills" ;;
        opencode) [ "$SCOPE" = user ] && echo "$HOME/.config/opencode/skills"  || echo "$PROJECT/.opencode/skills" ;;
        agents)   [ "$SCOPE" = user ] && echo "$HOME/.agents/skills"           || echo "$PROJECT/.agents/skills" ;;
        *) echo "unknown agent: $1 (claude, pi, codex, opencode, agents, all)" >&2; exit 2 ;;
    esac
}

install_one() {
    local agent="$1" dir dest
    dir="$(skills_dir "$agent")"
    dest="$dir/$SKILL_NAME"

    if [ -e "$dest" ] || [ -L "$dest" ]; then
        if [ "$FORCE" -eq 0 ]; then
            echo "  $agent: $dest exists — pass --force to replace it"
            return 0
        fi
        rm -rf "$dest"
    fi

    mkdir -p "$dir"
    if [ "$MODE" = copy ]; then
        cp -R "$SRC" "$dest"
        echo "  $agent: copied to $dest"
    else
        ln -s "$SRC" "$dest"
        echo "  $agent: linked $dest -> $SRC"
    fi
}

if [ "$AGENT" = all ]; then
    AGENTS="claude pi codex opencode agents"
else
    skills_dir "$AGENT" >/dev/null   # validates the name before anything is written
    AGENTS="$AGENT"
fi

echo "sssf skill: $SRC"
for a in $AGENTS; do install_one "$a"; done

cat <<'NEXT'

next: open the target repo in that agent and ask it to install the factory
      (Claude Code: /sssf:sssf install · pi: /skill:sssf install · otherwise
      "use the sssf skill to install the factory"), or run the installer
      directly from the repo root:

      uv run <skill>/scripts/install.py
NEXT
