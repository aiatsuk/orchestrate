"""The execute workflow under an external authority (args.authority), with scripted agents."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO
from test_workflow import defect, fail, red_gate

SCRIPT = REPO / "skill" / "workflows" / "orchestrate-execute.js"
VERSION = (REPO / "VERSION").read_text().strip()
HARNESS = REPO / "tests" / "workflow_harness.js"
NODE = shutil.which("node")


def task(tid, *, deps=(), lenses=("conformance",)):
    return {
        "id": tid, "title": f"task {tid}", "spec": f"/runroot/tasks/{tid}.json", "worktree": None, "branch": None,
        "scope": [f"src/{tid}.txt"], "depends_on": list(deps), "scaffold_from": [], "gate": [f"check {tid}"],
        "risk": [], "gate_label_prefix": f"task-{tid}", "chain": [{"tier": 0, "model": None, "effort": None}],
        "lenses": [{"key": key, "model": None, "effort": None} for key in lenses],
    }


def args(*tasks, integration=None, rounds=5):
    out = {
        "schema": "orchestrate-execute-args/v1", "version": VERSION, "harness": "claude", "run_dir": "/runroot",
        "authority": {"name": "delivery", "helper": ["python3", "/plugin/scripts/workflow_steps.py", "--run", "/runroot"]},
        "reviewer_brief": "/plugin/references/host-execution.md", "limits": {"rework_rounds": rounds}, "utility": None,
        "agent_types": {"implementer": None, "reviewer": None}, "role_text": {},
        "formats": {"report": "REPORT FORMAT TEXT", "verdict": "VERDICT FORMAT TEXT", "brief": "YOU ARE NOT ALONE"},
        "schemas": {k: {"type": "object", "properties": {}} for k in ("report", "verdict", "gate", "finish", "step")},
        "adversary_variations": ["two events in the same turn"],
        "tasks": list(tasks),
    }
    if integration:
        out["integration"] = integration
    return out


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class AuthorityWorkflowTests(unittest.TestCase):
    def run_flow(self, flow_args, responses=None, expect_error=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.json"
            path.write_text(json.dumps({"args": flow_args, "responses": responses or {}}))
            proc = subprocess.run([NODE, str(HARNESS), str(SCRIPT), str(path)], capture_output=True, text=True, timeout=60)
        out = json.loads(proc.stdout)
        if expect_error:
            self.assertIn("error", out)
            return out
        self.assertNotIn("error", out, out.get("error"))
        self.assertEqual(out["unknownPhases"], [])
        return out

    def labels(self, out):
        return [c["label"] for c in out["calls"]]

    def prompt(self, out, label):
        return next(c["prompt"] for c in out["calls"] if c["label"] == label)

    def result(self, out, tid):
        return next(t for t in out["result"]["tasks"] if t["id"] == tid)

    def test_happy_path_records_every_step_with_the_authority(self):
        out = self.run_flow(args(task("a")))
        self.assertEqual(self.labels(out), [
            "prepare:a", "dispatch:a:r0", "impl:a:L0", "collect:a:r0", "gate:a:L0r0",
            "review-open:a:r0", "review:a:r0:conformance", "review-close:a:r0", "finish:a"])
        res = self.result(out, "a")
        self.assertEqual((res["status"], res["authority"], res["rounds"]), ("PASS", "delivery", 0))
        self.assertEqual(out["result"]["authority"], "delivery")
        self.assertIn("'python3' '/plugin/scripts/workflow_steps.py' '--run' '/runroot' prepare --task 'a'", self.prompt(out, "prepare:a"))
        self.assertIn("gate --task 'a' --label 'task-a-r0'", self.prompt(out, "gate:a:L0r0"))

    def test_no_model_is_pinned_when_the_authority_names_none(self):
        out = self.run_flow(args(task("a")))
        self.assertTrue(all(c.get("model") is None and c.get("effort") is None for c in out["calls"]))
        self.assertTrue(all(c.get("agentType") is None for c in out["calls"]))

    def test_prepared_worktree_and_dispatch_id_reach_the_implementer(self):
        out = self.run_flow(args(task("a")))
        prompt = self.prompt(out, "impl:a:L0")
        self.assertIn("'/wt/gov-a'", prompt)
        self.assertIn("dispatch_id must be exactly dispatch-a-r0", prompt)
        self.assertIn("sha256 spec-a", prompt)
        self.assertIn("REPORT FORMAT TEXT", prompt)
        self.assertIn("YOU ARE NOT ALONE", prompt)

    def test_review_token_reaches_each_lens_reviewer(self):
        out = self.run_flow(args(task("a", lenses=("conformance", "security"))))
        self.assertIn("--lens 'conformance' --lens 'security'", self.prompt(out, "review-open:a:r0"))
        self.assertIn("review_token must be exactly token-a-security", self.prompt(out, "review:a:r0:security"))
        self.assertIn("Your lens is security", self.prompt(out, "review:a:r0:security"))
        self.assertIn("VERDICT FORMAT TEXT", self.prompt(out, "review:a:r0:conformance"))

    def test_red_gate_is_recorded_as_rework_with_finding_keys_then_redispatched(self):
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [red_gate()]})
        labels = self.labels(out)
        self.assertEqual(labels[4:8], ["gate:a:L0r0", "return:a:r0", "dispatch:a:r1", "rework:a:L0r1"])
        self.assertNotIn("review-open:a:r0", labels)
        self.assertIn("--key '(gate) check|build'", self.prompt(out, "return:a:r0"))
        self.assertIn("dispatch_id must be exactly dispatch-a-r1", self.prompt(out, "rework:a:L0r1"))
        self.assertEqual(self.result(out, "a")["status"], "PASS")

    def test_authority_refusing_rework_blocks_with_its_reason(self):
        refusal = {"exit_code": 1, "output": "", "blocked": "non_converging: the same finding survived a rework round"}
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [red_gate()], "return:a:r0": [refusal]})
        res = self.result(out, "a")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertIn("non_converging", res["reason"])
        self.assertNotIn("dispatch:a:r1", self.labels(out))

    def test_review_fail_is_decided_by_the_authority_without_a_tie_break(self):
        out = self.run_flow(args(task("a", lenses=("conformance", "adversary"))),
                            {"review:a:r0:adversary": [fail(defect(kind="regression"))]})
        labels = self.labels(out)
        self.assertFalse(any(label.startswith("confirm:") for label in labels))
        self.assertIn("rework:a:L0r1", labels)
        self.assertIn("regression", self.prompt(out, "rework:a:L0r1"))
        self.assertEqual(self.result(out, "a")["status"], "PASS")

    def test_review_close_refusal_blocks(self):
        closed = {"exit_code": 1, "output": "", "blocked": "rework_budget: two rework rounds exhausted"}
        out = self.run_flow(args(task("a")), {"review:a:r0:conformance": [fail(defect())], "review-close:a:r0": [closed]})
        res = self.result(out, "a")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertIn("rework_budget", res["reason"])

    def test_the_authority_verdict_wins_over_the_reviewers(self):
        out = self.run_flow(args(task("a")), {"review-close:a:r0": [{"exit_code": 0, "output": "", "verdict": "FAIL"}]})
        self.assertIn("rework:a:L0r1", self.labels(out))
        self.assertIn("the review failed without naming a defect", self.prompt(out, "rework:a:L0r1"))

    def test_an_unaccepted_result_goes_back_to_rework_without_a_gate(self):
        out = self.run_flow(args(task("a")), {"collect:a:r0": [{"exit_code": 1, "output": "scope_violation: changes outside src/a.txt", "accepted": False}]})
        labels = self.labels(out)
        self.assertNotIn("gate:a:L0r0", labels)
        self.assertEqual(labels[4:6], ["dispatch:a:r1", "rework:a:L0r1"])
        self.assertIn("scope_violation", self.prompt(out, "rework:a:L0r1"))

    def test_a_blocked_collect_blocks(self):
        out = self.run_flow(args(task("a")), {"collect:a:r0": [{"exit_code": 1, "output": "", "accepted": False, "blocked": "support_budget exhausted"}]})
        self.assertEqual(self.result(out, "a")["status"], "BLOCKED")

    def test_a_refused_dispatch_blocks_before_any_implementer(self):
        out = self.run_flow(args(task("a")), {"dispatch:a:r0": [{"exit_code": 1, "output": "pre_dispatch_changes"}]})
        self.assertEqual(self.labels(out), ["prepare:a", "dispatch:a:r0"])
        self.assertIn("pre_dispatch_changes", self.result(out, "a")["reason"])

    def test_failed_preparation_blocks_and_skips_dependents(self):
        out = self.run_flow(args(task("a"), task("b", deps=["a"])), {"prepare:a": [{"exit_code": 1, "output": "task_exists"}]})
        self.assertEqual(self.result(out, "a")["status"], "BLOCKED")
        self.assertEqual(self.result(out, "b")["status"], "SKIPPED")
        self.assertNotIn("prepare:b", self.labels(out))

    def test_a_dependent_is_prepared_only_after_its_dependency_passed(self):
        out = self.run_flow(args(task("a"), task("b", deps=["a"])))
        labels = self.labels(out)
        self.assertLess(labels.index("finish:a"), labels.index("prepare:b"))
        self.assertEqual(self.result(out, "b")["status"], "PASS")

    def test_the_round_bound_stops_a_runaway_loop(self):
        gates = {f"gate:a:L0r{i}": [red_gate()] for i in range(3)}
        out = self.run_flow(args(task("a"), rounds=1), gates)
        res = self.result(out, "a")
        self.assertEqual(res["status"], "BLOCKED")
        self.assertIn("bound of 1 rework rounds", res["reason"])
        self.assertEqual(len(res["history"]), 2)

    def test_the_token_reaches_reviewers_without_format_overrides(self):
        flow = args(task("a"))
        del flow["formats"]
        out = self.run_flow(flow)
        prompt = self.prompt(out, "review:a:r0:conformance")
        self.assertIn("review_token must be exactly token-a-conformance", prompt)
        self.assertIn("dispatch_id must be exactly dispatch-a-r0", self.prompt(out, "impl:a:L0"))

    def test_an_implementer_without_result_is_collected_and_reworked(self):
        out = self.run_flow(args(task("a")), {"impl:a:L0": [None], "collect:a:r0": [{"exit_code": 1, "output": "journal_result_missing", "accepted": False}]})
        prompt = self.prompt(out, "rework:a:L0r1")
        self.assertIn("the previous attempt returned no report", prompt)
        self.assertIn("journal_result_missing", prompt)
        self.assertEqual(self.result(out, "a")["status"], "PASS")

    def test_a_quote_in_a_gate_failure_is_quoted_for_the_shell(self):
        gate = red_gate()
        gate["results"][0]["command"] = "it's check"
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [gate]})
        self.assertIn("--key '(gate) it'\\''s check|build'", self.prompt(out, "return:a:r0"))

    def test_a_moved_head_under_an_authority_is_a_defect(self):
        gate = {**red_gate(), "exit_code": 0, "head": "someone-committed"}
        out = self.run_flow(args(task("a")), {"gate:a:L0r0": [gate]})
        self.assertIn("reset --soft head-a", self.prompt(out, "rework:a:L0r1"))

    def test_a_preparation_without_a_worktree_blocks(self):
        out = self.run_flow(args(task("a")), {"prepare:a": [{"exit_code": 0, "output": "ok"}]})
        self.assertEqual(self.result(out, "a")["status"], "BLOCKED")
        self.assertEqual(self.labels(out), ["prepare:a"])

    def test_a_missing_reviewer_blocks_without_closing(self):
        out = self.run_flow(args(task("a")), {"review:a:r0:conformance": [None]})
        self.assertEqual(self.result(out, "a")["status"], "BLOCKED")
        self.assertNotIn("review-close:a:r0", self.labels(out))

    def test_export_with_another_tree_blocks(self):
        finish = {"patch": "/p", "sha256": "s", "files": [], "tree": "other", "verify_clean_exit": 0, "output": ""}
        out = self.run_flow(args(task("a")), {"finish:a": [finish]})
        self.assertEqual(self.result(out, "a")["status"], "BLOCKED")

    def test_integration_review_runs_after_tasks_with_tokens(self):
        target = {"worktree": "/wt/integration", "base_sha": "abc", "acceptance": "all requirements", "requirements": ["R1: oracle"],
                  "gate": ["make test"], "brief": "/runroot/plan.json", "lenses": [{"key": "conformance"}, {"key": "adversary"}]}
        out = self.run_flow(args(integration=target), {"review:integration:adversary": [fail(defect())]})
        self.assertEqual(self.labels(out), ["review-open:integration", "review:integration:conformance",
                                            "review:integration:adversary", "review-close:integration"])
        self.assertIn("--integration --lens 'conformance' --lens 'adversary'", self.prompt(out, "review-open:integration"))
        prompt = self.prompt(out, "review:integration:conformance")
        self.assertIn("git -C '/wt/integration' diff abc", prompt)
        self.assertIn("R1: oracle", prompt)
        self.assertIn("review_token must be exactly token-integration-conformance", prompt)
        self.assertEqual(out["result"]["integration"]["status"], "FAIL")
        self.assertEqual(out["result"]["tasks"], [])

    def test_integration_without_an_authority_refuses_to_start(self):
        flow = args(integration={"worktree": "/w", "base_sha": "a", "acceptance": "x", "lenses": [{"key": "conformance"}]})
        del flow["authority"]
        flow["scripts"] = {"task": "/t.py", "python": "python3"}
        out = self.run_flow(flow, expect_error=True)
        self.assertIn("args.integration needs args.authority", out["error"])

    def test_an_authority_without_a_helper_refuses_to_start(self):
        flow = args(task("a"))
        flow["authority"] = {"name": "delivery", "helper": []}
        out = self.run_flow(flow, expect_error=True)
        self.assertIn("helper", out["error"])


if __name__ == "__main__":
    unittest.main()
