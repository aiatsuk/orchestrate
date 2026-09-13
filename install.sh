#!/usr/bin/env bash
# Install the orchestrate skill and its named roles.
#
#   ./install.sh                 user scope: symlink skill/ into ~/.claude/skills and ~/.agents/skills,
#                                and the role files into ~/.claude/agents and ~/.codex/agents
#   ./install.sh --uninstall     remove the user-scope symlinks created by this script
#   ./install.sh --repo <path> [--force]
#                                project scope: copy skill/ and the roles into <path>/.claude, <path>/.agents,
#                                <path>/.codex and append the orchestration block to <path>/AGENTS.md;
#                                existing files are kept unless --force
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
src="$here/skill"

link() { # link <source> <target>
  local s=$1 t=$2
  mkdir -p "$(dirname "$t")"
  if [ -L "$t" ]; then rm "$t"
  elif [ -e "$t" ]; then local bak="$t.bak-$(date +%Y%m%d-%H%M%S)"; mv "$t" "$bak"; echo "moved existing $t to $bak"; fi
  ln -s "$s" "$t"; echo "linked $t -> $s"
}
unlink_if_ours() { local t=$1; if [ -L "$t" ]; then rm "$t"; echo "removed $t"; else echo "not a symlink, left alone: $t"; fi; }

user_targets() {
  echo "$src|$HOME/.claude/skills/orchestrate"
  echo "$src|$HOME/.agents/skills/orchestrate"
  for f in "$src"/claude/agents/*.md; do echo "$f|$HOME/.claude/agents/$(basename "$f")"; done
  for f in "$src"/codex/agents/*.toml; do echo "$f|$HOME/.codex/agents/$(basename "$f")"; done
}

append_agents_block() { # append_agents_block <repo>
  local file="$1/AGENTS.md" marker="<!-- orchestrate: begin -->"
  if [ -f "$file" ] && grep -qF "$marker" "$file"; then echo "AGENTS.md already carries the orchestration block"; return; fi
  [ -f "$file" ] && printf '\n' >> "$file"
  cat >> "$file" <<'BLOCK'
<!-- orchestrate: begin -->
## Orchestration

For work that spans several files, needs independent verification, or has two or more independent
parts, use the `orchestrate` skill (`/orchestrate` in Claude Code, `$orchestrate` in Codex). The
orchestrator writes a spec per task, runs each task in its own git worktree through the
`orchestrate-implementer` role, verifies with a gate plus the read-only `orchestrate-reviewer`, and
integrates on a separate branch. Do not delegate trivial one-file edits. Never let two agents edit
the same files in parallel. User instructions take precedence over this policy.
<!-- orchestrate: end -->
BLOCK
  echo "appended the orchestration block to $file"
}

copy_into() { # copy_into <source> <target> <force>
  local s=$1 t=$2 force=$3
  if [ -e "$t" ] && [ "$force" != "1" ]; then echo "kept existing $t (use --force to replace)"; return; fi
  mkdir -p "$(dirname "$t")"; rm -rf "$t"; cp -R "$s" "$t"; echo "installed $t"
}

mode=user; repo=""; force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) mode=uninstall; shift ;;
    --repo) mode=repo; repo=$2; shift 2 ;;
    --force) force=1; shift ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
  esac
done

case "$mode" in
  user)
    user_targets | while IFS='|' read -r s t; do link "$s" "$t"; done ;;
  uninstall)
    user_targets | while IFS='|' read -r s t; do unlink_if_ours "$t"; done ;;
  repo)
    [ -d "$repo" ] || { echo "not a directory: $repo" >&2; exit 2; }
    repo=$(cd "$repo" && pwd)
    copy_into "$src" "$repo/.claude/skills/orchestrate" "$force"
    copy_into "$src" "$repo/.agents/skills/orchestrate" "$force"
    for f in "$src"/claude/agents/*.md; do copy_into "$f" "$repo/.claude/agents/$(basename "$f")" "$force"; done
    for f in "$src"/codex/agents/*.toml; do copy_into "$f" "$repo/.codex/agents/$(basename "$f")" "$force"; done
    append_agents_block "$repo" ;;
esac
