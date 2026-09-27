"""task.py: gate, scaffold and finish each print one schema-valid JSON object."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, SCRIPTS, git, make_repo

sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import plan as planmod  # noqa: E402

TASK = SCRIPTS / "task.py"
INTEGRATE = SCRIPTS / "integrate.sh"
SCHEMAS = REPO / "skill" / "schemas"


def run_task(*args):
    proc = subprocess.run([sys.executable, str(TASK), *map(str, args)], capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)  # fails unless stdout is exactly one JSON document


def schema(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


class TaskCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.repo = make_repo(self.root / "repo")

    def tearDown(self):
        self._tmp.cleanup()

    def worktree(self, name: str) -> Path:
        wt = self.root / name
        git("worktree", "add", "-q", "-b", f"run/{name}", str(wt), "main", cwd=self.repo)
        return wt

    def patch_from(self, name: str, edits: dict, base_patches=(), deletes=()) -> Path:
        """Export a patch made on `name`'s worktree, optionally on top of earlier patches."""
        wt = self.worktree(name)
        for p in base_patches:
            git("apply", "--index", str(p), cwd=wt)
        if base_patches:
            git("commit", "-q", "-m", "base", cwd=wt)
        for rel, content in edits.items():
            (wt / rel).write_text(content)
        for rel in deletes:
            (wt / rel).unlink()
        git("add", "-A", cwd=wt)
        out = self.root / f"{name}.patch"
        subprocess.run([str(INTEGRATE), "export", str(wt), str(out)], check=True, capture_output=True)
        return out

    def assertValid(self, obj, name):
        self.assertEqual(planmod.validate(obj, schema(name)), [])


