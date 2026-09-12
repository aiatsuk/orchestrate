#!/usr/bin/env python3
"""Run gate commands with logs and a status trail, or diff analyzer issues against a baseline.

Usage:
  gate.py run --run-dir DIR --label LABEL [--cwd DIR] [--status FILE] -- CMD [CMD ...]
  gate.py delta --baseline FILE --current FILE [--issue-regex RE]

`run` executes each CMD through the shell, writes `<label>-<n>.log` and `<label>.json` into the
run directory, appends one line per command to the status file (default `<run-dir>/status.md`),
and exits non-zero if any command failed.

`delta` extracts issue lines from two analyzer logs and prints the ones that are new in `current`.
It exits 1 when there is at least one new issue, so it can serve as a gate on its own.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ISSUE_REGEX = r"^\s*(info|warning|error)\b"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Path(args.status) if args.status else run_dir / "status.md"
    results = []
    worst = 0
    for index, cmd in enumerate(args.commands, start=1):
        log = run_dir / f"{args.label}-{index}.log"
        start = dt.datetime.now()
        proc = subprocess.run(cmd, shell=True, cwd=args.cwd, capture_output=True, text=True)
        seconds = round((dt.datetime.now() - start).total_seconds(), 2)
        log.write_text(proc.stdout + proc.stderr)
        results.append({"command": cmd, "rc": proc.returncode, "seconds": seconds, "log": log.name})
        worst = max(worst, proc.returncode)
        with status.open("a") as out:
            out.write(f"{_now()} - {args.label}: `{cmd}` exit {proc.returncode}, {seconds}s; {log.name}\n")
        print(f"[{args.label}] rc={proc.returncode} {seconds}s :: {cmd}")
    (run_dir / f"{args.label}.json").write_text(json.dumps(results, indent=2))
    return 1 if worst else 0


def issues(text: str, regex: str) -> list[str]:
    pattern = re.compile(regex)
    return sorted({line.strip() for line in text.splitlines() if pattern.search(line)})


def delta(args: argparse.Namespace) -> int:
    baseline = issues(Path(args.baseline).read_text(), args.issue_regex)
    current = issues(Path(args.current).read_text(), args.issue_regex)
    new = [line for line in current if line not in baseline]
    gone = [line for line in baseline if line not in current]
    print(f"baseline={len(baseline)} current={len(current)} new={len(new)} resolved={len(gone)}")
    for line in new:
        print(f"NEW: {line}")
    for line in gone:
        print(f"RESOLVED: {line}")
    return 1 if new else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--run-dir", required=True)
    p_run.add_argument("--label", required=True)
    p_run.add_argument("--cwd", default=None)
    p_run.add_argument("--status", default=None)
    p_run.add_argument("commands", nargs="+")
    p_delta = sub.add_parser("delta")
    p_delta.add_argument("--baseline", required=True)
    p_delta.add_argument("--current", required=True)
    p_delta.add_argument("--issue-regex", default=DEFAULT_ISSUE_REGEX)
    args = parser.parse_args(argv)
    return run(args) if args.mode == "run" else delta(args)


if __name__ == "__main__":
    sys.exit(main())
