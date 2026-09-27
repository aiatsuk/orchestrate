import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

SCORE = REPO / "evals" / "score.py"
SPEC = REPO / "skill" / "references" / "spec-template.md"


def make_run(root: Path, complete: bool) -> Path:
    run = root / "run"; run.mkdir()
    (run / "plan.md").write_text("| # | Task |\n|---|---|\n| 1 | thing |\n| 2 | other |\n")
    (run / "reviewer-brief.md").write_text("brief")
    (run / "status.md").write_text("started\n")
    (run / "final-report.md").write_text("done\n")
    spec = SPEC.read_text().replace("<absolute path>", "/tmp/worktrees/run-thing")
    for n in (1, 2):
        (run / f"task-{n}.md").write_text(spec)
        (run / f"review-{n}.md").write_text("VERDICT: FAIL\nDEFECTS: 1. x\n\nVERDICT: PASS\n")
        (run / f"task-{n}.patch").write_text("diff --git a/x b/x\n")
    if not complete:
        (run / "task-2.patch").write_text("")
        (run / "review-1.md").write_text("no verdict here\n")
    return run


def make_plan_run(root: Path) -> Path:
    """A plan.json run: task a reviewed in review-a.md, task b reviewed only in the execute result."""
    run = root / "run"; run.mkdir()
    # a Markdown table with numbered rows that a plan.json run must ignore
    (run / "plan.md").write_text("| # | Task |\n|---|---|\n| 1 | thing |\n| 7 | other |\n")
    for name in ("reviewer-brief.md", "status.md", "final-report.md"):
        (run / name).write_text("x\n")
    spec = SPEC.read_text().replace("<absolute path>", "/tmp/worktrees/run-thing")
    (run / "specs").mkdir()
    (run / "specs" / "a.md").write_text(spec)
    (root / "b-spec.md").write_text(spec)
    tasks = [{"id": "a", "title": "a", "spec": "specs/a.md", "scope": ["src/a.txt"], "tier": 2, "depends_on": [], "gate": ["true"]},
             {"id": "b", "title": "b", "spec": str(root / "b-spec.md"), "scope": ["src/b.txt"], "tier": 2, "depends_on": ["a"], "gate": ["true"]}]
    plan = {"schema": "orchestrate-plan/v1", "goal": "g", "harness": "claude", "repo": str(root / "repo"), "base": "main",
            "run_dir": str(run), "worktree_parent": str(root / "wt"), "branch_prefix": "run", "tasks": tasks}
    (run / "plan.json").write_text(json.dumps(plan))
    (run / "review-a.md").write_text("VERDICT: PASS\n")
    (run / "patches").mkdir()
    (run / "patches" / "task-a.patch").write_text("diff --git a/src/a.txt b/src/a.txt\n")
    (root / "b.patch").write_text("diff --git a/src/b.txt b/src/b.txt\n")
    review = {"lens": "conformance", "model": "tier-3-model", "verdict": "PASS", "defects": 0}
    result = {"schema": "orchestrate-execute-result/v1", "version": "0.0.0", "tasks": [
        {"id": "a", "status": "PASS", "reason": "", "reviews": [], "patch": str(run / "moved" / "task-a.patch")},
        {"id": "b", "status": "PASS", "reason": "", "reviews": [review], "patch": str(root / "b.patch")}]}
    (run / "execute-result.json").write_text(json.dumps(result))
    return run


def score_json(run: Path) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(SCORE), str(run), "--json"], capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


class PlanJsonScoreTests(unittest.TestCase):
    def test_complete_plan_json_run_scores_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = score_json(make_plan_run(Path(tmp)))
            self.assertEqual(rc, 0, out["failed"])
            self.assertEqual(out["tasks"], ["a", "b"])
            # 4 run files, plan check, a task, execute result, then per task 9 spec checks, the review and the patch
            self.assertEqual(out["checks"], 4 + 3 + 2 * 11)

    def test_plan_json_checks_fail_individually(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_plan_run(Path(tmp))
            plan = json.loads((run / "plan.json").read_text())
            plan["tasks"][1]["scope"] = ["src/*.txt"]
            (run / "plan.json").write_text(json.dumps(plan))
            result = json.loads((run / "execute-result.json").read_text())
            result["tasks"][1]["reviews"] = []
            (run / "execute-result.json").write_text(json.dumps(result))
            (run / "patches" / "task-a.patch").write_text("")
            (run / "specs" / "a.md").unlink()
            rc, out = score_json(run)
            self.assertEqual(rc, 1)
            self.assertEqual(sorted(out["failed"]), sorted([
                "run: plan.json passes plan.py check", "task a: patch exported after PASS", "task b: review present with a verdict",
                "task a: spec present", *[f"task a: spec has '{s}'" for s in ("## Goal", "## Repo and location", "## Change",
                                                                               "## Constraints", "## Definition of done", "## Report")],
                "task a: spec names an absolute worktree path", "task a: spec has a gate command"]))

    def test_missing_execute_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_plan_run(Path(tmp))
            (run / "execute-result.json").unlink()
            rc, out = score_json(run)
            self.assertEqual(rc, 1)
            # task b was reviewed only in the execute result, so its review is missing too
            self.assertEqual(out["failed"], ["run: execute-result.json present", "task b: review present with a verdict"])


class ResultEntryScoreTests(unittest.TestCase):
    """SKIPPED tasks and reviews recorded only in a result's history."""

    def run_with(self, root: Path, entry_b: dict) -> tuple[int, dict]:
        run = make_plan_run(root)
        result = json.loads((run / "execute-result.json").read_text())
        result["tasks"][1] = {"id": "b", **entry_b}
        (run / "execute-result.json").write_text(json.dumps(result))
        return score_json(run)

    def test_skipped_task_needs_no_review_or_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self.run_with(Path(tmp), {"status": "SKIPPED", "reason": "dependency did not pass: a"})
            self.assertEqual(rc, 0, out["failed"])
            self.assertEqual(out["checks"], 4 + 3 + 11 + 9)  # task b keeps its 9 spec checks only

    def test_blocked_task_still_needs_a_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self.run_with(Path(tmp), {"status": "BLOCKED", "reason": "x"})
            self.assertEqual(out["failed"], ["task b: review present with a verdict"])

    def test_reviews_in_history_count(self):
        review = {"lens": "conformance", "model": "m", "verdict": "FAIL", "defects": 1}
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self.run_with(Path(tmp), {"status": "ESCALATE", "reason": "x", "reviews": [],
                                                "history": [{"level": 0, "round": 0, "reviews": []},
                                                            {"level": 0, "round": 1, "reviews": [review]}]})
            self.assertEqual(rc, 0, out["failed"])
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self.run_with(Path(tmp), {"status": "ESCALATE", "reason": "x",
                                                "history": [{"level": 0, "round": 0, "gate_exit": 1, "reviews": []}]})
            self.assertEqual(out["failed"], ["task b: review present with a verdict"])


class ScoreTests(unittest.TestCase):
    def test_complete_run_scores_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(Path(tmp), complete=True)
            proc = subprocess.run([sys.executable, str(SCORE), str(run)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("score 1.0", proc.stdout)

    def test_incomplete_run_lists_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(Path(tmp), complete=False)
            proc = subprocess.run([sys.executable, str(SCORE), str(run), "--json"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("task 1: review present with a verdict", proc.stdout)
            self.assertIn("task 2: patch exported after PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
