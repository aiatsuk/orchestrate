#!/usr/bin/env bash
# Create one git worktree per task from a base ref and fetch dependencies in each.
#
#   prepare_worktrees.sh --repo <path> --base <ref> --parent <dir> --prefix <name> [--deps "<cmd>"] [--log-dir <dir>] <slug> [<slug> ...]
#
# Each slug becomes worktree <parent>/<dirprefix>-<slug> on branch <prefix>/<slug> cut from <ref>, where
# <dirprefix> is <prefix> with every '/' replaced by '-' (a prefix such as feature/run gives feature-run-<slug>).
# The optional --deps command runs inside every worktree (for example "npm ci" or "just get"); its log is
# written to <log-dir>/<dirprefix>-<slug>.deps.log (default: next to the worktree), never inside it.
set -euo pipefail

repo=""; base=""; parent=""; prefix=""; deps=""; logdir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo=$2; shift 2 ;;
    --base) base=$2; shift 2 ;;
    --parent) parent=$2; shift 2 ;;
    --prefix) prefix=$2; shift 2 ;;
    --deps) deps=$2; shift 2 ;;
    --log-dir) logdir=$2; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ -n "$repo" ] && [ -n "$base" ] && [ -n "$parent" ] && [ -n "$prefix" ] && [ $# -ge 1 ] || { sed -n '2,8p' "$0"; exit 2; }

mkdir -p "$parent"
dirprefix=${prefix//\//-}
[ -n "$logdir" ] || logdir=$parent
mkdir -p "$logdir"
for slug in "$@"; do
  wt="$parent/$dirprefix-$slug"
  git -C "$repo" worktree add -q -b "$prefix/$slug" "$wt" "$base"
  log="$logdir/$dirprefix-$slug.deps.log"
  if [ -n "$deps" ]; then (cd "$wt" && sh -c "$deps" > "$log" 2>&1) || { echo "deps failed in $wt, see $log" >&2; exit 1; }; fi
  echo "$wt"
done
