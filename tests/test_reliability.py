"""Regressions reported by the 2026-09-13 external review: gate return codes, log overwrites,
analyzer line formats, dependent and conflicting patch sequences, stray files, cleanup safety."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, SCRIPTS, git, make_repo

GATE = SCRIPTS / "gate.py"
INTEGRATE = SCRIPTS / "integrate.sh"
PREPARE = SCRIPTS / "prepare_worktrees.sh"
INSTALL = REPO / "install.sh"


def gate(*args):
    return subprocess.run([sys.executable, str(GATE), *args], capture_output=True, text=True)


def integrate(*args):
    return subprocess.run([str(INTEGRATE), *args], capture_output=True, text=True)


def make_patch(repo: Path, branch: str, edits: dict, out: Path) -> Path:
    wt = repo.parent / branch
    git("worktree", "add", "-q", "-b", branch, str(wt), "main", cwd=repo)
    for rel, content in edits.items():
        (wt / rel).write_text(content)
    git("add", "-A", cwd=wt)
    integrate("export", str(wt), str(out))
    return wt


class GateReliabilityTests(unittest.TestCase):
    def test_signal_killed_command_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = gate("run", "--run-dir", tmp, "--label", "sig", "--", "kill -TERM $$")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("killed by signal 15", proc.stdout)
            self.assertIn("killed by signal 15", (Path(tmp) / "status.md").read_text())

    def test_failure_inside_a_pipeline_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = gate("run", "--run-dir", tmp, "--label", "pipe", "--", "false | cat", "sh -c 'exit 3' | tee /dev/null")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            results = json.loads((Path(tmp) / "pipe" / "result.json").read_text())
            self.assertEqual([r["rc"] for r in results], [1, 3])

    def test_rerun_and_interrupted_attempts_never_overwrite_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate("run", "--run-dir", tmp, "--label", "t", "--", "echo first")
            # an interrupted attempt: a log exists but no result.json
            (Path(tmp) / "t-r2").mkdir(); (Path(tmp) / "t-r2" / "1.log").write_text("partial\n")
            gate("run", "--run-dir", tmp, "--label", "t", "--", "echo third")
            self.assertEqual((Path(tmp) / "t" / "1.log").read_text(), "first\n")
            self.assertEqual((Path(tmp) / "t-r2" / "1.log").read_text(), "partial\n")
            self.assertEqual((Path(tmp) / "t-r3" / "1.log").read_text(), "third\n")
            self.assertTrue((Path(tmp) / "t-r3" / "result.json").is_file())

    def test_delta_sees_eslint_style_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "b.log"; c = Path(tmp) / "c.log"
            b.write_text("src/a.js\n  12:3  warning  Unexpected console statement  no-console\n")
            c.write_text(b.read_text() + "  40:1  error  'x' is not defined  no-undef\n")
            proc = gate("delta", "--baseline", str(b), "--current", str(c))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("NEW: src/a.js: 40:1  error", proc.stdout)

    def test_delta_counts_the_same_issue_in_another_file_or_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "b.log"; c = Path(tmp) / "c.log"
            warning = "  12:3  warning  Unexpected console statement  no-console\n"
            b.write_text("src/a.js\n" + warning)
            c.write_text("src/a.js\n" + warning + "src/b.js\n" + warning)
            proc = gate("delta", "--baseline", str(b), "--current", str(c))
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("new=1", proc.stdout); self.assertIn("NEW: src/b.js:", proc.stdout)
            c.write_text("src/a.js\n" + warning + warning)
            proc = gate("delta", "--baseline", str(b), "--current", str(c))
            self.assertEqual(proc.returncode, 1); self.assertIn("new=1", proc.stdout)


class IntegrateReliabilityTests(unittest.TestCase):
    def test_dependent_patches_apply_in_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            a = Path(tmp) / "a.patch"
            make_patch(repo, "task-a", {"a.txt": "alpha-one\n"}, a)
            # task b is scaffolded on task a's patch, so its own patch is alpha-one -> alpha-two
            wt_b = Path(tmp) / "task-b"
            git("worktree", "add", "-q", "-b", "task-b", str(wt_b), "main", cwd=repo)
            git("apply", "--index", str(a), cwd=wt_b)
            git("commit", "-q", "-m", "scaffold", cwd=wt_b)
            (wt_b / "a.txt").write_text("alpha-two\n")
            git("add", "-A", cwd=wt_b)
            b = Path(tmp) / "b.patch"
            integrate("export", str(wt_b), str(b))
            integ = Path(tmp) / "integ"
            git("worktree", "add", "-q", "-b", "integration", str(integ), "main", cwd=repo)
            self.assertNotEqual(integrate("apply", str(integ), str(b)).returncode, 0, "b alone must not apply on the base")
            proc = integrate("apply", str(integ), str(a), str(b))
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((integ / "a.txt").read_text(), "alpha-two\n")
            self.assertEqual(git("diff", "--cached", "--name-only", cwd=integ).split(), ["a.txt"])

    def test_conflicting_patches_leave_integration_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            a = Path(tmp) / "a.patch"; b = Path(tmp) / "b.patch"
            make_patch(repo, "task-a", {"a.txt": "alpha from a\n"}, a)
            make_patch(repo, "task-b", {"a.txt": "alpha from b\n"}, b)
            integ = Path(tmp) / "integ"
            git("worktree", "add", "-q", "-b", "integration", str(integ), "main", cwd=repo)
            (integ / "stray.txt").write_text("not mine\n")
            proc = integrate("apply", str(integ), str(a), str(b))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("left unchanged", proc.stderr)
            self.assertEqual((integ / "a.txt").read_text(), "alpha\n")
            self.assertEqual(git("diff", "--cached", "--name-only", cwd=integ), "")
            self.assertEqual(git("status", "--porcelain", cwd=integ).strip(), "?? stray.txt")

    def test_apply_stages_only_patch_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            a = Path(tmp) / "a.patch"
            make_patch(repo, "task-a", {"b.txt": "beta changed\n"}, a)
            integ = Path(tmp) / "integ"
            git("worktree", "add", "-q", "-b", "integration", str(integ), "main", cwd=repo)
            (integ / "notes.log").write_text("stray\n")
            self.assertEqual(integrate("apply", str(integ), str(a)).returncode, 0)
            self.assertEqual(git("diff", "--cached", "--name-only", cwd=integ).split(), ["b.txt"])
            self.assertIn("?? notes.log", git("status", "--porcelain", cwd=integ))

    def test_verify_clean_detects_untracked_unstaged_mismatch_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            patch = Path(tmp) / "a.patch"
            wt = make_patch(repo, "task-a", {"a.txt": "alpha changed\n"}, patch)
            self.assertEqual(integrate("verify-clean", str(wt), str(patch), "--scope", "a.txt").returncode, 0)
            self.assertEqual(integrate("verify-clean", str(wt), str(patch), "--scope", "b.txt").returncode, 1)
            (wt / ".deps.log").write_text("log\n")
            proc = integrate("verify-clean", str(wt), str(patch))
            self.assertEqual(proc.returncode, 1); self.assertIn("untracked files present", proc.stdout)
            (wt / ".deps.log").unlink()
            (wt / "b.txt").write_text("unstaged edit\n")
            proc = integrate("verify-clean", str(wt), str(patch))
            self.assertEqual(proc.returncode, 1); self.assertIn("unstaged changes present", proc.stdout)
            git("checkout", "--", "b.txt", cwd=wt)
            (wt / "a.txt").write_text("alpha changed again\n"); git("add", "-A", cwd=wt)
            proc = integrate("verify-clean", str(wt), str(patch))
            self.assertEqual(proc.returncode, 1); self.assertIn("staged diff differs", proc.stdout)


    def test_verify_clean_scope_sees_the_old_path_of_a_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            wt = Path(tmp) / "task"
            git("worktree", "add", "-q", "-b", "task", str(wt), "main", cwd=repo)
            (wt / "allowed").mkdir()
            git("mv", "a.txt", "allowed/a.txt", cwd=wt)
            patch = Path(tmp) / "t.patch"
            integrate("export", str(wt), str(patch))
            proc = integrate("verify-clean", str(wt), str(patch), "--scope", "allowed/")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("a.txt", proc.stdout)
            self.assertEqual(integrate("verify-clean", str(wt), str(patch), "--scope", "allowed/", "--scope", "a.txt").returncode, 0)

    def test_apply_refuses_to_overwrite_an_ignored_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            (repo / ".gitignore").write_text("build.log\n"); git("add", "-A", cwd=repo); git("commit", "-q", "-m", "ignore", cwd=repo)
            patch = Path(tmp) / "a.patch"
            wt = Path(tmp) / "task-a"
            git("worktree", "add", "-q", "-b", "task-a", str(wt), "main", cwd=repo)
            (wt / "build.log").write_text("tracked now\n")
            git("add", "-f", "build.log", cwd=wt)  # force: the path is ignored on main
            integrate("export", str(wt), str(patch))
            integ = Path(tmp) / "integ"
            git("worktree", "add", "-q", "-b", "integration", str(integ), "main", cwd=repo)
            (integ / "build.log").write_text("local ignored content\n")
            proc = integrate("apply", str(integ), str(patch))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("would overwrite", proc.stderr)
            self.assertEqual((integ / "build.log").read_text(), "local ignored content\n")
            self.assertEqual(git("diff", "--cached", "--name-only", cwd=integ), "")


class PrepareAndInstallReliabilityTests(unittest.TestCase):
    def test_deps_log_goes_to_log_dir_outside_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            logs = Path(tmp) / "logs"
            proc = subprocess.run([str(PREPARE), "--repo", str(repo), "--base", "main", "--parent", tmp + "/w",
                                   "--prefix", "run", "--deps", "true", "--log-dir", str(logs), "one"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((logs / "run-one.deps.log").is_file())
            self.assertEqual(git("status", "--porcelain", cwd=Path(tmp) / "w" / "run-one"), "")

    def test_uninstall_leaves_foreign_symlinks_alone(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = Path(home) / ".claude" / "skills"; foreign.mkdir(parents=True)
            # a sibling path that merely starts with the repo's skill path must not count as ours
            target = Path(str(REPO / "skill") + "-other")
            (foreign / "orchestrate").symlink_to(target)
            env = {**os.environ, "HOME": home}
            proc = subprocess.run([str(INSTALL), "--uninstall"], capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((foreign / "orchestrate").is_symlink())
            self.assertIn("not ours", proc.stdout)


if __name__ == "__main__":
    unittest.main()
