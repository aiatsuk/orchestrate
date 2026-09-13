#!/usr/bin/env bash
# Patch export, all-or-nothing integration, and worktree safety checks for orchestrated runs.
#
#   integrate.sh export <worktree> <out.patch>
#       Write the worktree's staged diff (renames and binaries included) to out.patch.
#   integrate.sh apply <integration-worktree> <patch> [<patch> ...]
#       Apply the patches in order on a temporary worktree cut from the integration worktree's
#       current index (so a later patch may depend on an earlier one). If every patch applies, the
#       resulting tree is adopted into the integration worktree's index and files; if any fails,
#       nothing in the integration worktree changes. Only the patches' files are staged.
#   integrate.sh verify-clean <worktree> <patch> [--scope <pathspec> ...]
#       Exit 0 only when the worktree has no unstaged changes and no untracked files, its staged
#       diff equals <patch> byte for byte, and (with --scope) every staged path matches one of the
#       pathspecs. This is the check to run before a worktree is removed.
#   integrate.sh same-tree <worktree-a> <worktree-b>
#       Exit 0 when both worktrees' staged trees are identical (integration versus a replay).
set -euo pipefail

usage() { sed -n '2,18p' "$0"; exit 2; }
die() { echo "integrate.sh: $*" >&2; exit 1; }

cmd=${1:-}; shift || true
case "$cmd" in
  export)
    [ $# -eq 2 ] || usage
    git -C "$1" diff --cached -M --binary > "$2"
    echo "exported $(grep -c '^diff --git' "$2" || true) file diffs to $2"
    ;;
  apply)
    [ $# -ge 2 ] || usage
    wt=$1; shift
    for p in "$@"; do [ -s "$p" ] || die "empty or missing patch: $p"; done
    base_tree=$(git -C "$wt" write-tree)
    base_commit=$(GIT_AUTHOR_NAME=orchestrate GIT_AUTHOR_EMAIL=orchestrate@localhost GIT_COMMITTER_NAME=orchestrate GIT_COMMITTER_EMAIL=orchestrate@localhost \
      git -C "$wt" commit-tree "$base_tree" -p HEAD -m "integration staging (temporary)")
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/integrate.XXXXXX")
    git -C "$wt" worktree add -q --detach "$tmp/wt" "$base_commit"
    cleanup() { git -C "$wt" worktree remove --force "$tmp/wt" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
    trap cleanup EXIT
    for p in "$@"; do
      if ! git -C "$tmp/wt" apply --index --3way "$p" >/dev/null 2>&1; then
        die "patch does not apply in sequence, integration worktree left unchanged: $p"
      fi
      echo "applied $p"
    done
    new_tree=$(git -C "$tmp/wt" write-tree)
    git -C "$wt" read-tree -m -u "$new_tree"
    echo "tree $(git -C "$wt" write-tree)"
    ;;
  verify-clean)
    [ $# -ge 2 ] || usage
    wt=$1; patch=$2; shift 2
    scopes=()
    while [ $# -gt 0 ]; do case "$1" in --scope) scopes+=("$2"); shift 2 ;; *) usage ;; esac; done
    status=0
    if [ -n "$(git -C "$wt" diff --name-only)" ]; then echo "unstaged changes present"; status=1; fi
    untracked=$(git -C "$wt" ls-files --others --exclude-standard)
    if [ -n "$untracked" ]; then echo "untracked files present:"; echo "$untracked" | sed 's/^/  /'; status=1; fi
    if ! git -C "$wt" diff --cached -M --binary | cmp -s - "$patch"; then echo "staged diff differs from $patch"; status=1; fi
    if [ ${#scopes[@]} -gt 0 ]; then
      allowed=$(git -C "$wt" diff --cached --name-only -- "${scopes[@]}" | sort)
      staged=$(git -C "$wt" diff --cached --name-only | sort)
      outside=$(comm -23 <(echo "$staged") <(echo "$allowed"))
      if [ -n "$outside" ]; then echo "staged paths outside the allowed scope:"; echo "$outside" | sed 's/^/  /'; status=1; fi
    fi
    [ $status -eq 0 ] && echo "clean: staged diff equals $patch, nothing unstaged or untracked"
    exit $status
    ;;
  same-tree)
    [ $# -eq 2 ] || usage
    a=$(git -C "$1" write-tree); b=$(git -C "$2" write-tree)
    if [ "$a" = "$b" ]; then echo "identical $a"; else echo "differ $a $b"; exit 1; fi
    ;;
  *) usage ;;
esac
