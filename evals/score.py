#!/usr/bin/env python3
"""Score an orchestrated run directory against the protocol.

Usage: score.py <run-dir> [--json]

Checks, per run: plan.md with a task table, reviewer-brief.md, status.md, final-report.md.
Checks, per task N found in plan.md: task-N.md with the required sections, review-N.md with a
VERDICT line, task-N.patch that is non-empty when the last verdict is PASS.

When the run directory holds plan.json, tasks come from it instead (their ids and spec paths,
relative to the run directory unless absolute), and the run also needs: plan.json passing
`plan.py check`, and execute-result.json. A task's review counts as present when review-<id>.md
has a VERDICT line, or its execute-result entry has a non-empty `reviews` list, or any entry of its
`history` does; every task the execute result reports as PASS needs a non-empty patch (the
result's `patch`, or patches/task-<id>.patch in the run directory). A task the execute result
reports as SKIPPED never ran, so it is exempt from the review and patch checks.

Prints a completeness score in [0, 1] and the failed checks. Exit 0 when the score is 1.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SPEC_SECTIONS = ["## Goal", "## Repo and location", "## Change", "## Constraints", "## Definition of done", "## Report"]
RUN_FILES = ["plan.md", "reviewer-brief.md", "status.md", "final-report.md"]
PLAN_PY = Path(__file__).resolve().parents[1] / "skill" / "scripts" / "plan.py"


def task_numbers(plan: str) -> list[int]:
    numbers = []
    for line in plan.splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|", line)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(set(numbers))


def spec_checks(checks: list, label: str, spec: Path) -> None:
    text = spec.read_text() if spec.is_file() else ""
    checks.append((f"{label}: spec present", spec.is_file()))
    for section in SPEC_SECTIONS:
        checks.append((f"{label}: spec has '{section}'", section in text))
    checks.append((f"{label}: spec names an absolute worktree path", bool(re.search(r"(^|\s)/\S+", text))))
    checks.append((f"{label}: spec has a gate command", "`" in text and "Definition of done" in text))


def verdicts(review: Path) -> list[str]:
    return re.findall(r"VERDICT:\s*(PASS|FAIL)", review.read_text() if review.is_file() else "")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def score_markdown(run_dir: Path, checks: list) -> list:
    plan = (run_dir / "plan.md").read_text() if (run_dir / "plan.md").is_file() else ""
    tasks = task_numbers(plan)
    checks.append(("plan: has at least one task row", bool(tasks)))
    for n in tasks:
        spec_checks(checks, f"task {n}", run_dir / f"task-{n}.md")
        found = verdicts(run_dir / f"review-{n}.md")
        checks.append((f"task {n}: review present with a verdict", bool(found)))
        patch = run_dir / f"task-{n}.patch"
        if found and found[-1] == "PASS":
            checks.append((f"task {n}: patch exported after PASS", patch.is_file() and patch.stat().st_size > 0))
    return tasks


def reviewed(run_dir: Path, tid: str, entry: dict) -> bool:
    """A VERDICT in review-<id>.md, or reviews in the result entry or in any of its history entries."""
    history = entry.get("history") if isinstance(entry.get("history"), list) else []
    return (bool(verdicts(run_dir / f"review-{tid}.md")) or bool(entry.get("reviews"))
            or any(isinstance(h, dict) and bool(h.get("reviews")) for h in history))


def score_plan_json(run_dir: Path, checks: list) -> list:
    plan_path = run_dir / "plan.json"
    proc = subprocess.run([sys.executable, str(PLAN_PY), "check", str(plan_path)], capture_output=True, text=True)
    checks.append(("run: plan.json passes plan.py check", proc.returncode == 0))
    plan = read_json(plan_path)
    raw_tasks = plan.get("tasks") if isinstance(plan, dict) else None
    tasks = [t for t in raw_tasks if isinstance(t, dict) and isinstance(t.get("id"), str)] if isinstance(raw_tasks, list) else []
    checks.append(("plan: has at least one task", bool(tasks)))
    result_path = run_dir / "execute-result.json"
    checks.append(("run: execute-result.json present", result_path.is_file()))
    result = read_json(result_path)
    results = result.get("tasks") if isinstance(result, dict) else None
    done = {r.get("id"): r for r in results if isinstance(r, dict)} if isinstance(results, list) else {}
    for t in tasks:
        tid = t["id"]
        spec = Path(t["spec"]) if isinstance(t.get("spec"), str) and t["spec"] else Path(f"task-{tid}.md")
        spec_checks(checks, f"task {tid}", spec if spec.is_absolute() else run_dir / spec)
        entry = done.get(tid, {})
        if entry.get("status") == "SKIPPED":
            continue
        checks.append((f"task {tid}: review present with a verdict", reviewed(run_dir, tid, entry)))
        if entry.get("status") == "PASS":
            candidates = [Path(entry["patch"])] if isinstance(entry.get("patch"), str) and entry["patch"] else []
            candidates.append(run_dir / "patches" / f"task-{tid}.patch")
            exported = any(p.is_file() and p.stat().st_size > 0 for p in candidates)
            checks.append((f"task {tid}: patch exported after PASS", exported))
    return [t["id"] for t in tasks]


def score(run_dir: Path) -> dict:
    checks: list[tuple[str, bool]] = []
    for name in RUN_FILES:
        checks.append((f"run: {name} present", (run_dir / name).is_file()))
    if (run_dir / "plan.json").is_file():
        tasks = score_plan_json(run_dir, checks)
    else:
        tasks = score_markdown(run_dir, checks)
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