class GateTests(TaskCase):
    def gate(self, wt, *commands, label="task-a"):
        return run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", label, "--worktree", wt, "--", *commands)

    def test_pass_prints_results_tail_tree_and_clean_state(self):
        wt = self.worktree("a")
        (wt / "a.txt").write_text("alpha changed\n")
        git("add", "-A", cwd=wt)
        rc, out = self.gate(wt, "pwd > /dev/null", "seq 1 7; test -f a.txt")
        self.assertEqual(rc, 0)
        self.assertValid(out, "gate")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["attempt_dir"], str(self.root / "run" / "gates" / "task-a"))
        self.assertEqual(json.loads(Path(out["attempt_dir"], "result.json").read_text()), out["results"])
        self.assertEqual([r["rc"] for r in out["results"]], [0, 0])
        self.assertEqual(out["tail"], "3\n4\n5\n6\n7")
        self.assertEqual(out["tree"], git("write-tree", cwd=wt).strip())
        self.assertEqual(out["head"], git("rev-parse", "HEAD", cwd=wt).strip())
        self.assertNotEqual(out["tree"], git("rev-parse", "HEAD^{tree}", cwd=wt).strip())
        self.assertFalse(out["dirty"])
        self.assertIn("task-a: `pwd > /dev/null` ok", (self.root / "run" / "gates" / "status.md").read_text())

    def test_commands_run_inside_the_worktree(self):
        wt = self.worktree("a")
        rc, out = self.gate(wt, "pwd")
        self.assertEqual(Path(out["tail"]).resolve(), wt)

    def test_fail_keeps_the_last_forty_lines_of_each_failing_command(self):
        wt = self.worktree("a")
        rc, out = self.gate(wt, "echo passing-output", "seq 1 50; exit 3", "echo second-failure >&2; false")
        self.assertEqual(rc, 1)
        self.assertValid(out, "gate")
        self.assertEqual(out["exit_code"], 1)
        lines = out["tail"].splitlines()
        self.assertEqual(lines[0], "$ seq 1 50; exit 3  (exit 3)")
        self.assertEqual(lines[1:41], [str(n) for n in range(11, 51)])
        self.assertEqual(lines[41:], ["$ echo second-failure >&2; false  (exit 1)", "second-failure"])
        self.assertNotIn("passing-output", out["tail"])

    def test_a_second_run_gets_a_new_attempt(self):
        wt = self.worktree("a")
        _, first = self.gate(wt, "true")
        _, second = self.gate(wt, "true")
        self.assertEqual(Path(second["attempt_dir"]).name, "task-a-r2")
        self.assertNotEqual(first["attempt_dir"], second["attempt_dir"])

    def test_dirty_flag_for_untracked_and_unstaged_but_not_staged(self):
        wt = self.worktree("a")
        (wt / "a.txt").write_text("staged\n")
        git("add", "-A", cwd=wt)
        self.assertFalse(self.gate(wt, "true", label="staged")[1]["dirty"])
        (wt / "scratch.log").write_text("x\n")
        self.assertTrue(self.gate(wt, "true", label="untracked")[1]["dirty"])
        (wt / "scratch.log").unlink()
        (wt / "b.txt").write_text("unstaged\n")
        self.assertTrue(self.gate(wt, "true", label="unstaged")[1]["dirty"])

    def test_state_separates_unstaged_untracked_and_outside_scope(self):
        wt = self.worktree("a")
        (wt / "a.txt").write_text("staged\n")
        (wt / "notes.txt").write_text("staged outside\n")
        git("add", "-A", cwd=wt)
        rc, out = run_task("gate", "--run-dir", self.root / "run", "--label", "state", "--worktree", wt,
                           "--scope", "a.txt", "--", "mkdir -p cache && touch cache/artifact.bin")
        self.assertEqual(rc, 0, out)
        self.assertValid(out, "gate")
        self.assertEqual(out["outside_scope"], ["notes.txt"])
        self.assertEqual(out["untracked"], ["cache/artifact.bin"])
        self.assertEqual(out["unstaged"], [])
        (wt / "a.txt").write_text("changed after staging\n")
        (wt / "b.txt").write_text("changed, never staged\n")
        rc, out = run_task("gate", "--run-dir", self.root / "run", "--label", "state2", "--worktree", wt, "--", "true")
        self.assertEqual(out["unstaged"], ["a.txt", "b.txt"])
        self.assertEqual(out["outside_scope"], [])

    def test_paths_come_back_verbatim(self):
        # a tab and a double quote make git quote a path unless -z is used, whatever core.quotePath says
        odd_tracked, odd_untracked, odd_staged = 'q"uote\ttab.txt', "dir with space/\u00e9t\u00e9.txt", "\u00fc outside.txt"
        wt = self.worktree("a")
        (wt / odd_tracked).write_text("one\n")
        (wt / odd_staged).write_text("staged\n")
        (wt / "a.txt").write_text("in scope\n")
        git("add", "-A", cwd=wt)
        (wt / odd_tracked).write_text("two\n")
        (wt / "dir with space").mkdir()
        (wt / odd_untracked).write_text("new\n")
        rc, out = run_task("gate", "--run-dir", self.root / "run", "--label", "odd", "--worktree", wt,
                           "--scope", "a.txt", "--scope", odd_tracked, "--", "true")
        self.assertEqual(rc, 0, out)
        self.assertValid(out, "gate")
        self.assertEqual(out["unstaged"], [odd_tracked])
        self.assertEqual(out["untracked"], [odd_untracked])
        self.assertEqual(out["outside_scope"], [odd_staged])
        self.assertTrue(out["dirty"])

    def test_state_json_records_what_the_gate_saw(self):
        wt = self.worktree("a")
        (wt / "a.txt").write_text("staged\n")
        (wt / "b.txt").write_text("staged outside\n")
        git("add", "-A", cwd=wt)
        (wt / "a.txt").write_text("changed after staging\n")
        (wt / "scratch.log").write_text("x\n")
        rc, out = run_task("gate", "--run-dir", self.root / "run", "--label", "task-a-L0r0", "--worktree", wt,
                           "--scope", "a.txt", "--", "true")
        state = json.loads(Path(out["attempt_dir"], "state.json").read_text())
        self.assertEqual(state, {"worktree": str(wt), "label": "task-a-L0r0", "tree": out["tree"],
                                 "head": git("rev-parse", "HEAD", cwd=wt).strip(), "unstaged": ["a.txt"],
                                 "untracked": ["scratch.log"], "outside_scope": ["b.txt"]})
        self.assertEqual((out["head"], out["unstaged"], out["untracked"], out["outside_scope"]),
                         (state["head"], state["unstaged"], state["untracked"], state["outside_scope"]))

    def test_ignored_artifacts_are_not_untracked(self):
        wt = self.worktree("a")
        exclude = Path(git("rev-parse", "--git-path", "info/exclude", cwd=wt).strip())
        exclude = exclude if exclude.is_absolute() else wt / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("cache/\n")
        _, out = run_task("gate", "--run-dir", self.root / "run", "--label", "ignored", "--worktree", wt,
                          "--", "mkdir -p cache && touch cache/artifact.bin")
        self.assertEqual(out["untracked"], [])

    def test_missing_worktree_is_a_json_error(self):
        rc, out = self.gate(self.root / "missing", "true")
        self.assertEqual(rc, 2)
        self.assertIn("worktree does not exist", out["error"])


