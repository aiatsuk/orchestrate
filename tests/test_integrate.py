import hashlib
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


def export_patch(repo: Path, name: str, edits: dict, out: Path, on=(), deletes=()) -> Path:
    """Stage `edits` (and deletions) in a new worktree, on top of the patches in `on`, and export them."""
    wt = repo.parent / name
    git("worktree", "add", "-q", "-b", f"run/{name}", str(wt), "main", cwd=repo)
    for p in on:
        git("apply", "--index", str(p), cwd=wt)
    if on:
        git("commit", "-q", "-m", "base", cwd=wt)
    for rel, content in edits.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(content)
    for rel in deletes:
        (wt / rel).unlink()
    git("add", "-A", cwd=wt)
    sh("export", str(wt), str(out))
    return out


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.repo = make_repo(self.root / "repo")
        # the prepare_worktrees.sh layout: <parent>/<prefix with / as ->-<slug> on branch <prefix>/<slug>
        self.wt = self.root / "wt" / "run-task"
        git("worktree", "add", "-q", "-b", "run/task", str(self.wt), "main", cwd=self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dependent_patches_become_one_commit_with_the_fixed_identity(self):
        a = export_patch(self.repo, "pa", {"a.txt": "one\n", "dir/new.txt": "new\n"}, self.root / "a.patch")
        b = export_patch(self.repo, "pb", {"a.txt": "two\n"}, self.root / "b.patch", on=[a], deletes=["b.txt"])
        base = git("rev-parse", "HEAD", cwd=self.wt).strip()
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        head = git("rev-parse", "HEAD", cwd=self.wt).strip()
        self.assertEqual(proc.stdout.splitlines()[-1], f"head {head}")
        self.assertEqual(git("log", "-1", "--format=%s|%an|%ae|%cn|%ce|%P", cwd=self.wt).strip(),
                         f"scaffolding (temporary)|orchestrate|orchestrate@localhost|orchestrate|orchestrate@localhost|{base}")
        self.assertEqual(sorted(git("show", "--name-only", "--format=", "HEAD", cwd=self.wt).split()), ["a.txt", "b.txt", "dir/new.txt"])
        self.assertEqual(git("rev-parse", "run/task", cwd=self.wt).strip(), head, "the branch itself moves")
        self.assertEqual(git("diff", "--cached", "--name-only", cwd=self.wt), "")
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual((self.wt / "a.txt").read_text(), "two\n")
        self.assertFalse((self.wt / "b.txt").exists())

    def test_message_records_the_patch_hashes_in_order(self):
        a = export_patch(self.repo, "pa", {"a.txt": "one\n"}, self.root / "a.patch")
        b = export_patch(self.repo, "pb", {"b.txt": "beta two\n"}, self.root / "b.patch")
        self.assertEqual(sh("scaffold", str(self.wt), str(b), str(a)).returncode, 0)
        sums = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (b, a)]
        self.assertEqual(git("log", "-1", "--format=%B", cwd=self.wt).rstrip("\n"),
                         f"scaffolding (temporary)\n\npatches: {sums[0]} {sums[1]}")

    def test_rerun_with_the_same_patches_touches_nothing_even_when_dirty(self):
        a = export_patch(self.repo, "pa", {"a.txt": "one\n"}, self.root / "a.patch")
        b = export_patch(self.repo, "pb", {"a.txt": "two\n"}, self.root / "b.patch", on=[a])
        self.assertEqual(sh("scaffold", str(self.wt), str(a), str(b)).returncode, 0)
        head = git("rev-parse", "HEAD", cwd=self.wt).strip()
        # the task has started: a staged change, an unstaged change and a scratch file
        (self.wt / "a.txt").write_text("task change\n"); git("add", "a.txt", cwd=self.wt)
        (self.wt / "b.txt").write_text("unstaged\n")
        (self.wt / "scratch.txt").write_text("x\n")
        before = git("status", "--porcelain", cwd=self.wt)
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"head {head} (already scaffolded)")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt).strip(), head)
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), before)
        self.assertEqual((self.wt / "a.txt").read_text(), "task change\n")

    def scaffolded_on_changed_patch(self):
        """Scaffold on patches a and b, then change b's content as a reworked dependency would."""
        a = export_patch(self.repo, "pa", {"a.txt": "one\n"}, self.root / "a.patch")
        b = export_patch(self.repo, "pb", {"b.txt": "beta two\n"}, self.root / "b.patch")
        self.assertEqual(sh("scaffold", str(self.wt), str(a), str(b)).returncode, 0)
        stale = git("rev-parse", "HEAD", cwd=self.wt).strip()
        parent = git("rev-parse", "HEAD^", cwd=self.wt).strip()
        b.write_text(b.read_text().replace("beta two", "beta three"))
        return a, b, stale, parent

    def assertStaleRefusal(self, proc, stale, parent):
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"HEAD {stale} is a scaffolding commit for other patches", proc.stderr)
        self.assertIn("never stacked", proc.stderr)
        self.assertIn("preserve the task's own work", proc.stderr)
        self.assertIn("recreate the worktree", proc.stderr)
        self.assertIn(f"git -C {self.wt} reset --hard {parent}", proc.stderr)
        self.assertIn("leaves untracked\nfiles behind; remove them too", proc.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt).strip(), stale, "nothing is stacked")

    def test_changed_dependency_patch_on_a_clean_scaffold_is_refused_not_stacked(self):
        a, b, stale, parent = self.scaffolded_on_changed_patch()
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertStaleRefusal(proc, stale, parent)
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual((self.wt / "b.txt").read_text(), "beta two\n")
        for patches in ([a], [b, a]):  # a subset or another order is another patch set too
            with self.subTest(patches=[p.name for p in patches]):
                self.assertStaleRefusal(sh("scaffold", str(self.wt), *map(str, patches)), stale, parent)
        # the reset recovery the message names works, once untracked files are removed as it says
        (self.wt / "scratch.txt").write_text("x\n")
        git("reset", "-q", "--hard", parent, cwd=self.wt)
        self.assertIn("untracked files present", sh("scaffold", str(self.wt), str(a), str(b)).stderr)
        git("clean", "-q", "-fd", cwd=self.wt)
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(git("rev-parse", "HEAD^", cwd=self.wt).strip(), parent)
        self.assertEqual((self.wt / "b.txt").read_text(), "beta three\n")

    def test_changed_dependency_patch_with_staged_work_gets_the_same_refusal(self):
        a, b, stale, parent = self.scaffolded_on_changed_patch()
        clean = sh("scaffold", str(self.wt), str(a), str(b))
        (self.wt / "a.txt").write_text("the task's own work\n")
        git("add", "a.txt", cwd=self.wt)
        (self.wt / "scratch.txt").write_text("x\n")
        before = git("status", "--porcelain", cwd=self.wt)
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertStaleRefusal(proc, stale, parent)
        self.assertEqual(proc.stderr, clean.stderr)
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), before)
        self.assertEqual((self.wt / "a.txt").read_text(), "the task's own work\n")

    def recreate_commands(self, stderr: str) -> list[str]:
        return [line.strip() for line in stderr.splitlines() if line.startswith(("  git -C ", f"  {SCRIPTS}/"))]

    def test_message_names_the_exact_recreate_commands_and_they_work(self):
        a, b, stale, parent = self.scaffolded_on_changed_patch()
        (self.wt / "a.txt").write_text("the task's own work\n")
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(self.recreate_commands(proc.stderr), [
            f"git -C {self.repo} worktree remove --force {self.wt} && git -C {self.repo} branch -D run/task",
            f"{SCRIPTS}/prepare_worktrees.sh --repo {self.repo} --base {parent} --parent {self.wt.parent} --prefix run task"])
        self.assertIn(f"integrate.sh export {self.wt} <file>", proc.stderr)
        for command in self.recreate_commands(proc.stderr):
            done = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt).strip(), parent)
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(git("rev-parse", "HEAD^", cwd=self.wt).strip(), parent)
        self.assertEqual((self.wt / "b.txt").read_text(), "beta three\n")

    def test_worktree_outside_the_prepare_layout_gets_a_worktree_add_command(self):
        self.wt = self.root / "elsewhere"
        git("worktree", "add", "-q", "-b", "run/other", str(self.wt), "main", cwd=self.repo)
        a, b, stale, parent = self.scaffolded_on_changed_patch()
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertStaleRefusal(proc, stale, parent)
        self.assertEqual(self.recreate_commands(proc.stderr), [
            f"git -C {self.repo} worktree remove --force {self.wt} && git -C {self.repo} branch -D run/other",
            f"git -C {self.repo} worktree add -b run/other {self.wt} {parent}"])
        for command in self.recreate_commands(proc.stderr):
            self.assertEqual(subprocess.run(["bash", "-c", command], capture_output=True).returncode, 0)
        self.assertEqual(sh("scaffold", str(self.wt), str(a), str(b)).returncode, 0)

    def test_relative_patch_path_is_read_from_the_caller_directory(self):
        export_patch(self.repo, "pa", {"a.txt": "one\n"}, self.root / "a.patch")
        proc = subprocess.run([str(INTEGRATE), "scaffold", str(self.wt), "a.patch"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.wt / "a.txt").read_text(), "one\n")

    def test_refuses_staged_unstaged_or_untracked_changes(self):
        a = export_patch(self.repo, "pa", {"a.txt": "one\n"}, self.root / "a.patch")
        head = git("rev-parse", "HEAD", cwd=self.wt).strip()
        cases = {"staged": lambda: ((self.wt / "b.txt").write_text("x\n"), git("add", "b.txt", cwd=self.wt)),
                 "unstaged": lambda: (self.wt / "b.txt").write_text("x\n"),
                 "untracked": lambda: (self.wt / "c.txt").write_text("x\n")}
        for kind, dirty in cases.items():
            with self.subTest(kind=kind):
                dirty()
                proc = sh("scaffold", str(self.wt), str(a))
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"{kind} changes present" if kind != "untracked" else "untracked files present", proc.stderr)
                self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt).strip(), head)
                self.assertEqual((self.wt / "a.txt").read_text(), "alpha\n")
                git("reset", "-q", "--hard", cwd=self.wt)
                git("clean", "-qfd", cwd=self.wt)

    def test_failure_restores_every_touched_path_and_names_the_patch(self):
        a = export_patch(self.repo, "pa", {"a.txt": "from a\n", "new-a.txt": "a\n"}, self.root / "a.patch", deletes=["b.txt"])
        b = export_patch(self.repo, "pb", {"a.txt": "from b\n", "new-b.txt": "b\n"}, self.root / "b.patch")
        head = git("rev-parse", "HEAD", cwd=self.wt).strip()
        proc = sh("scaffold", str(self.wt), str(a), str(b))
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"patch does not apply in sequence, worktree restored to HEAD: {b}", proc.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt).strip(), head)
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")
        self.assertEqual(git("ls-files", "--unmerged", cwd=self.wt), "")
        self.assertEqual((self.wt / "a.txt").read_text(), "alpha\n")
        self.assertEqual((self.wt / "b.txt").read_text(), "beta\n")
        self.assertFalse((self.wt / "new-a.txt").exists())
        self.assertFalse((self.wt / "new-b.txt").exists())

    def test_missing_or_empty_patch_is_refused_before_anything_changes(self):
        empty = self.root / "empty.patch"
        empty.write_text("")
        proc = sh("scaffold", str(self.wt), str(empty))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("empty or missing patch", proc.stderr)

    def test_usage_lists_every_subcommand(self):
        proc = sh()
        self.assertEqual(proc.returncode, 2)
        for name in ("export", "apply", "verify-clean", "same-tree", "scaffold"):
            self.assertIn(f"integrate.sh {name} ", proc.stdout)
        self.assertIn("staged diff is empty", proc.stdout)


if __name__ == "__main__":
    unittest.main()
