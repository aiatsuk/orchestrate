#!/usr/bin/env bash
# Patch export, all-or-nothing integration, and worktree safety checks for orchestrated runs.
#
#   integrate.sh export <worktree> <out.patch>
#       Write the worktree's staged diff (renames and binaries included) to out.patch.
#   integrate.sh apply <integration-worktree> <patch> [<patch> ...]
#       Apply the patches in order on a temporary worktree cut from the integration worktree's
#       current index (so a later patch may depend on an earlier one). If every patch applies, the
#       resulting tree is adopted into the integration worktree's index and files; if any fails,
#       nothing in the integration worktree changes. Only the patches' files are staged. Adoption is
#       refused when it would overwrite an untracked or ignored file present in the worktree.
#   integrate.sh verify-clean <worktree> <patch> [--scope <pathspec> ...]
#       Exit 0 only when the worktree has no unstaged changes and no untracked files, its staged
#       diff equals <patch> byte for byte, and (with --scope) every staged path, including the old
#       path of a rename, matches one of the pathspecs. This is the check to run before a worktree
#       is removed.
#   integrate.sh same-tree <worktree-a> <worktree-b>
#       Exit 0 when both worktrees' staged trees are identical (integration versus a replay).
#   integrate.sh scaffold <worktree> <patch> [<patch> ...]
#       Prepare a dependent task's worktree: refuse when it has staged, unstaged or untracked
#       changes, apply the patches in order with `git apply --index --3way`, and commit exactly
#       their paths as one commit with the fixed identity `apply` uses. Its message is
#       "scaffolding (temporary)", a blank line, and `patches: <sha256 of each patch, in order>`.
#       All or nothing: when a patch fails, the index and files of every path the patches touched
#       are restored to HEAD and the failing patch is named. Prints `head <sha>`. Afterwards the
#       staged diff is empty, so the task's own later diff contains only its own change.
#       Idempotent: when HEAD's message already carries exactly that `patches:` line, it prints
#       `head <sha> (already scaffolded)` and exits 0 without touching the worktree, even when the
#       task has since staged or changed files there. Never stacked: when HEAD is a scaffolding
#       commit for other patches (a dependency's patch changed), it refuses, whether or not the
#       worktree is clean, and prints the recovery with the real paths: keep the task's own work,
#       remove the worktree and its branch, prepare it again from the commit's parent (or reset to
#       that parent and remove untracked files when nothing needs keeping), and scaffold again.
set -euo pipefail