class ScaffoldTests(TaskCase):
    def test_two_dependent_patches_become_one_scaffolding_commit(self):
        a = self.patch_from("pa", {"a.txt": "one\n"})
        b = self.patch_from("pb", {"a.txt": "two\n", "n.txt": "new\n"}, base_patches=[a])
        wt = self.worktree("c")
        base = git("rev-parse", "HEAD", cwd=wt).strip()
        rc, out = run_task("scaffold", "--worktree", wt, "--patch", a, "--patch", b)
        self.assertEqual(rc, 0, out)
        self.assertValid(out, "scaffold")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["head"], git("rev-parse", "HEAD", cwd=wt).strip())
        self.assertIn(f"head {out['head']}", out["output"])
        self.assertEqual(git("rev-parse", "HEAD^", cwd=wt).strip(), base)
        self.assertEqual((wt / "a.txt").read_text(), "two\n")
        self.assertEqual(git("status", "--porcelain", cwd=wt), "")

    def test_rerun_with_the_same_patches_reports_the_existing_scaffold(self):
        a = self.patch_from("pa", {"a.txt": "one\n"})
        wt = self.worktree("c")
        _, first = run_task("scaffold", "--worktree", wt, "--patch", a)
        (wt / "a.txt").write_text("the task's own change\n")
        git("add", "-A", cwd=wt)
        rc, second = run_task("scaffold", "--worktree", wt, "--patch", a)
        self.assertEqual(rc, 0, second)
        self.assertValid(second, "scaffold")
        self.assertEqual((second["exit_code"], second["head"]), (0, first["head"]))
        self.assertIn(f"head {first['head']} (already scaffolded)", second["output"])
        self.assertEqual((wt / "a.txt").read_text(), "the task's own change\n")

    def test_stale_scaffolding_is_refused_and_the_output_relayed(self):
        a = self.patch_from("pa", {"a.txt": "one\n"})
        wt = self.worktree("c")
        _, first = run_task("scaffold", "--worktree", wt, "--patch", a)
        a.write_text(a.read_text().replace("+one", "+one, reworked"))
        (wt / "b.txt").write_text("the task's own work\n")
        git("add", "-A", cwd=wt)
        rc, out = run_task("scaffold", "--worktree", wt, "--patch", a)
        self.assertEqual(rc, 1)
        self.assertValid(out, "scaffold")
        self.assertEqual((out["exit_code"], out["head"]), (1, first["head"]))
        direct = subprocess.run([str(INTEGRATE), "scaffold", str(wt), str(a)], capture_output=True, text=True)
        self.assertEqual(out["output"], direct.stdout + direct.stderr)
        self.assertIn(f"HEAD {first['head']} is a scaffolding commit for other patches", out["output"])
        self.assertIn(f"reset --hard {git('rev-parse', 'HEAD^', cwd=wt).strip()}", out["output"])

    def test_refuses_a_dirty_worktree(self):
        a = self.patch_from("pa", {"a.txt": "one\n"})
        wt = self.worktree("c")
        (wt / "stray.txt").write_text("x\n")
        head = git("rev-parse", "HEAD", cwd=wt).strip()
        rc, out = run_task("scaffold", "--worktree", wt, "--patch", a)
        self.assertEqual(rc, 1)
        self.assertEqual(out["exit_code"], 1)
        self.assertIn("untracked files present", out["output"])
        self.assertEqual(out["head"], head)
        self.assertEqual((wt / "a.txt").read_text(), "alpha\n")

    def test_conflicting_second_patch_leaves_nothing_applied(self):
        a = self.patch_from("pa", {"a.txt": "from a\n", "new-a.txt": "a\n"}, deletes=["b.txt"])
        b = self.patch_from("pb", {"a.txt": "from b\n", "new-b.txt": "b\n"})
        wt = self.worktree("c")
        head = git("rev-parse", "HEAD", cwd=wt).strip()
        rc, out = run_task("scaffold", "--worktree", wt, "--patch", a, "--patch", b)
        self.assertEqual(rc, 1)
        self.assertEqual(out["exit_code"], 1)
        self.assertIn(str(b), out["output"])
        self.assertEqual(out["head"], head)
        self.assertEqual(git("status", "--porcelain", cwd=wt), "")
        self.assertEqual((wt / "a.txt").read_text(), "alpha\n")
        self.assertEqual((wt / "b.txt").read_text(), "beta\n")
        self.assertFalse((wt / "new-a.txt").exists())


