import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import SCRIPTS, git, make_repo

INTEGRATE = SCRIPTS / "integrate.sh"


def sh(*args):
    return subprocess.run([str(INTEGRATE), *args], capture_output=True, text=True)


class IntegrateTests(unittest.TestCase):
    def test_export_apply_and_same_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            task = Path(tmp) / "task"; integ = Path(tmp) / "integ"; other = Path(tmp) / "other"
            git("worktree", "add", "-q", "-b", "run/task", str(task), "main", cwd=repo)
            git("worktree", "add", "-q", "-b", "run/integration", str(integ), "main", cwd=repo)
            git("worktree", "add", "-q", "-b", "run/other", str(other), "main", cwd=repo)
            (task / "a.txt").write_text("alpha changed\n")
            (task / "new.txt").write_text("new file\n")
            git("add", "-A", cwd=task)
            patch = Path(tmp) / "task.patch"
            self.assertEqual(sh("export", str(task), str(patch)).returncode, 0)
            self.assertIn("diff --git a/new.txt", patch.read_text())
            proc = sh("apply", str(integ), str(patch))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((integ / "a.txt").read_text(), "alpha changed\n")
            self.assertEqual(sh("same-tree", str(task), str(integ)).returncode, 0)
            self.assertEqual(sh("same-tree", str(task), str(other)).returncode, 1)

    def test_apply_refuses_when_any_patch_fails_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            integ = Path(tmp) / "integ"
            git("worktree", "add", "-q", "-b", "run/integration", str(integ), "main", cwd=repo)
            bad = Path(tmp) / "bad.patch"
            bad.write_text("--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-x\n+y\n")
            proc = sh("apply", str(integ), str(bad))
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(git("status", "--porcelain", cwd=integ), "")


if __name__ == "__main__":
    unittest.main()
