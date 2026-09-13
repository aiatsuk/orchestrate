import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import SCRIPTS, git, make_repo

PREPARE = SCRIPTS / "prepare_worktrees.sh"


class PrepareWorktreesTests(unittest.TestCase):
    def test_creates_worktrees_and_runs_deps(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            parent = Path(tmp) / "wts"
            proc = subprocess.run([str(PREPARE), "--repo", str(repo), "--base", "main", "--parent", str(parent),
                                   "--prefix", "feature/run", "--deps", "touch deps-ok", "one", "two"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for slug in ("one", "two"):
                wt = parent / f"feature-run-{slug}"
                self.assertTrue((wt / "deps-ok").exists(), str(wt))
                self.assertFalse((wt / ".deps.log").exists(), "deps log must not be inside the worktree")
                self.assertTrue(Path(str(wt) + ".deps.log").is_file())
                self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt).strip(), f"feature/run/{slug}")

    def test_deps_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            proc = subprocess.run([str(PREPARE), "--repo", str(repo), "--base", "main", "--parent", tmp + "/w",
                                   "--prefix", "run", "--deps", "exit 7", "one"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("deps failed", proc.stderr)


if __name__ == "__main__":
    unittest.main()
