#!/usr/bin/env bash
# Install the orchestrate skill and its named roles.
#
#   ./install.sh                 user scope: symlink skill/ into ~/.claude/skills and ~/.agents/skills, symlink the
#                                Claude roles into ~/.claude/agents, copy the Codex roles into ~/.codex/agents
#                                (Codex ignores symlinked role files), and copy the workflow scripts into
#                                ~/.claude/workflows so they run as saved commands (/orchestrate-execute);
#                                rerun it after an update to refresh the copies
#   ./install.sh --uninstall     remove the user-scope symlinks created by this script
#   ./install.sh --repo <path> [--force]
#                                project scope: copy skill/, the roles and the workflow scripts into <path>/.claude,
#                                <path>/.agents, <path>/.codex and append the orchestration block to <path>/AGENTS.md;
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
unlink_if_ours() { local t=$1 dest; if [ -L "$t" ]; then dest=$(readlink "$t"); if [ "$dest" = "$src" ] || [[ "$dest" == "$src/"* ]]; then rm "$t"; echo "removed $t"; return; fi; fi; if [ -e "$t" ] || [ -L "$t" ]; then echo "not ours, left alone: $t"; fi; }

user_targets() { # symlink targets
  echo "$src|$HOME/.claude/skills/orchestrate"
  echo "$src|$HOME/.agents/skills/orchestrate"
  for f in "$src"/claude/agents/*.md; do echo "$f|$HOME/.claude/agents/$(basename "$f")"; done
}
codex_role_targets() { # copy targets: Codex does not load symlinked agent files
  for f in "$src"/codex/agents/*.toml; do echo "$f|$HOME/.codex/agents/$(basename "$f")"; done
}
workflow_targets() { # copy targets: a saved workflow must be a regular file
  for f in "$src"/workflows/*.js; do echo "$f|$HOME/.claude/workflows/$(basename "$f")"; done
}
ours_workflow() { # a workflow file this repository installed: its meta block names it after the file
  local t=$1 name; name=$(basename "$t" .js)
  [ -f "$t" ] && [ ! -L "$t" ] && head -n 1 "$t" | grep -q '^export const meta = {' && grep -q "^  name: '$name',\$" "$t" && [[ "$name" == orchestrate-* ]]
}
copy_workflow() { local s=$1 t=$2; mkdir -p "$(dirname "$t")"
  if [ -e "$t" ] || [ -L "$t" ]; then
    if [ -L "$t" ] || ours_workflow "$t"; then rm "$t"; else echo "not ours, left alone: $t"; return; fi
  fi
  cp "$s" "$t"; echo "copied $t"; }
remove_workflow() { local t=$1; if ours_workflow "$t"; then rm "$t"; echo "removed $t"; elif [ -e "$t" ] || [ -L "$t" ]; then echo "not ours, left alone: $t"; fi; }
copy_role() { local s=$1 t=$2; mkdir -p "$(dirname "$t")"; if [ -L "$t" ]; then rm "$t"; fi; cp "$s" "$t"; echo "copied $t"; }
remove_role() { local t=$1; if [ -f "$t" ] && grep -q '^name = "orchestrate-' "$t"; then rm "$t"; echo "removed $t"; elif [ -e "$t" ]; then echo "not ours, left alone: $t"; fi; }

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
    user_targets | while IFS='|' read -r s t; do link "$s" "$t"; done
    codex_role_targets | while IFS='|' read -r s t; do copy_role "$s" "$t"; done
    workflow_targets | while IFS='|' read -r s t; do copy_workflow "$s" "$t"; done ;;
  uninstall)
    user_targets | while IFS='|' read -r s t; do unlink_if_ours "$t"; done
    codex_role_targets | while IFS='|' read -r s t; do remove_role "$t"; done
    workflow_targets | while IFS='|' read -r s t; do remove_workflow "$t"; done ;;
  repo)
    [ -d "$repo" ] || { echo "not a directory: $repo" >&2; exit 2; }
    repo=$(cd "$repo" && pwd)
    copy_into "$src" "$repo/.claude/skills/orchestrate" "$force"
    copy_into "$src" "$repo/.agents/skills/orchestrate" "$force"
    for f in "$src"/claude/agents/*.md; do copy_into "$f" "$repo/.claude/agents/$(basename "$f")" "$force"; done
    for f in "$src"/codex/agents/*.toml; do copy_into "$f" "$repo/.codex/agents/$(basename "$f")" "$force"; done
    for f in "$src"/workflows/*.js; do copy_into "$f" "$repo/.claude/workflows/$(basename "$f")" "$force"; done
    append_agents_block "$repo" ;;
esac
