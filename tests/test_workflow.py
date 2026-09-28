"""The execute workflow's control flow, run under node with scripted agents (tests/workflow_harness.js)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

SCRIPT = REPO / "skill" / "workflows" / "orchestrate-execute.js"
HARNESS = REPO / "tests" / "workflow_harness.js"
NODE = shutil.which("node")


def task(tid, *, deps=(), scaffold=None, risky=False, chain=("sonnet", "opus")):
    lenses = [{"key": "conformance", "model": "opus", "effort": None}]
    if risky:
        lenses.append({"key": "adversary", "model": "sonnet", "effort": None})
    return {
        "id": tid, "title": f"task {tid}", "spec": f"/run/task-{tid}.md", "worktree": f"/wt/run-{tid}",
        "branch": f"run/{tid}", "scope": [f"src/{tid}.txt"], "depends_on": list(deps),
        "scaffold_from": list(scaffold if scaffold is not None else deps), "gate": [f"check {tid}"],
        "risk": ["async"] if risky else [], "gate_label_prefix": f"task-{tid}", "spec_sha256": f"sha-of-{tid}",
        "start_head": f"base-{tid}" if (scaffold if scaffold is not None else deps) else f"head-{tid}", "worktree_id": f"wt-id-{tid}",
        "chain": [{"tier": 2 + i, "model": m, "effort": None} for i, m in enumerate(chain)],
        "lenses": lenses, "confirm": {"model": "opus", "effort": None}, "patch": f"/run/patches/task-{tid}.patch",
    }


def args(*tasks, agent_types=True):
    names = {"implementer": "orchestrate-implementer", "reviewer": "orchestrate-reviewer"}
    return {
        "schema": "orchestrate-execute-args/v1", "version": "0.0.0", "harness": "claude", "run_dir": "/run", "repo": "/repo",
        "base": "main", "scripts": {"task": "/skill/scripts/task.py", "python": "python3"},
        "reviewer_brief": "/run/reviewer-brief.md", "limits": {"rework_rounds": 2},
        "utility": {"model": "haiku", "effort": "low"},
        "agent_types": names if agent_types else {"implementer": None, "reviewer": None},
        "role_text": {"implementer": "" if agent_types else "IMPLEMENTER ROLE TEXT", "reviewer": "" if agent_types else "REVIEWER ROLE TEXT"},
        "schemas": {k: {"type": "object", "properties": {}} for k in ("report", "verdict", "gate", "finish", "scaffold", "confirm")},
        "adversary_variations": ["two events in the same turn", "duplicate delivery"],
        "tasks": list(tasks),
    }


def fail(*defects):
    return {"verdict": "FAIL", "defects": list(defects), "notes": [], "gate": []}


def defect(file="src/a.txt", kind="behavior", severity="major", summary="wrong"):
    return {"file": file, "line": 1, "kind": kind, "severity": severity, "summary": summary, "scenario": "scenario"}


def red_gate():
    return {"attempt_dir": "/run/gates/x", "exit_code": 1, "results": [{"command": "check", "rc": 1, "outcome": "exit 1", "log": "1.log"}],
            "tail": "FAILED: expected 2 got 1", "tree": "t", "dirty": False, "unstaged": [], "untracked": [], "outside_scope": [], "head": "head-a"}


def green_gate(**state):
    return {"attempt_dir": "/g", "exit_code": 0, "results": [], "tail": "", "tree": "tree-a", "dirty": bool(state),
            "unstaged": [], "untracked": [], "outside_scope": [], "head": "head-a", **state}


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class WorkflowTests(unittest.TestCase):
    def run_flow(self, flow_args, responses=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.json"
            path.write_text(json.dumps({"args": flow_args, "responses": responses or {}}))
            proc = subprocess.run([NODE, str(HARNESS), str(SCRIPT), str(path)], capture_output=True, text=True, timeout=60)
        out = json.loads(proc.stdout)
        self.assertNotIn("error", out, out.get("error"))
        self.assertEqual(out["unknownPhases"], [])
        return out

    def labels(self, out):
        return [c["label"] for c in out["calls"]]

    def by_id(self, out):
        return {t["id"]: t for t in out["result"]["tasks"]}

    def test_meta_is_first_and_pure_and_names_every_phase(self):
        out = self.run_flow(args(task("a")))
        self.assertEqual(out["meta"]["name"], "orchestrate-execute")
        self.assertEqual(out["result"]["schema"], "orchestrate-execute-result/v1")

    def test_happy_path_runs_implement_gate_review_finish_with_roles_and_models(self):
        out = self.run_flow(args(task("a")))
        self.assertEqual(self.labels(out), ["impl:a:L0", "gate:a:L0r0", "review:a:conformance:L0r0", "finish:a"])
        calls = {c["label"]: c for c in out["calls"]}
        self.assertEqual(calls["impl:a:L0"]["agentType"], "orchestrate-implementer")
        self.assertEqual(calls["impl:a:L0"]["model"], "sonnet")
        self.assertEqual(calls["review:a:conformance:L0r0"]["agentType"], "orchestrate-reviewer")
        self.assertEqual(calls["gate:a:L0r0"]["model"], "haiku")
        self.assertEqual(calls["gate:a:L0r0"]["effort"], "low")
        self.assertTrue(all(c["schema"] for c in out["calls"]))
        result = self.by_id(out)["a"]
        self.assertEqual((result["status"], result["tier"], result["rounds"]), ("PASS", 2, 0))
        self.assertEqual(result["patch"], "/run/patches/task-a.patch")

    def test_gate_command_is_quoted_and_uses_the_helper(self):
        t = task("a")
        t["gate"] = ["flutter test 'test/it'\"s.dart'"]
        out = self.run_flow(args(t))
        prompt = next(c["prompt"] for c in out["calls"] if c["label"] == "gate:a:L0r0")
        self.assertIn("python3 '/skill/scripts/task.py' gate --run-dir '/run/gates' --label 'task-a-L0r0' --worktree '/wt/run-a' --scope 'src/a.txt' --", prompt)
        self.assertIn("'flutter test '\\''test/it'\\''\"s.dart'\\'''", prompt)

    def test_red_gate_goes_to_rework_without_spending_a_review(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [red_gate()]})
        self.assertEqual(self.labels(out), ["impl:a:L0", "gate:a:L0r0", "rework:a:L0r1", "gate:a:L0r1", "review:a:conformance:L0r1", "finish:a"])
        rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
        self.assertIn("FAILED: expected 2 got 1", rework)
        self.assertEqual(self.by_id(out)["a"]["rounds"], 1)

    def test_gate_artifacts_outside_the_scope_block_as_environment_without_rework(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(untracked=["pkg/__pycache__/m.pyc"])]})
        self.assertEqual(self.labels(out), ["impl:a:L0", "gate:a:L0r0"])
        result = self.by_id(out)["a"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("environment", result["reason"])
        self.assertIn("pkg/__pycache__/m.pyc", result["reason"])

    def test_unstaged_work_inside_the_scope_is_the_implementers_defect(self):
        for state in ({"untracked": ["src/a.txt"]}, {"untracked": ["src/a.txt/new.txt"]}, {"unstaged": ["src/a.txt"]}):
            with self.subTest(state=state):
                out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(**state)]})
                self.assertIn("rework:a:L0r1", self.labels(out))
                self.assertNotIn("review:a:conformance:L0r0", self.labels(out))
                rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
                self.assertIn("not staged", rework)

    def test_staged_files_outside_the_scope_are_a_defect(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(outside_scope=["notes.txt"])]})
        rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
        self.assertIn("notes.txt", rework)
        self.assertIn("outside the task scope", rework)

    def test_two_rework_rounds_then_one_tier_up(self):
        responses = {f"review:a:conformance:L0r{r}": [fail(defect(file=f"src/f{r}.txt"))] for r in range(3)}
        out = self.run_flow(args(task("a")), responses)
        labels = self.labels(out)
        self.assertEqual([l for l in labels if l.startswith(("impl", "rework"))], ["impl:a:L0", "rework:a:L0r1", "rework:a:L0r2", "impl:a:L1"])
        calls = {c["label"]: c for c in out["calls"]}
        self.assertEqual(calls["impl:a:L1"]["model"], "opus")
        self.assertIn("Earlier attempts on this task failed on", calls["impl:a:L1"]["prompt"])
        result = self.by_id(out)["a"]
        self.assertEqual((result["status"], result["tier"], result["level"]), ("PASS", 3, 1))
        self.assertEqual(len(result["history"]), 3)

    def test_same_defect_after_a_rework_escalates_early(self):
        same = defect(file="src/a.txt", kind="behavior")
        out = self.run_flow(args(task("a")), {"review:a:conformance:L0r0": [fail(same)], "review:a:conformance:L0r1": [fail(same)]})
        labels = self.labels(out)
        self.assertNotIn("rework:a:L0r2", labels)
        self.assertIn("impl:a:L1", labels)
        self.assertTrue(any("not converging" in line for line in out["logs"]))

    def test_regression_introduced_by_a_rework_escalates_early(self):
        out = self.run_flow(args(task("a")), {
            "review:a:conformance:L0r0": [fail(defect(file="src/a.txt"))],
            "review:a:conformance:L0r1": [fail(defect(file="src/b.txt", kind="regression"))],
        })
        self.assertNotIn("rework:a:L0r2", self.labels(out))
        self.assertIn("impl:a:L1", self.labels(out))

    def test_last_tier_failing_reports_escalate_and_skips_dependents(self):
        responses = {f"review:a:conformance:L{l}r{r}": [fail(defect(file=f"src/{l}{r}.txt"))] for l in range(2) for r in range(3)}
        out = self.run_flow(args(task("a"), task("b", deps=["a"])), responses)
        result = self.by_id(out)
        self.assertEqual(result["a"]["status"], "ESCALATE")
        self.assertEqual(result["b"]["status"], "SKIPPED")
        self.assertFalse(any(label.endswith(":b:L0") for label in self.labels(out)))

    def test_risky_task_gets_an_adversary_lens_on_another_model(self):
        out = self.run_flow(args(task("a", risky=True)))
        calls = {c["label"]: c for c in out["calls"]}
        self.assertEqual(calls["review:a:adversary:L0r0"]["model"], "sonnet")
        self.assertEqual(calls["review:a:conformance:L0r0"]["model"], "opus")
        self.assertIn("two events in the same turn", calls["review:a:adversary:L0r0"]["prompt"])
        self.assertNotIn("two events in the same turn", calls["review:a:conformance:L0r0"]["prompt"])

    def test_disagreement_goes_to_a_tie_break_that_can_refute(self):
        out = self.run_flow(args(task("a", risky=True)), {
            "review:a:adversary:L0r0": [fail(defect())],
            "confirm:a:L0r0": [{"defects": [{"index": 0, "confirmed": False, "evidence": "handled by the guard"}]}],
        })
        self.assertEqual(self.by_id(out)["a"]["status"], "PASS")
        self.assertNotIn("rework:a:L0r1", self.labels(out))

    def test_confirmed_or_unanswered_defect_goes_to_rework(self):
        out = self.run_flow(args(task("a", risky=True)), {
            "review:a:adversary:L0r0": [fail(defect())],
            "confirm:a:L0r0": [{"defects": []}],
        })
        self.assertIn("rework:a:L0r1", self.labels(out))

    def test_both_lenses_failing_needs_no_tie_break(self):
        out = self.run_flow(args(task("a", risky=True)), {
            "review:a:adversary:L0r0": [fail(defect())], "review:a:conformance:L0r0": [fail(defect(file="src/x.txt"))],
        })
        self.assertNotIn("confirm:a:L0r0", self.labels(out))
        rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
        self.assertIn("src/x.txt", rework)
        self.assertIn("src/a.txt", rework)

    def test_fail_without_defects_is_not_a_pass(self):
        out = self.run_flow(args(task("a")), {"review:a:conformance:L0r0": [fail()]})
        self.assertIn("rework:a:L0r1", self.labels(out))

    def test_dependent_waits_and_scaffolds_on_every_transitive_patch_in_order(self):
        out = self.run_flow(args(task("a"), task("b"), task("c", deps=["b"], scaffold=["a", "b"])))
        labels = self.labels(out)
        self.assertLess(labels.index("finish:a"), labels.index("scaffold:c"))
        self.assertLess(labels.index("finish:b"), labels.index("scaffold:c"))
        self.assertLess(labels.index("scaffold:c"), labels.index("impl:c:L0"))
        scaffold = next(c["prompt"] for c in out["calls"] if c["label"] == "scaffold:c")
        self.assertIn("--patch '/run/patches/task-a.patch' --patch '/run/patches/task-b.patch'", scaffold)

    def test_independent_tasks_start_before_either_finishes(self):
        out = self.run_flow(args(task("a"), task("b")))
        labels = self.labels(out)
        self.assertLess(labels.index("impl:b:L0"), labels.index("finish:a"))

    def test_failed_scaffold_blocks_the_dependent(self):
        out = self.run_flow(args(task("a"), task("b", deps=["a"])), {"scaffold:b": [{"exit_code": 1, "output": "conflict", "head": ""}]})
        result = self.by_id(out)["b"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("conflict", result["reason"])

    def test_agent_without_result_blocks_instead_of_passing(self):
        for label in ("impl:a:L0", "gate:a:L0r0", "review:a:conformance:L0r0", "finish:a"):
            with self.subTest(label=label):
                out = self.run_flow(args(task("a")), {label: [None]})
                self.assertEqual(self.by_id(out)["a"]["status"], "BLOCKED")

    def test_tree_changed_between_gate_and_export_blocks(self):
        finish = {"patch": "/p", "sha256": "s", "files": [], "tree": "another-tree", "verify_clean_exit": 0, "output": ""}
        out = self.run_flow(args(task("a")), {"finish:a": [finish]})
        result = self.by_id(out)["a"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("staged tree changed", result["reason"])

    def test_verify_clean_failure_blocks(self):
        finish = {"patch": "/p", "sha256": "s", "files": [], "tree": "tree-a", "verify_clean_exit": 1, "output": "untracked files present"}
        out = self.run_flow(args(task("a")), {"finish:a": [finish]})
        self.assertIn("untracked files present", self.by_id(out)["a"]["reason"])

    def test_missing_roles_are_inlined_into_prompts(self):
        out = self.run_flow(args(task("a"), agent_types=False))
        calls = {c["label"]: c for c in out["calls"]}
        self.assertIsNone(calls["impl:a:L0"].get("agentType"))
        self.assertTrue(calls["impl:a:L0"]["prompt"].startswith("IMPLEMENTER ROLE TEXT"))
        self.assertTrue(calls["review:a:conformance:L0r0"]["prompt"].startswith("REVIEWER ROLE TEXT"))

    def test_tracked_files_changed_outside_the_scope_block_as_environment(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(unstaged=["pubspec.lock"])]})
        self.assertEqual(self.labels(out), ["impl:a:L0", "gate:a:L0r0"])
        result = self.by_id(out)["a"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("tracked files outside the scope changed", result["reason"])
        self.assertIn("pubspec.lock", result["reason"])

    def test_a_rework_that_fixes_some_defects_keeps_its_rounds(self):
        two = fail(defect(file="src/a.txt"), defect(file="src/a.txt", summary="second"))
        one = fail(defect(file="src/a.txt"))
        out = self.run_flow(args(task("a")), {"review:a:conformance:L0r0": [two], "review:a:conformance:L0r1": [one]})
        self.assertIn("rework:a:L0r2", self.labels(out))
        self.assertNotIn("impl:a:L1", self.labels(out))

    def test_a_red_gate_with_changed_failures_keeps_its_rounds(self):
        first = dict(red_gate(), tail="FAILED test_one\nFAILED test_two\n2 failed in 0.31s")
        second = dict(red_gate(), tail="FAILED test_two\n1 failed in 0.29s")
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [first], "gate:a:L0r1": [second]})
        self.assertIn("rework:a:L0r2", self.labels(out))

    def test_the_same_red_gate_after_a_rework_escalates(self):
        first = dict(red_gate(), tail="FAILED test_two\n1 failed in 0.31s")
        second = dict(red_gate(), tail="FAILED test_two\n1 failed in 0.52s")
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [first], "gate:a:L0r1": [second]})
        self.assertNotIn("rework:a:L0r2", self.labels(out))
        self.assertIn("impl:a:L1", self.labels(out))

    def test_counts_in_a_summary_footer_are_progress(self):
        for first, second in (("Tests: 3 failed, 12 passed, 15 total\nTime: 4.1 s\nRan all test suites.",
                               "Tests: 1 failed, 14 passed, 15 total\nTime: 3.9 s\nRan all test suites."),
                              ("Found 6 errors in 2 files.", "Found 5 errors in 2 files.")):
            with self.subTest(first=first):
                out = self.run_flow(args(task("a")), {"gate:a:L0r0": [dict(red_gate(), tail=first)],
                                                       "gate:a:L0r1": [dict(red_gate(), tail=second)]})
                self.assertIn("rework:a:L0r2", self.labels(out))

    def test_only_durations_and_times_are_ignored(self):
        first = dict(red_gate(), tail="FAILED test_two at 10:41:07\nTests: 1 failed\nTime: 4.12 s")
        second = dict(red_gate(), tail="FAILED test_two at 10:44:52\nTests: 1 failed\nTime: 812 ms")
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [first], "gate:a:L0r1": [second]})
        self.assertNotIn("rework:a:L0r2", self.labels(out))
        self.assertIn("impl:a:L1", self.labels(out))

    def test_environment_advice_distinguishes_tracked_from_untracked(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(unstaged=["pubspec.lock"])]})
        reason = self.by_id(out)["a"]["reason"]
        self.assertIn("ignore rules do not apply to tracked files", reason)
        self.assertNotIn(".git/info/exclude", reason)
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [green_gate(untracked=["build/out.bin"])]})
        self.assertIn(".git/info/exclude", self.by_id(out)["a"]["reason"])

    def test_a_moved_head_is_a_defect_with_the_reset_command(self):
        moved = dict(green_gate(), head="c0ffee")
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [moved]})
        rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
        self.assertIn("HEAD moved from head-a to c0ffee", rework)
        self.assertIn("reset --soft head-a", rework)
        self.assertNotIn("review:a:conformance:L0r0", self.labels(out))

    def test_scaffold_head_becomes_the_expected_head(self):
        out = self.run_flow(args(task("a"), task("b", deps=["a"])))
        self.assertEqual(self.by_id(out)["b"]["status"], "PASS")
        out = self.run_flow(args(task("a"), task("b", deps=["a"])), {"gate:b:L0r0": [dict(green_gate(), head="base-b")]})
        self.assertIn("rework:b:L0r1", self.labels(out))

    def test_worktree_identity_and_start_are_in_implementer_prompts(self):
        out = self.run_flow(args(task("a")))
        prompt = next(c["prompt"] for c in out["calls"] if c["label"] == "impl:a:L0")
        self.assertIn("Worktree wt-id-a started at head-a", prompt)

    def test_spec_hash_is_part_of_every_implementer_prompt(self):
        out = self.run_flow(args(task("a")), {"review:a:conformance:L0r0": [fail(defect())]})
        for label in ("impl:a:L0", "rework:a:L0r1"):
            prompt = next(c["prompt"] for c in out["calls"] if c["label"] == label)
            self.assertIn("sha256 sha-of-a", prompt)

    def test_carried_over_dependency_is_not_rerun(self):
        done = task("a")
        done.update(done=True, previous={"id": "a", "status": "PASS", "patch": "/run/patches/task-a.patch", "sha256": "s", "tree": "t"})
        out = self.run_flow(args(done, task("b", deps=["a"])))
        labels = self.labels(out)
        self.assertFalse(any(label.endswith(":a:L0") or label == "finish:a" for label in labels))
        self.assertIn("scaffold:b", labels)
        result = self.by_id(out)
        self.assertEqual((result["a"]["status"], result["a"]["carried"]), ("PASS", True))
        self.assertEqual(result["b"]["status"], "PASS")

    def test_a_carried_over_task_without_a_pass_blocks(self):
        done = task("a")
        done.update(done=True, previous={"id": "a", "status": "ESCALATE"})
        out = self.run_flow(args(done, task("b", deps=["a"])))
        self.assertEqual(self.by_id(out)["a"]["status"], "BLOCKED")
        self.assertEqual(self.by_id(out)["b"]["status"], "SKIPPED")

    def test_a_throwing_agent_blocks_only_its_task(self):
        out = self.run_flow(args(task("a"), task("b")), {"impl:a:L0": ["__throw__"]})
        result = self.by_id(out)
        self.assertEqual(result["a"]["status"], "BLOCKED")
        self.assertIn("the loop stopped for this task", result["a"]["reason"])
        self.assertEqual(result["b"]["status"], "PASS")

    def test_args_that_are_not_the_workflow_args_object_refuse_to_start(self):
        for value in (None, "run/execute-args.json", {"tasks": []}):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "scenario.json"
                    path.write_text(json.dumps({"args": value, "responses": {}}))
                    proc = subprocess.run([NODE, str(HARNESS), str(SCRIPT), str(path)], capture_output=True, text=True, timeout=60)
                self.assertIn("plan.py workflow-args", json.loads(proc.stdout)["error"])

    def test_invalid_rework_rounds_refuse_to_start(self):
        for value in (None, -1, 1.5, 9):
            with self.subTest(value=value):
                flow = args(task("a"))
                flow["limits"]["rework_rounds"] = value
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "scenario.json"
                    path.write_text(json.dumps({"args": flow, "responses": {}}))
                    proc = subprocess.run([NODE, str(HARNESS), str(SCRIPT), str(path)], capture_output=True, text=True, timeout=60)
                self.assertIn("rework_rounds", json.loads(proc.stdout)["error"])

    def test_relays_ask_for_the_long_timeout_and_forbid_guessing(self):
        out = self.run_flow(args(task("a")))
        for label in ("gate:a:L0r0", "finish:a"):
            prompt = next(c["prompt"] for c in out["calls"] if c["label"] == label)
            self.assertIn("600000 ms", prompt)
            self.assertIn("never write or guess the output", prompt)

    def test_adversary_does_not_rerun_the_full_gate(self):
        out = self.run_flow(args(task("a", risky=True)))
        calls = {c["label"]: c for c in out["calls"]}
        self.assertIn("do not rerun the full gate", calls["review:a:adversary:L0r0"]["prompt"])
        self.assertIn("check a", calls["review:a:conformance:L0r0"]["prompt"])
        self.assertNotIn("- check a", calls["review:a:adversary:L0r0"]["prompt"])

    def test_review_prompts_carry_the_staged_tree_and_the_spec_hash(self):
        out = self.run_flow(args(task("a", risky=True)), {
            "review:a:adversary:L0r0": [fail(defect())],
            "confirm:a:L0r0": [{"defects": [{"index": 0, "confirmed": False, "evidence": "handled"}]}],
        })
        for label in ("review:a:conformance:L0r0", "review:a:adversary:L0r0", "confirm:a:L0r0"):
            prompt = next(c["prompt"] for c in out["calls"] if c["label"] == label)
            self.assertIn("staged tree tree-a", prompt)
            self.assertIn("sha256 sha-of-a", prompt)

    def test_worktree_paths_with_spaces_are_quoted_in_prompts(self):
        t = task("a")
        t["worktree"] = "/wt/run a"
        out = self.run_flow(args(t))
        review = next(c["prompt"] for c in out["calls"] if c["label"] == "review:a:conformance:L0r0")
        self.assertIn("git -C '/wt/run a' diff --cached", review)

    def test_rework_prompt_is_self_contained(self):
        out = self.run_flow(args(task("a")), {"review:a:conformance:L0r0": [fail(defect(summary="counts twice"))]})
        rework = next(c["prompt"] for c in out["calls"] if c["label"] == "rework:a:L0r1")
        for needle in ("/run/task-a.md", "counts twice", "/wt/run-a", "a.txt: edited", "Do not commit"):
            self.assertIn(needle, rework)
        rereview = next(c["prompt"] for c in out["calls"] if c["label"] == "review:a:conformance:L0r1")
        self.assertIn("counts twice", rereview)


if __name__ == "__main__":
    unittest.main()
