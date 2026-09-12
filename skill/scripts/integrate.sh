#!/usr/bin/env bash
# Patch export, integration and tree-equality checks for orchestrated runs.
#
#   integrate.sh export <worktree> <out.patch>
#       Write the worktree's staged diff (renames and binaries included) to out.patch.
#   integrate.sh apply <integration-worktree> <patch> [<patch> ...]
#       Check that every patch applies, then apply each with --3way and stage the result.
#       Prints the resulting index tree hash. Fails before touching anything if a check fails.
#   integrate.sh same-tree <worktree-a> <worktree-b>
#       Exit 0 when both worktrees' staged trees are identical, 1 otherwise.
set -euo pipefail

usage() { sed -n '2,12p' "$0"; exit 2; }

cmd=${1:-}; shift || true
case "$cmd" in
  export)
    [ $# -eq 2 ] || usage
    git -C "$1" diff --cached -M --binary > "$2"
    echo "exported $(grep -c '^diff --git' "$2") file diffs to $2"
    ;;
  apply)
    [ $# -ge 2 ] || usage
    wt=$1; shift
    for p in "$@"; do git -C "$wt" apply --check "$p"; done
    for p in "$@"; do git -C "$wt" apply --3way "$p" >/dev/null; echo "applied $p"; done
    git -C "$wt" add -A
    echo "tree $(git -C "$wt" write-tree)"
    ;;
  same-tree)
    [ $# -eq 2 ] || usage
    a=$(git -C "$1" write-tree); b=$(git -C "$2" write-tree)
    if [ "$a" = "$b" ]; then echo "identical $a"; else echo "differ $a $b"; exit 1; fi
    ;;
  *) usage ;;
esac
