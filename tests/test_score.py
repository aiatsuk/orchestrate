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
