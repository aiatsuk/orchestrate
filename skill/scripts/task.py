#!/usr/bin/env python3
"""Run one mechanical step of a task and print its outcome as one JSON object.

Usage:
  task.py gate --run-dir DIR --label LABEL --worktree WT [--scope S ...] -- CMD [CMD ...]
  task.py scaffold --worktree WT --patch P [--patch P ...]
  task.py finish --worktree WT --patch P --scope S [--scope S ...]

Every subcommand prints exactly one JSON object on stdout, so an agent that runs it only relays
the output. Progress lines go to stderr. The objects follow skill/schemas/<subcommand>.schema.json.

`gate` runs the commands with gate.py's attempt reservation and logging inside WT (an attempt
directory `<run-dir>/<label>/`, or `<label>-r2/` ... when the label was used before) and prints
{attempt_dir, exit_code, results, tail, tree, head, dirty, unstaged, untracked, outside_scope}:
`results` is the attempt's result.json, `tail` holds the last 40 log lines of every failing
command (for a pass, the last 5 lines of the last command), `tree` is `git write-tree` of WT and
`head` its HEAD commit.
`unstaged` lists the tracked paths changed after staging, `untracked` the untracked paths that
.gitignore and .git/info/exclude do not cover (the gate's own artifacts land here), and
`outside_scope` the staged paths outside every `--scope S` (none when no scope is given); all three
are sorted and read NUL-separated, so paths with spaces or non-ASCII characters come back verbatim.
`dirty` is true when `unstaged` or `untracked` is non-empty, or when Git could not report them.
The same state is written to `<attempt_dir>/state.json` as {worktree, label, tree, head, unstaged,
untracked, outside_scope}, so a later check can tie the attempt to the worktree and tree it saw.
The exit status mirrors `exit_code` (0 or 1).

`scaffold` runs `integrate.sh scaffold WT P ...` and prints {exit_code, output, head}.

`finish` exports WT's staged diff to P with `integrate.sh export` (creating P's directory), then
runs `integrate.sh verify-clean WT P --scope S ...`, and prints {patch, sha256, files, tree,
verify_clean_exit, output}; `files` lists the staged paths, `output` combines both steps. An empty
staged diff fails with verify_clean_exit 1, since integration rejects empty patches. The exit
status is 0 only when verify-clean passed.

A usage error (for example a worktree that does not exist) prints {"error": "..."} and exits 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import gate as gate_runner  # noqa: E402

INTEGRATE = SCRIPTS / "integrate.sh"
FAIL_TAIL_LINES = 40
PASS_TAIL_LINES = 5


class JsonArgumentParser(argparse.ArgumentParser):
    """Report usage errors as one JSON object on stdout, like every other outcome."""

    def error(self, message: str):
        emit({"error": f"{self.prog}: {message}"}, 2)
        sys.exit(2)


def emit(obj: dict, code: int) -> int:
    print(json.dumps(obj, indent=2))
    return code


def git_out(worktree: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout


def write_tree(worktree: str) -> str:
    rc, out = git_out(worktree, "write-tree")
    return out.strip() if rc == 0 else ""


def git_paths(worktree: str, *args: str) -> tuple[bool, set[str]]:
    """Run a git command with `-z` path output; return whether it succeeded and the paths, verbatim."""
    proc = subprocess.run(["git", "-C", worktree, *args], capture_output=True, encoding="utf-8", errors="surrogateescape")
    return proc.returncode == 0, {path for path in proc.stdout.split("\0") if path}


def worktree_state(worktree: str, scopes: list[str]) -> tuple[dict, bool]:
    """Unstaged tracked paths, untracked paths, and staged paths outside the scopes (renames split).

    Returns the state and whether every git query succeeded.
    """
    ok_unstaged, unstaged = git_paths(worktree, "diff", "--no-renames", "--name-only", "-z")
    ok_untracked, untracked = git_paths(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    ok = ok_unstaged and ok_untracked
    outside: set[str] = set()
    if scopes:
        ok_staged, staged = git_paths(worktree, "diff", "--cached", "--no-renames", "--name-only", "-z")
        ok_allowed, allowed = git_paths(worktree, "diff", "--cached", "--no-renames", "--name-only", "-z", "--", *scopes)
        ok = ok and ok_staged and ok_allowed
        outside = staged - allowed
    return {"unstaged": sorted(unstaged), "untracked": sorted(untracked), "outside_scope": sorted(outside)}, ok


def last_lines(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-count:]
    except OSError:
        return []


def integrate(*args: str) -> tuple[int, str]:
    proc = subprocess.run([str(INTEGRATE), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def cmd_gate(args: argparse.Namespace) -> int:
    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        return emit({"error": f"worktree does not exist: {worktree}"}, 2)
    attempt, _, failed = gate_runner.run_commands(
        args.run_dir, args.label, args.commands, cwd=worktree, echo=lambda line: print(line, file=sys.stderr))
    results = json.loads((attempt / "result.json").read_text())
    if failed:
        tail: list[str] = []
        for entry in results:
            if entry["rc"] != 0:
                tail.append(f"$ {entry['command']}  ({entry['outcome']})")
                tail.extend(last_lines(attempt / Path(entry["log"]).name, FAIL_TAIL_LINES))
    else:
        tail = last_lines(attempt / Path(results[-1]["log"]).name, PASS_TAIL_LINES) if results else []
    exit_code = 1 if failed else 0
    state, ok = worktree_state(worktree, args.scope or [])
    tree = write_tree(worktree)
    head_rc, head = git_out(worktree, "rev-parse", "HEAD")
    head = head.strip() if head_rc == 0 else ""
    (attempt / "state.json").write_text(json.dumps(
        {"worktree": worktree, "label": args.label, "tree": tree, "head": head, **state}, indent=2))
    dirty = bool(state["unstaged"] or state["untracked"]) or not ok
    return emit({"attempt_dir": str(attempt), "exit_code": exit_code, "results": results, "tail": "\n".join(tail),
                 "tree": tree, "head": head, "dirty": dirty, **state}, exit_code)


def cmd_scaffold(args: argparse.Namespace) -> int:
    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        return emit({"error": f"worktree does not exist: {worktree}"}, 2)
    rc, output = integrate("scaffold", worktree, *[os.path.abspath(p) for p in args.patch])
    head_rc, head = git_out(worktree, "rev-parse", "HEAD")
    return emit({"exit_code": rc, "output": output, "head": head.strip() if head_rc == 0 else ""}, 0 if rc == 0 else 1)


def sha256_of(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def cmd_finish(args: argparse.Namespace) -> int:
    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        return emit({"error": f"worktree does not exist: {worktree}"}, 2)
    patch = os.path.abspath(args.patch)
    os.makedirs(os.path.dirname(patch), exist_ok=True)
    rc, output = integrate("export", worktree, patch)
    if rc == 0 and os.path.getsize(patch) == 0:
        rc, output = 1, output + "empty staged diff: a passed task must export a non-empty patch\n"
    elif rc == 0:
        rc, verify_output = integrate("verify-clean", worktree, patch, *[a for s in args.scope for a in ("--scope", s)])
        output += verify_output
    files_rc, files = git_out(worktree, "diff", "--cached", "--name-only")
    return emit({"patch": patch, "sha256": sha256_of(patch), "files": files.splitlines() if files_rc == 0 else [],
                 "tree": write_tree(worktree), "verify_clean_exit": rc, "output": output}, 0 if rc == 0 else 1)


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--run-dir", required=True)
    p_gate.add_argument("--label", required=True)
    p_gate.add_argument("--worktree", required=True)
    p_gate.add_argument("--scope", action="append", default=[], help="repeatable; staged paths outside every scope are listed in outside_scope")
    p_gate.add_argument("commands", nargs="+")
    p_scaffold = sub.add_parser("scaffold")
    p_scaffold.add_argument("--worktree", required=True)
    p_scaffold.add_argument("--patch", action="append", required=True)
    p_finish = sub.add_parser("finish")
    p_finish.add_argument("--worktree", required=True)
    p_finish.add_argument("--patch", required=True)
    p_finish.add_argument("--scope", action="append", required=True)
    args = parser.parse_args(argv)
    return {"gate": cmd_gate, "scaffold": cmd_scaffold, "finish": cmd_finish}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
