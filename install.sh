#!/usr/bin/env bash
# Link (or unlink) the skill into the user-scope skill directories of both harnesses.
#   ./install.sh              create symlinks; existing directories are moved aside with a .bak-<date> suffix
#   ./install.sh --uninstall  remove the symlinks created by this script
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
src="$here/skill"
targets=("$HOME/.claude/skills/orchestrate" "$HOME/.agents/skills/orchestrate")

for target in "${targets[@]}"; do
  if [ "${1:-}" = "--uninstall" ]; then
    if [ -L "$target" ]; then rm "$target"; echo "removed $target"; else echo "not a symlink, left alone: $target"; fi
    continue
  fi
  mkdir -p "$(dirname "$target")"
  if [ -L "$target" ]; then rm "$target"
  elif [ -e "$target" ]; then bak="$target.bak-$(date +%Y%m%d-%H%M%S)"; mv "$target" "$bak"; echo "moved existing $target to $bak"; fi
  ln -s "$src" "$target"
  echo "linked $target -> $src"
done
