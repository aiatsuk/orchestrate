#!/usr/bin/env python3
"""Run gate commands with logs and a status trail, or diff analyzer issues against a baseline.

Usage:
  gate.py run --run-dir DIR --label LABEL [--cwd DIR] [--status FILE] -- CMD [CMD ...]
  gate.py delta --baseline FILE --current FILE [--issue-regex RE]

`run` reserves an attempt directory atomically before the first command (`<run-dir>/<label>/`, or
`<label>-r2/`, `-r3/` ... when the label was used before, whether or not that attempt finished),
executes each CMD through `bash -o pipefail -c`, writes `<attempt>/<n>.log` per command and
`<attempt>/result.json`, appends one line per command to the status file (default
`<run-dir>/status.md`), and exits non-zero if any command failed. Any non-zero exit counts,
including a negative code (killed by a signal) and a failure inside a pipeline.

`delta` extracts issue lines from two analyzer logs and prints the ones that are new in `current`.
An issue is identified by its file and its text: analyzers that print the file on its own line
(ESLint stylish) get the file attached to every issue below it, and repeated identical issues are
counted, so the same warning appearing in a second file or a second time is new. Exit 1 when there
is at least one new issue.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Dart/Flutter analyzer lines start with the severity; ESLint stylish lines start with line:col.
DEFAULT_ISSUE_REGEX = r"^\s*(info|warning|error)\b|^\s*\d+:\d+\s+(warning|error)\b"
FILE_HEADER_REGEX = re.compile(r"^[^\s•]+\.[A-Za-z0-9]+$")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def reserve_attempt(run_dir: Path, label: str) -> Path:
    """Create and return a fresh attempt directory; never reuses one that exists."""
    candidate = run_dir / label
    n = 1
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            n += 1
            candidate = run_dir / f"{label}-r{n}"


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    attempt = reserve_attempt(run_dir, args.label)
    status = Path(args.status) if args.status else run_dir / "status.md"
    results = []
    failed = False
    for index, cmd in enumerate(args.commands, start=1):
        log = attempt / f"{index}.log"
        start = dt.datetime.now()
        proc = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], cwd=args.cwd, capture_output=True, text=True)
        seconds = round((dt.datetime.now() - start).total_seconds(), 2)
        log.write_text(proc.stdout + proc.stderr)
        rc = proc.returncode
        outcome = "ok" if rc == 0 else (f"killed by signal {-rc}" if rc < 0 else f"exit {rc}")
        rel = f"{attempt.name}/{log.name}"
        results.append({"command": cmd, "rc": rc, "outcome": outcome, "seconds": seconds, "log": rel})
        failed = failed or rc != 0
        with status.open("a") as out:
            out.write(f"{_now()} - {attempt.name}: `{cmd}` {outcome}, {seconds}s; {rel}\n")
        print(f"[{attempt.name}] {outcome} {seconds}s :: {cmd}")
    (attempt / "result.json").write_text(json.dumps(results, indent=2))
    return 1 if failed else 0


def issues(text: str, regex: str) -> Counter:
    pattern = re.compile(regex)
    found: Counter = Counter()
    current_file = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if pattern.search(line):
            key = line.strip()
            if current_file and current_file not in key:
                key = f"{current_file}: {key}"
            found[key] += 1
        elif FILE_HEADER_REGEX.match(line):
            current_file = line
    return found


def delta(args: argparse.Namespace) -> int:
    baseline = issues(Path(args.baseline).read_text(), args.issue_regex)
    current = issues(Path(args.current).read_text(), args.issue_regex)
    new = current - baseline
    gone = baseline - current
    print(f"baseline={sum(baseline.values())} current={sum(current.values())} new={sum(new.values())} resolved={sum(gone.values())}")
    for line, count in sorted(new.items()):
        print(f"NEW{' x' + str(count) if count > 1 else ''}: {line}")
    for line, count in sorted(gone.items()):
        print(f"RESOLVED{' x' + str(count) if count > 1 else ''}: {line}")
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