usage() { sed -n '2,33p' "$0"; exit 2; }
die() { echo "integrate.sh: $*" >&2; exit 1; }
abspath() { case $1 in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$PWD/$1" ;; esac; }
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
IDENTITY=(GIT_AUTHOR_NAME=orchestrate GIT_AUTHOR_EMAIL=orchestrate@localhost GIT_COMMITTER_NAME=orchestrate GIT_COMMITTER_EMAIL=orchestrate@localhost)

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
    base_commit=$(env "${IDENTITY[@]}" git -C "$wt" commit-tree "$base_tree" -p HEAD -m "integration staging (temporary)")
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
    # refuse to overwrite files that exist in the integration worktree but are not tracked (ignored or stray)
    clobber=""
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      if [ -e "$wt/$path" ] && ! git -C "$wt" ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then clobber="$clobber $path"; fi
    done < <(git -C "$wt" diff --name-only --diff-filter=A "$base_tree" "$new_tree")
    [ -z "$clobber" ] || die "would overwrite untracked or ignored files in the integration worktree:$clobber"
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
      # --no-renames: a rename shows as a deletion of the old path and an addition of the new one,
      # so moving a file from outside the scope into it is caught on the old path
      allowed=$(git -C "$wt" diff --cached --no-renames --name-only -- "${scopes[@]}" | sort)
      staged=$(git -C "$wt" diff --cached --no-renames --name-only | sort)
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
  scaffold)
    [ $# -ge 2 ] || usage
    wt=$1; shift
    patches=(); sums=()
    for p in "$@"; do [ -s "$p" ] || die "empty or missing patch: $p"; patches+=("$(abspath "$p")"); sums+=("$(sha256 "$p")"); done
    marker="patches: ${sums[*]}"
    message=$'\n'"$(git -C "$wt" log -1 --format=%B HEAD)"$'\n'
    case $message in
      *$'\n'"$marker"$'\n'*) echo "head $(git -C "$wt" rev-parse HEAD) (already scaffolded)"; exit 0 ;;
    esac
    if [ "$(git -C "$wt" log -1 --format=%s HEAD)" = "scaffolding (temporary)" ]; then
      stale=$(git -C "$wt" rev-parse HEAD); parent=$(git -C "$wt" rev-parse HEAD^)
      recorded=""
      while IFS= read -r line; do case $line in "patches: "*) recorded=${line#patches: }; break ;; esac; done <<< "$message"
      # the main worktree is listed first; the task's branch and conventional path give the prepare call
      repo=$(git -C "$wt" worktree list --porcelain | sed -n '1s/^worktree //p')
      branch=$(git -C "$wt" symbolic-ref --quiet --short HEAD || true)
      wt_abs=$(cd "$wt" && pwd -P)
      prefix=${branch%/*}; slug=${branch##*/}; wt_parent=$(dirname "$wt_abs")
      q() { printf '%q' "$1"; }
      {
        echo "integrate.sh: refusing to scaffold $wt: HEAD $stale is a scaffolding commit for other patches;"
        echo "  recorded:  patches: ${recorded:-(none recorded)}"
        echo "  requested: $marker"
        echo "Scaffolding is never stacked on stale scaffolding. First preserve the task's own work if there is any"
        echo "(for example: $(q "$SCRIPT_DIR/integrate.sh") export $(q "$wt_abs") <file>). Then recreate the worktree:"
        if [ -n "$branch" ]; then
          echo "  git -C $(q "$repo") worktree remove --force $(q "$wt_abs") && git -C $(q "$repo") branch -D $(q "$branch")"
        else
          echo "  git -C $(q "$repo") worktree remove --force $(q "$wt_abs")"
        fi
        if [ -n "$branch" ] && [ "$branch" != "$slug" ] && [ "$wt_parent/${prefix//\//-}-$slug" = "$wt_abs" ]; then
          echo "  $(q "$SCRIPT_DIR/prepare_worktrees.sh") --repo $(q "$repo") --base $parent --parent $(q "$wt_parent") --prefix $(q "$prefix") $(q "$slug")"
          echo "  (add the run's --deps and --log-dir options if it used them)"
        else
          echo "  git -C $(q "$repo") worktree add -b $(q "${branch:-<branch>}") $(q "$wt_abs") $parent"
          echo "  (then fetch its dependencies, as the run's --deps command did)"
        fi
        echo "then run plan.py workflow-args again (the new worktree has a new worktree_id and start_head) and the scaffold."
        echo "When nothing needs keeping, git -C $(q "$wt_abs") reset --hard $parent also works, but it leaves untracked"
        echo "files behind; remove them too (git -C $(q "$wt_abs") clean -fd) before running the scaffold again."
      } >&2
      exit 1
    fi
    git -C "$wt" diff --cached --quiet || die "refusing to scaffold $wt: staged changes present"
    git -C "$wt" diff --quiet || die "refusing to scaffold $wt: unstaged changes present"
    [ -z "$(git -C "$wt" ls-files --others --exclude-standard)" ] || die "refusing to scaffold $wt: untracked files present"
    for p in "${patches[@]}"; do
      if ! out=$(git -C "$wt" apply --index --3way "$p" 2>&1); then
        # The worktree was clean, so every path that now differs from HEAD came from the patches.
        touched=()
        while IFS= read -r -d '' path; do touched+=("$path"); done < <(
          { git -C "$wt" diff --cached --no-renames --name-only -z HEAD; git -C "$wt" diff --no-renames --name-only -z; } | sort -zu)
        if [ ${#touched[@]} -gt 0 ]; then git -C "$wt" restore --source=HEAD --staged --worktree -- "${touched[@]}"; fi
        printf '%s\n' "$out" >&2
        die "patch does not apply in sequence, worktree restored to HEAD: $p"
      fi
      echo "applied $p"
    done
    tree=$(git -C "$wt" write-tree)
    commit=$(env "${IDENTITY[@]}" git -C "$wt" commit-tree "$tree" -p HEAD -m "scaffolding (temporary)" -m "$marker")
    git -C "$wt" update-ref -m "scaffolding (temporary)" HEAD "$commit"
    echo "head $commit"
    ;;
  *) usage ;;
esac