class FinishTests(TaskCase):
    def staged_worktree(self) -> Path:
        wt = self.worktree("a")
        (wt / "a.txt").write_text("alpha changed\n")
        git("add", "-A", cwd=wt)
        return wt

    def test_clean_pass_exports_and_verifies(self):
        wt = self.staged_worktree()
        patch = self.root / "run" / "patches" / "task-a.patch"
        rc, out = run_task("finish", "--worktree", wt, "--patch", patch, "--scope", "a.txt")
        self.assertEqual(rc, 0, out)
        self.assertValid(out, "finish")
        self.assertEqual(out["patch"], str(patch))
        self.assertIn("diff --git a/a.txt b/a.txt", patch.read_text())
        self.assertEqual(out["sha256"], hashlib.sha256(patch.read_bytes()).hexdigest())
        self.assertEqual(out["files"], ["a.txt"])
        self.assertEqual(out["tree"], git("write-tree", cwd=wt).strip())
        self.assertEqual(out["verify_clean_exit"], 0)
        self.assertIn("exported 1 file diffs", out["output"])
        self.assertIn("clean: staged diff equals", out["output"])

    def test_empty_staged_diff_fails(self):
        wt = self.worktree("a")
        rc, out = run_task("finish", "--worktree", wt, "--patch", self.root / "p" / "empty.patch", "--scope", "a.txt")
        self.assertEqual(rc, 1)
        self.assertValid(out, "finish")
        self.assertEqual(out["verify_clean_exit"], 1)
        self.assertIn("empty staged diff", out["output"])

    def test_stray_untracked_file_fails_verify_clean(self):
        wt = self.staged_worktree()
        (wt / "debug.log").write_text("x\n")
        rc, out = run_task("finish", "--worktree", wt, "--patch", self.root / "p" / "a.patch", "--scope", "a.txt")
        self.assertEqual(rc, 1)
        self.assertValid(out, "finish")
        self.assertEqual(out["verify_clean_exit"], 1)
        self.assertIn("untracked files present", out["output"])
        self.assertIn("debug.log", out["output"])

    def test_out_of_scope_staged_file_fails(self):
        wt = self.staged_worktree()
        (wt / "b.txt").write_text("beta changed\n")
        git("add", "-A", cwd=wt)
        rc, out = run_task("finish", "--worktree", wt, "--patch", self.root / "a.patch", "--scope", "a.txt")
        self.assertEqual(rc, 1)
        self.assertEqual(out["verify_clean_exit"], 1)
        self.assertEqual(out["files"], ["a.txt", "b.txt"])
        self.assertIn("staged paths outside the allowed scope", out["output"])
        rc, out = run_task("finish", "--worktree", wt, "--patch", self.root / "a.patch", "--scope", "a.txt", "--scope", "b.txt")
        self.assertEqual((rc, out["verify_clean_exit"]), (0, 0))

    def test_scope_is_required(self):
        wt = self.staged_worktree()
        rc, out = run_task("finish", "--worktree", wt, "--patch", self.root / "a.patch")
        self.assertEqual(rc, 2)
        self.assertIn("--scope", out["error"])


if __name__ == "__main__":
    unittest.main()
