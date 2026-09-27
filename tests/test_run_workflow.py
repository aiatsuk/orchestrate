"""run_workflow.js: the execute workflow on a fake codex binary, against a real repository and the real helpers."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import REPO, SCRIPTS, git, make_repo  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

NODE = shutil.which("node")
RUNNER = SCRIPTS / "run_workflow.js"
WORKFLOW = REPO / "skill" / "workflows" / "orchestrate-execute.js"
FAKE = REPO / "tests" / "fake_codex.py"
PLAN = SCRIPTS / "plan.py"
PREPARE = SCRIPTS / "prepare_worktrees.sh"
ROLES = REPO / "skill" / "codex" / "agents"
ROUTING = json.loads((REPO / "skill" / "routing.json").read_text())

T1 = {"id": "t1", "title": "mark a", "spec": "task-t1.md", "scope": ["a.txt"], "tier": 2, "gate": ["grep -q expected a.txt"]}
# t2 depends on t1; its second gate command only passes when t1's patch was scaffolded into its worktree
T2 = {"id": "t2", "title": "mark b", "spec": "task-t2.md", "scope": ["b.txt"], "tier": 2, "depends_on": ["t1"],
      "gate": ["grep -q expected b.txt", "grep -q expected a.txt"]}
EDITS = {"feature-run-t1": {"edits": [{"a.txt": "alpha expected\n"}]}, "feature-run-t2": {"edits": [{"b.txt": "beta expected\n"}]}}


def scenario(**per_task):
    """The default edits, with per-task overrides keyed t1/t2."""
    out = json.loads(json.dumps(EDITS))
    for tid, extra in per_task.items():
        out[f"feature-run-{tid}"].update(extra)
    return out


def defect(summary="counts twice", file="a.txt"):
    return {"file": file, "line": 1, "kind": "behavior", "severity": "major", "summary": summary, "scenario": "run it twice"}


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def started_at(pid):
    """The start time ps reports for pid, in the form the runner records."""
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def session_sleep(case):
    """A `sleep 60` leading its own process group, killed when the test ends."""
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    case.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))
    return proc


def wait_dead(pids, seconds=5.0):
    """The pids still alive after waiting up to `seconds` (an orphan is reaped a moment after it dies)."""
    deadline = time.time() + seconds
    while time.time() < deadline and any(alive(pid) for pid in pids):
        time.sleep(0.1)
    return [pid for pid in pids if alive(pid)]


class Fixture:
    """A repository, a two-task codex plan with specs and a reviewer brief, its workflow args and worktrees."""

    def __init__(self, root: Path, scenario_obj: dict, tasks=(T1, T2)):
        self.root = root
        self.repo = make_repo(root / "repo")
        # files a gate command may generate are not the implementer's change
        with open(self.repo / ".git" / "info" / "exclude", "a") as exclude:
            exclude.write("__pycache__/\n")
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        self.wt_parent = root / "wt"
        for t in tasks:
            (self.run_dir / t["spec"]).write_text(f"# Task {t['id']}\n\nPut the word expected into {t['scope'][0]}.\n")
        (self.run_dir / "reviewer-brief.md").write_text("# Reviewer brief\n\nCheck the staged diff against the spec.\n")
        plan = {"schema": "orchestrate-plan/v1", "goal": "mark the files", "harness": "codex", "repo": str(self.repo),
                "base": "main", "run_dir": str(self.run_dir), "worktree_parent": str(self.wt_parent),
                "branch_prefix": "feature/run", "tasks": list(tasks)}
        self.plan_path = root / "plan.json"
        self.plan_path.write_text(json.dumps(plan))
        check = subprocess.run([sys.executable, str(PLAN), "check", "--check-files", str(self.plan_path)], capture_output=True, text=True)
        assert check.returncode == 0, check.stdout + check.stderr
        prep = subprocess.run([str(PREPARE), "--repo", str(self.repo), "--base", "main", "--parent", str(self.wt_parent),
                               "--prefix", "feature/run", *[t["id"] for t in tasks]], capture_output=True, text=True)
        assert prep.returncode == 0, prep.stderr
        # workflow-args records each worktree's HEAD and id, so the worktrees come first
        proc = subprocess.run([sys.executable, str(PLAN), "workflow-args", str(self.plan_path)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        self.args_path = root / "args.json"
        self.args_path.write_text(proc.stdout)
        self.args = json.loads(proc.stdout)
        self.scenario_path = root / "scenario.json"
        self.scenario_path.write_text(json.dumps(scenario_obj))
        self.calls_path = root / "calls.jsonl"

    def workflow_args(self, name, *extra):
        """Run plan.py workflow-args again (for example after a spec edit or with --only) into `name`."""
        proc = subprocess.run([sys.executable, str(PLAN), "workflow-args", str(self.plan_path), *map(str, extra)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        path = self.root / name
        path.write_text(proc.stdout)
        return path

    def command(self, *extra, script=WORKFLOW, args_path=None):
        return [NODE, str(RUNNER), "--script", str(script), "--args", str(args_path or self.args_path), "--codex", str(FAKE), *map(str, extra)]

    def environ(self, env=None):
        return {**os.environ, "FAKE_CODEX_SCENARIO": str(self.scenario_path), "FAKE_CODEX_CALLS": str(self.calls_path), **(env or {})}

    def run(self, *extra, script=WORKFLOW, args_path=None, env=None):
        return subprocess.run(self.command(*extra, script=script, args_path=args_path), capture_output=True, text=True,
                              env=self.environ(env), timeout=120)

    def registry(self):
        return self.run_dir / "agents" / "process-groups.json"

    def lock(self):
        return self.run_dir / "agents" / "runner.lock"

    def hanging_runner(self, case, *extra):
        """A runner whose first implementer hangs; returns (runner, grandchild pid) once the fake is running."""
        pids = self.root / "grandchildren.txt"
        runner = subprocess.Popen(self.command("--idle-timeout", "60", *extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=self.environ({"FAKE_CODEX_HANG": "30", "FAKE_CODEX_GRANDCHILD": str(pids)}))
        case.addCleanup(lambda: runner.poll() is None and (runner.kill(), runner.communicate()))
        deadline = time.time() + 15
        while time.time() < deadline and not (pids.exists() and pids.read_text().strip()):
            time.sleep(0.1)
        grandchild = int(pids.read_text().split()[0])
        pgid = os.getpgid(grandchild)
        case.addCleanup(lambda: group_alive(pgid) and os.killpg(pgid, signal.SIGKILL))
        return runner, grandchild

    def calls(self):
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text().splitlines() if line.strip()]

    def journal(self, path=None):
        path = path or self.run_dir / "execute-journal.jsonl"
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

    def labels(self, path=None):
        return [e["label"] for e in self.journal(path) if e["type"] == "started"]

    def entry(self, label, kind="result", path=None, last=False):
        journal = self.journal(path)
        started = [e for e in journal if e["type"] == "started" and e["label"] == label]
        started = started[-1] if last else started[0]
        return next(e for e in journal if e["type"] == kind and e["agentId"] == started["agentId"])

    def result(self, path=None):
        return {t["id"]: t for t in json.loads(Path(path or self.run_dir / "execute-result.json").read_text())["tasks"]}

    def gate_dirs(self):
        return sorted(p.name for p in (self.run_dir / "gates").iterdir())

    def worktree(self, tid):
        return self.wt_parent / f"feature-run-{tid}"

    def verify(self, args_path=None, result_path=None):
        return subprocess.run([sys.executable, str(PLAN), "verify-result", str(args_path or self.args_path),
                               str(result_path or self.run_dir / "execute-result.json")], capture_output=True, text=True)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def fixture(self, scenario_obj=None, tasks=(T1, T2)):
        return Fixture(self.root, scenario_obj or scenario(), tasks)

    def run_ok(self, fx, *extra, **kwargs):
        proc = fx.run(*extra, **kwargs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def timed(self, fx, *extra, **kwargs):
        start = time.time()
        proc = self.run_ok(fx, *extra, **kwargs)
        return proc, time.time() - start


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class HappyPathTests(unittest.TestCase):
    """One real run of the execute workflow: t1, then t2 scaffolded on t1's patch."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixture(Path(cls._tmp.name).resolve(), scenario())
        cls.proc = cls.fx.run()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_both_tasks_pass_and_the_result_is_written_and_printed(self):
        result = self.fx.result()
        self.assertEqual({tid: t["status"] for tid, t in result.items()}, {"t1": "PASS", "t2": "PASS"})
        self.assertEqual(json.loads(self.proc.stdout), json.loads((self.fx.run_dir / "execute-result.json").read_text()))

    def test_dependent_was_scaffolded_on_the_dependency_patch(self):
        wt = self.fx.worktree("t2")
        self.assertEqual(git("log", "-1", "--format=%s", cwd=wt).strip(), "scaffolding (temporary)")
        self.assertEqual((wt / "a.txt").read_text(), "alpha expected\n")
        self.assertEqual(self.fx.result()["t2"]["files"], ["b.txt"])
        self.assertLess(self.fx.labels().index("finish:t1"), self.fx.labels().index("scaffold:t2"))

    def test_verify_result_accepts_the_result(self):
        proc = self.fx.verify()
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_journal_has_one_started_and_one_result_line_per_agent(self):
        journal = self.fx.journal()
        expected = ["impl:t1:L0", "gate:t1:L0r0", "review:t1:conformance:L0r0", "finish:t1",
                    "scaffold:t2", "impl:t2:L0", "gate:t2:L0r0", "review:t2:conformance:L0r0", "finish:t2"]
        self.assertEqual(self.fx.labels(), expected)
        by_agent = {}
        for entry in journal:
            by_agent.setdefault(entry["agentId"], []).append(entry["type"])
        self.assertEqual(len(by_agent), len(expected))
        self.assertTrue(all(types == ["started", "result"] for types in by_agent.values()), by_agent)
        started = next(e for e in journal if e["label"] == "impl:t1:L0")
        self.assertEqual(set(started), {"type", "key", "agentId", "label", "phase"})
        self.assertEqual(started["phase"], "Implement")
        self.assertEqual(len(started["key"]), 64)

    def test_reviewer_runs_read_only_and_implementer_workspace_write(self):
        sandboxes = {(c["kind"], c["worktree"]): c["sandbox"] for c in self.fx.calls()}
        self.assertEqual(sandboxes, {("impl", "feature-run-t1"): "workspace-write", ("review", "feature-run-t1"): "read-only",
                                     ("impl", "feature-run-t2"): "workspace-write", ("review", "feature-run-t2"): "read-only"})

    def test_relay_steps_never_spawn_codex(self):
        self.assertEqual(sorted(c["kind"] for c in self.fx.calls()), ["impl", "impl", "review", "review"])
        for label in ("gate:t1:L0r0", "finish:t1", "scaffold:t2", "gate:t2:L0r0", "finish:t2"):
            self.assertTrue(self.fx.entry(label)["agentId"].startswith("local-"), label)
        self.assertTrue((self.fx.run_dir / "gates" / "task-t1-L0r0" / "result.json").is_file())
        self.assertTrue((self.fx.run_dir / "agents" / "gate_t1_L0r0.log").is_file())

    def test_codex_argument_lists(self):
        calls = {c["kind"] + ":" + c["worktree"]: c for c in self.fx.calls()}
        tier2, tier3 = ROUTING["codex"]["tiers"]["2"], ROUTING["codex"]["tiers"]["3"]
        common = subprocess.run(["git", "-C", str(self.fx.worktree("t1")), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        for key, sandbox, entry, extra in (("impl:feature-run-t1", "workspace-write", tier2, ["--add-dir", common]),
                                           ("review:feature-run-t1", "read-only", tier3, [])):
            argv = calls[key]["argv"]
            wt = str(self.fx.worktree("t1"))
            self.assertEqual(argv[:9], ["exec", "--skip-git-repo-check", "-C", wt, "-s", sandbox, "-m", entry["model"], "-c"])
            self.assertEqual(argv[9], f"model_reasoning_effort={entry['effort']}")
            self.assertEqual(argv[10:10 + len(extra)], extra)
            rest = argv[10 + len(extra):]
            self.assertEqual([rest[0], rest[2], rest[4]], ["--output-schema", "-o", "--json"])
            self.assertEqual(len(rest), 6)
            self.assertNotIn("--ephemeral", argv)

    def test_implementer_gets_the_git_common_dir_and_the_reviewer_does_not(self):
        calls = {c["kind"] + ":" + c["worktree"]: c for c in self.fx.calls()}
        self.assertEqual([os.path.realpath(d) for d in calls["impl:feature-run-t1"]["add_dirs"]],
                         [os.path.realpath(self.fx.repo / ".git")])
        self.assertEqual(calls["review:feature-run-t1"]["add_dirs"], [])

    def test_role_instructions_are_prepended_from_the_role_file(self):
        calls = {c["kind"] + ":" + c["worktree"]: c for c in self.fx.calls()}
        impl, review = calls["impl:feature-run-t1"]["prompt"], calls["review:feature-run-t1"]["prompt"]
        self.assertTrue(impl.startswith("You are the implementer for one task of an orchestrated run."))
        self.assertIn("\n\nYou are the tier 2 implementer for task t1", impl)
        self.assertTrue(review.startswith("You are an independent reviewer. You did not write the change under review."))
        if tomllib:
            role = tomllib.loads((ROLES / "orchestrate-implementer.toml").read_text())["developer_instructions"].strip()
            self.assertTrue(impl.startswith(role + "\n\n"))

    def test_process_group_registry_is_empty_after_the_run(self):
        self.assertEqual(json.loads(self.fx.registry().read_text()), [])

    def test_runner_lock_is_removed_after_the_run(self):
        self.assertTrue(self.fx.registry().parent.is_dir())
        self.assertFalse(self.fx.lock().exists())

    def test_codex_event_stream_is_saved_per_agent(self):
        log = self.fx.run_dir / "agents" / "impl_t1_L0.jsonl"
        events = [json.loads(line)["type"] for line in log.read_text().splitlines()]
        self.assertEqual(events, ["thread.started", "turn.completed"])


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class ControlFlowTests(FixtureCase):
    def test_interrupted_run_repeats_only_unfinished_agents(self):
        fx = self.fixture()
        self.run_ok(fx)
        journal = fx.run_dir / "execute-journal.jsonl"
        lines = journal.read_text().splitlines()
        # drop the result of t2's review, as if the run had stopped while the reviewer was working
        review = next(json.loads(l) for l in lines if json.loads(l).get("label") == "review:t2:conformance:L0r0")
        kept = [l for l in lines if not (json.loads(l)["type"] == "result" and json.loads(l)["agentId"] == review["agentId"])]
        journal.write_text("\n".join(kept) + "\n")
        before = len(fx.calls())
        self.run_ok(fx)
        new = fx.calls()[before:]
        self.assertEqual([(c["kind"], c["worktree"]) for c in new], [("review", "feature-run-t2")])
        self.assertEqual(fx.result()["t2"]["status"], "PASS")

    def test_reviewer_fail_on_round_zero_goes_to_rework_then_pass(self):
        fx = self.fixture(scenario(t1={"verdicts": [{"verdict": "FAIL", "defects": [defect()]}]}))
        self.run_ok(fx)
        labels = fx.labels()
        for label in ("review:t1:conformance:L0r0", "rework:t1:L0r1", "gate:t1:L0r1", "review:t1:conformance:L0r1"):
            self.assertIn(label, labels)
        self.assertEqual(fx.entry("review:t1:conformance:L0r0")["result"]["verdict"], "FAIL")
        rework = next(c for c in fx.calls() if c["kind"] == "rework")
        self.assertIn("counts twice", rework["prompt"])
        self.assertEqual(rework["sandbox"], "workspace-write")
        t1 = fx.result()["t1"]
        self.assertEqual((t1["status"], t1["rounds"], len(t1["history"])), ("PASS", 1, 1))
        self.assertEqual(fx.verify().returncode, 0)

    def test_invalid_structured_output_is_retried_once_then_succeeds(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 1}}))
        self.run_ok(fx)
        impl = [c for c in fx.calls() if c["kind"] == "impl" and c["worktree"] == "feature-run-t1"]
        self.assertEqual(len(impl), 2)
        self.assertNotIn("did not match the output schema", impl[0]["prompt"])
        self.assertIn("did not match the output schema", impl[1]["prompt"])
        self.assertIn("$: missing required property 'files_changed'", impl[1]["prompt"])
        self.assertEqual([e["type"] for e in fx.journal() if e.get("agentId") == fx.entry("impl:t1:L0")["agentId"]], ["started", "result"])
        self.assertEqual(fx.result()["t1"]["status"], "PASS")

    def test_unparsable_output_is_retried_too(self):
        fx = self.fixture(scenario(t1={"fail_times": {"review": 1}, "fail_mode": "garbage"}))
        self.run_ok(fx)
        reviews = [c for c in fx.calls() if c["kind"] == "review" and c["worktree"] == "feature-run-t1"]
        self.assertEqual(len(reviews), 2)
        self.assertIn("not valid JSON", reviews[1]["prompt"])
        self.assertEqual(fx.result()["t1"]["status"], "PASS")

    def test_second_invalid_output_fails_the_agent(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 2}}))
        self.run_ok(fx)
        self.assertEqual(len([c for c in fx.calls() if c["kind"] == "impl"]), 2)
        self.assertEqual(fx.entry("impl:t1:L0", "failed")["type"], "failed")
        result = fx.result()
        self.assertEqual(result["t1"]["status"], "BLOCKED")
        self.assertIn("returned no result", result["t1"]["reason"])
        self.assertEqual(result["t2"]["status"], "SKIPPED")

    def test_nonzero_codex_exit_is_retried_then_fails_the_agent(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 2}, "fail_mode": "exit"}))
        proc = self.run_ok(fx)
        impl = [c for c in fx.calls() if c["kind"] == "impl"]
        self.assertEqual([c["exit"] for c in impl], [1, 1])
        self.assertIn("exit status 1", impl[1]["prompt"])
        self.assertIn("codex exited with status 1", proc.stderr)
        self.assertEqual(fx.result()["t1"]["status"], "BLOCKED")

    def test_gate_failure_goes_to_rework_without_a_review(self):
        fx = self.fixture(scenario(t1={"edits": [{"a.txt": "wrong\n"}, {"a.txt": "alpha expected\n"}]}))
        self.run_ok(fx)
        labels = fx.labels()
        self.assertEqual(fx.entry("gate:t1:L0r0")["result"]["exit_code"], 1)
        self.assertNotIn("review:t1:conformance:L0r0", labels)
        self.assertLess(labels.index("gate:t1:L0r0"), labels.index("rework:t1:L0r1"))
        kinds = [c["kind"] for c in fx.calls() if c["worktree"] == "feature-run-t1"]
        self.assertEqual(kinds, ["impl", "rework", "review"])
        t1 = fx.result()["t1"]
        self.assertEqual((t1["status"], t1["rounds"], t1["history"][0]["gate_exit"]), ("PASS", 1, 1))
        self.assertEqual(fx.verify().returncode, 0)

    def test_max_parallel_one_never_overlaps_codex_processes(self):
        independent = {**T2, "depends_on": [], "gate": ["grep -q expected b.txt"]}
        fx = self.fixture(tasks=(T1, independent))
        self.run_ok(fx, "--max-parallel", "1", env={"FAKE_CODEX_SLEEP": "0.4"})
        spans = sorted((c["start"], c["end"]) for c in fx.calls())
        self.assertEqual(len(spans), 4)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertGreaterEqual(start, end)

    def test_only_rerun_carries_the_passed_dependency_and_finishes_the_rest(self):
        # first run: t1 passes, t2's reviewer keeps failing the same defect until t2 escalates
        fails = [{"verdict": "FAIL", "defects": [defect(file="b.txt")]}] * 6
        fx = self.fixture(scenario(t2={"verdicts": fails}))
        self.run_ok(fx)
        first = fx.result()
        self.assertEqual((first["t1"]["status"], first["t2"]["status"]), ("PASS", "ESCALATE"))
        # rerun t2 alone with a reviewer that passes; a fresh journal, since the old one would replay the FAILs
        fx.scenario_path.write_text(json.dumps(scenario()))
        only = fx.workflow_args("args-only.json", "--only", "t2", "--previous", fx.run_dir / "execute-result.json")
        self.assertEqual([(t["id"], t.get("done", False)) for t in json.loads(only.read_text())["tasks"]], [("t1", True), ("t2", False)])
        journal, out = fx.run_dir / "journal-only.jsonl", fx.run_dir / "result-only.json"
        before = len(fx.calls())
        self.run_ok(fx, "--journal", journal, "--out", out, args_path=only)
        self.assertNotIn("feature-run-t1", [c["worktree"] for c in fx.calls()[before:]])
        self.assertFalse([label for label in fx.labels(journal) if label.split(":")[1] == "t1"])
        scaffold = fx.entry("scaffold:t2", path=journal)["result"]
        self.assertEqual(scaffold["exit_code"], 0)
        self.assertIn("already scaffolded", scaffold["output"])
        result = fx.result(out)
        self.assertEqual((result["t1"]["status"], result["t1"].get("carried"), result["t2"]["status"]), ("PASS", True, "PASS"))
        proc = fx.verify(only, out)
        self.assertEqual(proc.returncode, 0, proc.stdout)


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class RerunTests(unittest.TestCase):
    """A finished run, a plain rerun, a rerun with regenerated arguments, and one after t1's spec changed."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        fx = cls.fx = Fixture(Path(cls._tmp.name).resolve(), scenario())
        cls.first = fx.run()
        cls.first_calls, cls.first_gates, cls.first_labels = len(fx.calls()), fx.gate_dirs(), fx.labels()
        cls.second = fx.run()
        cls.second_calls, cls.second_gates, cls.second_labels = len(fx.calls()), fx.gate_dirs(), fx.labels()
        cls.second_result, cls.second_verify = fx.result(), fx.verify()
        # regenerated arguments record t2's scaffolded HEAD as its start, so only t2's implementer changes
        cls.regenerated = fx.run(args_path=fx.workflow_args("args-regenerated.json"))
        regenerated_calls = len(fx.calls())
        cls.regenerated_new_calls = fx.calls()[cls.second_calls:]
        (fx.run_dir / "task-t1.md").write_text("# Task t1\n\nPut the word expected into a.txt, on its own line.\n")
        cls.edited_args = fx.workflow_args("args-edited.json")
        cls.third = fx.run(args_path=cls.edited_args)
        cls.third_new_calls = fx.calls()[regenerated_calls:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        for proc in (self.first, self.second, self.regenerated, self.third):
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_rerun_spawns_no_codex_process(self):
        self.assertEqual(self.second_calls, self.first_calls)
        self.assertIn("impl:t1:L0: resumed from the journal", self.second.stderr)
        self.assertIn("review:t2:conformance:L0r0: resumed from the journal", self.second.stderr)
        self.assertEqual({tid: t["status"] for tid, t in self.second_result.items()}, {"t1": "PASS", "t2": "PASS"})

    def test_rerun_always_reruns_the_gate(self):
        self.assertEqual(sorted(set(self.second_gates) - set(self.first_gates)), ["task-t1-L0r0-r2", "task-t2-L0r0-r2"])
        self.assertEqual(self.second_labels.count("gate:t1:L0r0"), 2)
        self.assertEqual(self.second_labels.count("finish:t1"), 2)
        self.assertEqual(os.path.basename(self.second_result["t1"]["gate"]["attempt_dir"]), "task-t1-L0r0-r2")
        self.assertNotIn("gate:t1:L0r0: resumed from the journal", self.second.stderr)
        self.assertEqual(self.second_verify.returncode, 0, self.second_verify.stdout)

    def test_rerun_after_a_finished_run_scaffolds_idempotently(self):
        self.assertEqual(self.second_labels.count("scaffold:t2"), 2)
        scaffold = self.fx.entry("scaffold:t2", last=True)["result"]
        self.assertEqual(scaffold["exit_code"], 0)
        self.assertIn("already scaffolded", scaffold["output"])
        self.assertEqual(self.second_result["t2"]["status"], "PASS")

    def test_spec_edit_reruns_the_implementer(self):
        before = json.loads(self.fx.args_path.read_text())["tasks"][0]["spec_sha256"]
        after = json.loads(self.edited_args.read_text())["tasks"][0]["spec_sha256"]
        self.assertNotEqual(before, after)
        self.assertNotIn(("impl", "feature-run-t1"), [(c["kind"], c["worktree"]) for c in self.regenerated_new_calls])
        rerun = [(c["kind"], c["worktree"]) for c in self.third_new_calls]
        self.assertIn(("impl", "feature-run-t1"), rerun)
        self.assertNotIn(("impl", "feature-run-t2"), rerun)
        self.assertIn(f"sha256 {after}", next(c["prompt"] for c in self.third_new_calls if c["kind"] == "impl"))


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class TimeoutTests(FixtureCase):
    def test_agent_timeout_stops_a_busy_codex_and_retries(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 1}, "fail_mode": "chatter"}))
        proc, elapsed = self.timed(fx, "--agent-timeout", "1", "--idle-timeout", "60", env={"FAKE_CODEX_HANG": "20"})
        self.assertIn("impl:t1:L0: still running after 1s (timeout); stopping its process group", proc.stderr)
        impl = [c for c in fx.calls() if c["kind"] == "impl" and c["worktree"] == "feature-run-t1"]
        self.assertEqual(len(impl), 2)
        self.assertIn("was stopped (still running after 1s (timeout))", impl[1]["prompt"])
        self.assertGreater((fx.run_dir / "agents" / "impl_t1_L0.jsonl").read_text().count("item.updated"), 3)
        self.assertEqual(fx.result()["t1"]["status"], "PASS")
        self.assertLess(elapsed, 15)

    def test_idle_timeout_stops_a_silent_codex_and_its_process_group(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 2}, "fail_mode": "hang"}))
        pids = self.root / "grandchildren.txt"
        proc, elapsed = self.timed(fx, "--idle-timeout", "0.5", "--agent-timeout", "60",
                                   env={"FAKE_CODEX_HANG": "20", "FAKE_CODEX_GRANDCHILD": str(pids)})
        self.assertEqual(proc.stderr.count("no output for 0.5s (idle timeout); stopping its process group"), 2)
        self.assertEqual(len([c for c in fx.calls() if c["kind"] == "impl"]), 2)
        result = fx.result()
        self.assertEqual(result["t1"]["status"], "BLOCKED")
        self.assertIn("returned no result", result["t1"]["reason"])
        self.assertEqual(fx.entry("impl:t1:L0", "failed")["type"], "failed")
        started = [int(line) for line in pids.read_text().split()]
        self.assertEqual(len(started), 2)
        self.assertEqual(wait_dead(started), [])
        self.assertLess(elapsed, 15)

    def test_sigterm_is_followed_by_sigkill(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 1}, "fail_mode": "hang", "ignore_term": True}))
        proc, elapsed = self.timed(fx, "--idle-timeout", "0.5", env={"FAKE_CODEX_HANG": "30"})
        self.assertIn("impl:t1:L0: still running 5s after SIGTERM; sending SIGKILL to its process group", proc.stderr)
        self.assertEqual(fx.result()["t1"]["status"], "PASS")
        self.assertLess(elapsed, 20)


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class StartChecksTests(FixtureCase):
    def test_changed_or_missing_spec_refuses_to_start(self):
        fx = self.fixture()
        (fx.run_dir / "task-t1.md").write_text("# Task t1\n\nA different requirement.\n")
        proc = fx.run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("task t1: the spec", proc.stderr)
        self.assertIn(f"the arguments record {fx.args['tasks'][0]['spec_sha256']}", proc.stderr)
        self.assertIn("Regenerate the arguments with plan.py workflow-args", proc.stderr)
        self.assertEqual(fx.calls(), [])
        self.assertFalse((fx.run_dir / "execute-journal.jsonl").exists())
        (fx.run_dir / "task-t2.md").unlink()
        proc = fx.run()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("task t2: cannot read its spec", proc.stderr)

    def test_recreated_worktree_is_refused_until_the_args_are_regenerated(self):
        fx = self.fixture()
        wt = fx.worktree("t1")
        git("worktree", "remove", "--force", str(wt), cwd=fx.repo)
        git("worktree", "add", "-q", str(wt), "feature/run/t1", cwd=fx.repo)
        proc = fx.run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(f"task t1: the worktree {wt} has id", proc.stderr)
        self.assertIn(f"the arguments record {fx.args['tasks'][0]['worktree_id']}", proc.stderr)
        self.assertIn("After recreating a worktree, regenerate the arguments with plan.py workflow-args", proc.stderr)
        self.assertNotIn("task t2:", proc.stderr)
        self.assertEqual(fx.calls(), [])
        self.assertFalse((fx.run_dir / "execute-journal.jsonl").exists())
        regenerated = fx.workflow_args("args-regenerated.json")
        self.run_ok(fx, args_path=regenerated)
        self.assertEqual({tid: t["status"] for tid, t in fx.result().items()}, {"t1": "PASS", "t2": "PASS"})

    def test_missing_worktree_is_refused(self):
        fx = self.fixture()
        git("worktree", "remove", "--force", str(fx.worktree("t2")), cwd=fx.repo)
        proc = fx.run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(f"task t2: the worktree {fx.worktree('t2')} is missing", proc.stderr)
        self.assertEqual(fx.calls(), [])

    def test_a_task_marked_done_is_not_checked(self):
        fx = self.fixture()
        (fx.run_dir / "task-t1.md").write_text("# Task t1\n\nEdited after it passed.\n")
        git("worktree", "remove", "--force", str(fx.worktree("t1")), cwd=fx.repo)
        args = json.loads(fx.args_path.read_text())
        args["tasks"][0]["done"] = True
        carried = self.root / "carried-args.json"
        carried.write_text(json.dumps(args))
        proc = fx.run(args_path=carried)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("a spec changed", proc.stderr)
        self.assertNotIn("is not the one the arguments were generated for", proc.stderr)

    def test_live_leftover_group_is_refused_then_killed_with_kill_leftovers(self):
        fx = self.fixture()
        leftover = session_sleep(self)
        gone = subprocess.Popen(["true"])
        gone.wait()
        fx.registry().parent.mkdir(parents=True)
        fx.registry().write_text(json.dumps([
            {"pgid": leftover.pid, "label": "impl:t1:L0", "runner": 1, "started": started_at(leftover.pid)},
            {"pgid": gone.pid, "label": "review:t1:conformance:L0r0", "runner": 1, "started": "gone"}]))
        proc = fx.run()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(f"process group {leftover.pid} (impl:t1:L0, runner 1): {leftover.pid} sleep 60", proc.stderr)
        self.assertNotIn(f"process group {gone.pid} ", proc.stderr)
        self.assertIn("--kill-leftovers", proc.stderr)
        self.assertIsNone(leftover.poll())
        self.assertEqual(fx.calls(), [])
        self.assertFalse((fx.run_dir / "execute-journal.jsonl").exists())
        proc = self.run_ok(fx, "--kill-leftovers")
        self.assertIn("killing leftover process groups", proc.stderr)
        self.assertEqual(leftover.wait(timeout=10), -signal.SIGTERM)
        self.assertEqual({tid: t["status"] for tid, t in fx.result().items()}, {"t1": "PASS", "t2": "PASS"})
        self.assertEqual(json.loads(fx.registry().read_text()), [])

    def test_recorded_group_whose_leader_is_another_process_is_left_alone(self):
        fx = self.fixture()
        other = session_sleep(self)
        fx.registry().parent.mkdir(parents=True)
        fx.registry().write_text(json.dumps([{"pgid": other.pid, "label": "impl:t1:L0", "runner": 1, "started": "Thu Jan  1 00:00:00 1970"}]))
        proc = self.run_ok(fx, "--kill-leftovers")
        self.assertNotIn("killing leftover", proc.stderr)
        self.assertIsNone(other.poll())


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class SignalTests(FixtureCase):
    def test_sighup_stops_a_hanging_codex_and_its_grandchild(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 1}, "fail_mode": "hang"}))
        runner, grandchild = fx.hanging_runner(self)
        (entry,) = json.loads(fx.registry().read_text())
        pgid = entry["pgid"]
        self.assertEqual((entry["label"], entry["runner"]), ("impl:t1:L0", runner.pid))
        self.assertEqual(os.getpgid(grandchild), pgid)
        start = time.time()
        runner.send_signal(signal.SIGHUP)
        _, stderr = runner.communicate(timeout=30)
        self.assertEqual(runner.returncode, 128 + signal.SIGHUP, stderr)
        self.assertLess(time.time() - start, 4)  # SIGTERM reached the whole group; no SIGKILL round was needed
        self.assertIn("stopped by SIGHUP", stderr)
        self.assertEqual(wait_dead([grandchild]), [])
        self.assertFalse(group_alive(pgid))
        self.assertEqual(json.loads(fx.registry().read_text()), [])
        self.assertFalse((fx.run_dir / "execute-result.json").exists())
        self.assertFalse(fx.lock().exists())


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class LockTests(FixtureCase):
    def test_second_runner_refuses_while_the_first_holds_the_lock(self):
        fx = self.fixture(scenario(t1={"fail_times": {"impl": 1}, "fail_mode": "hang"}))
        runner, grandchild = fx.hanging_runner(self)
        self.assertEqual(json.loads(fx.lock().read_text()), {"pid": runner.pid, "started": started_at(runner.pid)})
        for extra in ((), ("--kill-leftovers",)):
            with self.subTest(extra=extra):
                proc = fx.run(*extra)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(f"another runner holds {fx.lock()} (pid {runner.pid}, started {started_at(runner.pid)})", proc.stderr)
                self.assertNotIn("killing leftover", proc.stderr)
        self.assertTrue(alive(grandchild))
        self.assertIsNone(runner.poll())
        self.assertEqual(len(fx.calls()), 1)
        runner.send_signal(signal.SIGTERM)
        runner.communicate(timeout=30)
        self.assertEqual(runner.returncode, 128 + signal.SIGTERM)
        self.assertFalse(fx.lock().exists())

    def test_stale_lock_is_taken_over(self):
        fx = self.fixture()
        gone = subprocess.Popen(["true"])
        gone.wait()
        stale = {"dead runner": json.dumps({"pid": gone.pid, "started": "gone"}),
                 "pid reused by another process": json.dumps({"pid": os.getpid(), "started": "Thu Jan  1 00:00:00 1970"}),
                 "unreadable": "not a lock"}
        fx.lock().parent.mkdir(parents=True)
        for name, text in stale.items():
            with self.subTest(name):
                fx.lock().write_text(text)
                proc = self.run_ok(fx)
                self.assertIn("taking over a stale runner lock", proc.stderr)
                self.assertFalse(fx.lock().exists())


REFUSAL_SCRIPT = r"""export const meta = {
  name: 'relay-refusal',
  description: 'relay prompts whose last line is not the task helper',
  phases: [{ title: 'Gate', detail: 'relays' }],
}
const t = args.tasks[0]
const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
const helper = `${args.scripts.python} ${q(args.scripts.task)}`
const intro = 'Run exactly this command once and nothing else, then return the JSON object it prints.\n\n'
const gate = label => `${helper} gate --run-dir ${q(args.run_dir + '/gates')} --label ${q(label)} --worktree ${q(t.worktree)} -- 'true'`
const opts = label => ({ label: `gate:${t.id}:${label}`, phase: 'Gate', schema: args.schemas.gate })
const other = await agent(intro + 'touch pwned-other && ' + gate('other'), opts('other'))
const chained = await agent(intro + gate('chained') + '; touch pwned-chained', opts('chained'))
const substituted = await agent(intro + gate('subst') + ' $(touch pwned-subst)', opts('subst'))
const kind = await agent(intro + `${helper} scaffold --worktree ${q(t.worktree)} --patch '/dev/null'`, opts('kind'))
const accepted = await agent(intro + gate('accepted'), opts('accepted'))
return { other, chained, substituted, kind, accepted: accepted && accepted.exit_code }
"""

NO_MODEL_SCRIPT = r"""export const meta = {
  name: 'no-model',
  description: 'an agent call without a model',
  phases: [{ title: 'Review', detail: 'one reviewer' }],
}
const t = args.tasks[0]
const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
const [verdict] = await parallel([() => agent(`You are an independent reviewer of task ${t.id}. The change is the staged diff of the worktree (\`git -C ${q(t.worktree)} diff --cached\`).`,
  { label: `review:${t.id}:nomodel`, phase: 'Review', schema: args.schemas.verdict })])
