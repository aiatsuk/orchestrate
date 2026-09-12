#!/usr/bin/env python3
"""Score an orchestrated run directory against the protocol.

Usage: score.py <run-dir> [--json]

Checks, per run: plan.md with a task table, reviewer-brief.md, status.md, final-report.md.
Checks, per task N found in plan.md: task-N.md with the required sections, review-N.md with a
VERDICT line, task-N.patch that is non-empty when the last verdict is PASS.
Prints a completeness score in [0, 1] and the failed checks. Exit 0 when the score is 1.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPEC_SECTIONS = ["## Goal", "## Repo and location", "## Change", "## Constraints", "## Definition of done", "## Report"]
RUN_FILES = ["plan.md", "reviewer-brief.md", "status.md", "final-report.md"]


def task_numbers(plan: str) -> list[int]:
    numbers = []
    for line in plan.splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|", line)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(set(numbers))


def score(run_dir: Path) -> dict:
    checks: list[tuple[str, bool]] = []
    for name in RUN_FILES:
        checks.append((f"run: {name} present", (run_dir / name).is_file()))
    plan = (run_dir / "plan.md").read_text() if (run_dir / "plan.md").is_file() else ""
    tasks = task_numbers(plan)
    checks.append(("plan: has at least one task row", bool(tasks)))
    for n in tasks:
        spec = run_dir / f"task-{n}.md"
        text = spec.read_text() if spec.is_file() else ""
        checks.append((f"task {n}: spec present", spec.is_file()))
        for section in SPEC_SECTIONS:
            checks.append((f"task {n}: spec has '{section}'", section in text))
        checks.append((f"task {n}: spec names an absolute worktree path", bool(re.search(r"(^|\s)/\S+", text))))
        checks.append((f"task {n}: spec has a gate command", "`" in text and "Definition of done" in text))
        review = run_dir / f"review-{n}.md"
        rtext = review.read_text() if review.is_file() else ""
        verdicts = re.findall(r"VERDICT:\s*(PASS|FAIL)", rtext)
        checks.append((f"task {n}: review present with a verdict", bool(verdicts)))
        patch = run_dir / f"task-{n}.patch"
        if verdicts and verdicts[-1] == "PASS":
            checks.append((f"task {n}: patch exported after PASS", patch.is_file() and patch.stat().st_size > 0))
    passed = sum(1 for _, ok in checks if ok)
    return {"run_dir": str(run_dir), "tasks": tasks, "checks": len(checks), "passed": passed,
            "score": round(passed / len(checks), 3) if checks else 0.0,
            "failed": [name for name, ok in checks if not ok]}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 2
    result = score(Path(argv[0]).expanduser().resolve())
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"score {result['score']} ({result['passed']}/{result['checks']} checks, tasks {result['tasks']})")
        for name in result["failed"]:
            print(f"FAIL: {name}")
    return 0 if result["score"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
