#!/usr/bin/env python3
"""Run gate commands with logs and a status trail, or diff analyzer issues against a baseline.

Usage:
  gate.py run --run-dir DIR --label LABEL [--cwd DIR] [--status FILE] -- CMD [CMD ...]
  gate.py delta --baseline FILE --current FILE [--issue-regex RE]

`run` executes each CMD through the shell, writes `<label>-<n>.log` and `<label>.json` into the
run directory, appends one line per command to the status file (default `<run-dir>/status.md`),
and exits non-zero if any command failed. Any non-zero exit counts as a failure, including a
negative code (the command was killed by a signal). A label that already has logs gets a numbered
suffix (`<label>-r2`, `-r3`, ...) so earlier logs are never overwritten.

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

# Dart/Flutter analyzer lines start with the severity; ESLint stylish lines start with line:col.
DEFAULT_ISSUE_REGEX = r"^\s*(info|warning|error)\b|^\s*\d+:\d+\s+(warning|error)\b"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status = Path(args.status) if args.status else run_dir / "status.md"
    label = unique_label(run_dir, args.label)
    results = []
    failed = False
    for index, cmd in enumerate(args.commands, start=1):
        log = run_dir / f"{label}-{index}.log"
        start = dt.datetime.now()
        proc = subprocess.run(cmd, shell=True, cwd=args.cwd, capture_output=True, text=True)
        seconds = round((dt.datetime.now() - start).total_seconds(), 2)
        log.write_text(proc.stdout + proc.stderr)
        rc = proc.returncode
        outcome = "ok" if rc == 0 else (f"killed by signal {-rc}" if rc < 0 else f"exit {rc}")
        results.append({"command": cmd, "rc": rc, "outcome": outcome, "seconds": seconds, "log": log.name})
        failed = failed or rc != 0
        with status.open("a") as out:
            out.write(f"{_now()} - {label}: `{cmd}` {outcome}, {seconds}s; {log.name}\n")
        print(f"[{label}] {outcome} {seconds}s :: {cmd}")
    (run_dir / f"{label}.json").write_text(json.dumps(results, indent=2))
    return 1 if failed else 0


def unique_label(run_dir: Path, label: str) -> str:
    if not (run_dir / f"{label}.json").exists():
        return label
    n = 2
    while (run_dir / f"{label}-r{n}.json").exists():
        n += 1
    return f"{label}-r{n}"


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