return verdict
"""

SLOW_RELAY_SCRIPT = r"""export const meta = {
  name: 'slow-relay',
  description: 'a gate whose command outlives the relay timeout',
  phases: [{ title: 'Gate', detail: 'one slow gate' }],
}
const t = args.tasks[0]
const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
const command = `${args.scripts.python} ${q(args.scripts.task)} gate --run-dir ${q(args.run_dir + '/gates')} --label 'slow' ` +
  `--worktree ${q(t.worktree)} -- ${q(args.slow)}`
return await agent('Run exactly this command once and nothing else.\n\n' + command, { label: `gate:${t.id}:slow`, phase: 'Gate', schema: args.schemas.gate })
"""


def script_with(body):
    return "export const meta = {\n  name: 'probe',\n  description: 'probe',\n  phases: [],\n}\n" + body + "\n"


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class RuntimeTests(FixtureCase):
    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def test_relay_prompt_with_another_command_is_refused(self):
        fx = self.fixture()
        proc = self.run_ok(fx, script=self.write("refusal.js", REFUSAL_SCRIPT))
        out = json.loads(proc.stdout)
        self.assertEqual(out, {"other": None, "chained": None, "substituted": None, "kind": None, "accepted": 0})
        for marker in ("pwned-other", "pwned-chained", "pwned-subst"):
            self.assertFalse((fx.worktree("t1") / marker).exists(), marker)
        for label in ("other", "chained", "subst", "kind"):
            self.assertIn(f"gate:t1:{label}: relay refused", proc.stderr)
            self.assertEqual(fx.entry(f"gate:t1:{label}", "failed")["type"], "failed")
        self.assertFalse((fx.run_dir / "gates" / "chained").exists())
        self.assertTrue((fx.run_dir / "gates" / "accepted" / "result.json").is_file())
        self.assertEqual(fx.calls(), [])

    def test_relay_timeout_stops_the_helper_and_its_commands(self):
        fx = self.fixture()
        pid_file = self.root / "slow.pid"
        slow = self.write("slow-args.json", json.dumps({**fx.args, "slow": f"sleep 30 & echo $! > {pid_file}; wait"}))
        start = time.time()
        proc = self.run_ok(fx, "--relay-timeout", "1", script=self.write("slow.js", SLOW_RELAY_SCRIPT), args_path=slow)
        self.assertLess(time.time() - start, 15)
        self.assertIsNone(json.loads(proc.stdout))
        self.assertIn("gate:t1:slow: still running after 1s (timeout); stopping its process group", proc.stderr)
        self.assertIn("gate:t1:slow: the helper was stopped", proc.stderr)
        self.assertEqual(fx.entry("gate:t1:slow", "failed")["type"], "failed")
        self.assertEqual(wait_dead([int(pid_file.read_text())]), [])
        self.assertEqual(fx.calls(), [])

    def test_missing_model_uses_codex_tier_three_or_stops(self):
        fx = self.fixture()
        script = self.write("nomodel.js", NO_MODEL_SCRIPT)
        proc = fx.run(script=script)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn('args.routing.codex.tiers["3"] is missing', proc.stderr)
        self.assertFalse((fx.run_dir / "execute-result.json").exists())
        self.assertEqual(fx.calls(), [])
        routed = self.write("routed-args.json", json.dumps({**fx.args, "routing": {"codex": {"tiers": {"3": {"model": "tier-three", "effort": "low"}}}}}))
        proc = self.run_ok(fx, script=script, args_path=routed)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "PASS")
        (call,) = fx.calls()
        self.assertEqual((call["model"], call["effort"], call["sandbox"]), ("tier-three", "low", "read-only"))

    def test_script_errors_exit_one_and_write_no_result(self):
        args = self.write("args.json", json.dumps({"harness": "codex", "run_dir": str(self.root / "run"), "tasks": []}))
        cases = {"throw new Error('boom')": "boom", "return Date.now()": "Date.now() is not available",
                 "return Math.random()": "Math.random() is not available", "return new Date()": "new Date() without arguments",
                 "return await agent('x', { label: 'explore:t9' })": "unknown label kind 'explore'"}
        for body, needle in cases.items():
            with self.subTest(body=body):
                proc = subprocess.run([NODE, str(RUNNER), "--script", str(self.write("bad.js", script_with(body))), "--args", str(args)],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn(needle, proc.stderr)
                self.assertFalse((self.root / "run" / "execute-result.json").exists())
        proc = subprocess.run([NODE, str(RUNNER), "--script", str(self.write("ok.js", script_with("return new Date(0).toISOString()"))),
                               "--args", str(args)], capture_output=True, text=True)
        self.assertEqual((proc.returncode, json.loads(proc.stdout)), (0, "1970-01-01T00:00:00.000Z"))
        self.assertEqual(json.loads((self.root / "run" / "execute-result.json").read_text()), "1970-01-01T00:00:00.000Z")

    def test_help_and_usage_errors(self):
        proc = subprocess.run([NODE, str(RUNNER), "--help"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(proc.stdout.startswith("usage: run_workflow.js --script FILE --args FILE"))
        self.assertEqual(subprocess.run([NODE, str(RUNNER), "--args", "x"], capture_output=True, text=True).returncode, 2)
        self.assertEqual(subprocess.run([NODE, str(RUNNER), "--bogus"], capture_output=True, text=True).returncode, 2)
        claude = self.write("claude.json", json.dumps({"harness": "claude", "run_dir": str(self.root), "tasks": []}))
        proc = subprocess.run([NODE, str(RUNNER), "--script", str(WORKFLOW), "--args", str(claude)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("runs codex arguments", proc.stderr)
        for flag in ("--agent-timeout", "--idle-timeout", "--relay-timeout"):
            for value in ("0", "soon"):
                proc = subprocess.run([NODE, str(RUNNER), "--script", str(WORKFLOW), "--args", "x", flag, value], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, (flag, value))
                self.assertIn(f"{flag} must be a positive number of seconds", proc.stderr)
        no_meta = self.write("no-meta.js", "return 1\n")
        proc = subprocess.run([NODE, str(RUNNER), "--script", str(no_meta), "--args", str(claude)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


def js(function_source, payload):
    """Call `function_source` (m, input) => value with run_workflow.js's exports and return its JSON result."""
    code = ("const m = require(process.argv[1]); const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            f"process.stdout.write(JSON.stringify(({function_source})(m, input)))")
    proc = subprocess.run([NODE, "-e", code, str(RUNNER)], input=json.dumps(payload), capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


@unittest.skipUnless(NODE, "node is required to run workflow scripts")
class HelperTests(unittest.TestCase):
    def test_toml_string_forms(self):
        cases = [
            ('a = """\nline one\nwith \\"quote\\" and \\\\ backslash\n"""\n', "a", 'line one\nwith "quote" and \\ backslash\n'),
            ("a = '''\nraw \\n stays\n'''\n", "a", "raw \\n stays\n"),
            ('a = "x\\ty\\u00e9"\n', "a", "x\tyé"),
            ("a = 'literal'\n", "a", "literal"),
            ('a = """\nab \\\n    cd"""\n', "a", "ab cd"),
            ('a_extra = "no"\na = "yes"\n', "a", "yes"),
            ('b = "x"\n', "a", None),
            ('a = """never closed\n', "a", None),
            ("a = 42\n", "a", None),
        ]
        for text, key, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(js("(m, i) => m.tomlString(i.text, i.key)", {"text": text, "key": key}), expected)

    @unittest.skipUnless(tomllib, "tomllib is required to cross-check the role files")
    def test_role_files_read_like_a_toml_parser(self):
        for path in sorted(ROLES.glob("*.toml")):
            with self.subTest(role=path.name):
                expected = tomllib.loads(path.read_text())["developer_instructions"]
                self.assertEqual(js("(m, i) => m.tomlString(i, 'developer_instructions')", path.read_text()), expected)

    def test_strict_schema_closes_every_object_and_requires_every_property(self):
        verdict = json.loads((REPO / "skill" / "schemas" / "verdict.schema.json").read_text())
        out = js("(m, s) => ({ strict: m.strictSchema(s), original: s, after: JSON.parse(JSON.stringify(s)) })", verdict)
        strict = out["strict"]
        self.assertIs(strict["additionalProperties"], False)
        item = strict["properties"]["defects"]["items"]
        self.assertIs(item["additionalProperties"], False)
        self.assertEqual(sorted(item["required"]), sorted(item["properties"]))
        self.assertIn("line", item["required"])
        self.assertEqual(item["properties"]["line"], {"type": "integer"})
        self.assertIs(strict["properties"]["gate"]["items"]["additionalProperties"], False)
        self.assertNotIn("additionalProperties", verdict)
        self.assertNotIn("line", verdict["properties"]["defects"]["items"]["required"])

    def test_validator_reports_type_required_enum_and_item_errors(self):
        verdict = json.loads((REPO / "skill" / "schemas" / "verdict.schema.json").read_text())
        good = {"verdict": "PASS", "defects": [], "notes": [], "gate": []}
        self.assertEqual(js("(m, i) => m.validate(i.v, i.s)", {"v": good, "s": verdict}), [])
        bad = {"verdict": "MAYBE", "defects": [{"file": "a", "line": True, "kind": "behavior", "severity": "major", "summary": "s"}], "notes": "x"}
        errors = js("(m, i) => m.validate(i.v, i.s)", {"v": bad, "s": verdict})
        self.assertEqual(sorted(errors), sorted([
            '$.verdict: "MAYBE" is not one of ["PASS","FAIL"]', "$: missing required property 'gate'",
            "$.defects[0]: missing required property 'scenario'", "$.defects[0].line: expected integer, got boolean",
            "$.notes: expected array, got string"]))

    def test_worktree_id_matches_plan_py(self):
        sys.path.insert(0, str(SCRIPTS))
        import plan as planmod
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp).resolve() / "my repo")
            linked = Path(tmp).resolve() / "linked wt"
            git("worktree", "add", "-q", "-b", "x", str(linked), "main", cwd=repo)
            alias = Path(tmp).resolve() / "alias"
            alias.symlink_to(linked)  # the id hashes the path as given, without resolving symlinks
            for path in (str(repo), str(linked), str(linked) + "/", str(alias)):
                with self.subTest(path=path):
                    self.assertEqual(js("(m, p) => m.worktreeId(p)", path), planmod.worktree_id(path))

    def test_default_timeouts(self):
        opts = js("(m, i) => m.parseArgs(i)", ["--script", "s.js", "--args", "a.json"])
        self.assertEqual((opts["agentTimeout"], opts["idleTimeout"], opts["relayTimeout"], opts["killLeftovers"]), (1800, 1800, 1800, False))
        self.assertTrue(js("(m, i) => m.parseArgs(i).killLeftovers", ["--script", "s.js", "--args", "a.json", "--kill-leftovers"]))
        usage = subprocess.run([NODE, str(RUNNER), "--help"], capture_output=True, text=True).stdout
        self.assertIn("printed no event for S seconds (default: 1800)", usage)
        self.assertIn("must exceed the longest gate command that runs without output", usage)

    def test_relay_command_accepts_only_the_quoted_helper(self):
        scripts = {"python": "python3", "task": "/skill/scripts/task.py"}
        ok = "python3 '/skill/scripts/task.py' gate --run-dir '/run/gates' --label 'task-a' --worktree '/wt/a' -- 'it'\\''s ok'"
        cases = {ok: True, ok + "; rm -rf x": False, ok + " | tee x": False, ok + " `id`": False,
                 "python3 /skill/scripts/task.py gate --label x": False, "python3 '/other/task.py' gate": False,
                 "python3 '/skill/scripts/task.py' finish --patch 'p'": False}
        for line, accepted in cases.items():
            with self.subTest(line=line):
                out = js("(m, i) => m.relayCommand(i.p, 'gate', i.s)", {"p": "Run exactly this.\n\n" + line + "\n\n", "s": scripts})
                self.assertEqual("command" in out, accepted, out)


if __name__ == "__main__":
    unittest.main()
