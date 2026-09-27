"""plan.py: plan validation rules, waves, render, workflow-args, verify-result, report, and the schema subset."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO, SCRIPTS, git, make_repo

sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))
import plan as planmod  # noqa: E402

PLAN = SCRIPTS / "plan.py"
TASK = SCRIPTS / "task.py"
SKILL = REPO / "skill"
SCHEMAS = SKILL / "schemas"
ROUTING = json.loads((SKILL / "routing.json").read_text())


def model(harness, tier):
    return ROUTING[harness]["tiers"][str(tier)]["model"]


def step(harness, tier):
    entry = ROUTING[harness]["tiers"][str(tier)]
    return {"model": entry["model"], "effort": entry["effort"]}


def lens(key, harness, tier):
    return {"key": key, **step(harness, tier)}


def adversary(harness):
    entry = ROUTING[harness]["adversary"]
    return {"key": "adversary", "model": entry["model"], "effort": entry["effort"]}


def task(tid, **fields):
    entry = {"id": tid, "title": f"task {tid}", "spec": f"task-{tid}.md", "scope": [f"src/{tid}.txt"], "tier": 2,
             "depends_on": [], "gate": ["true"], "risk": []}
    entry.update(fields)
    return entry


def make_plan(root: Path, *tasks, **fields) -> dict:
    plan = {"schema": "orchestrate-plan/v1", "goal": "ship the change", "harness": "claude", "repo": str(root / "repo"),
            "base": "main", "run_dir": str(root / "run"), "worktree_parent": str(root / "wt"), "branch_prefix": "feature/run",
            "tasks": list(tasks) or [task("a"), task("b", depends_on=["a"])]}
    plan.update(fields)
    return plan


def write_specs(plan: dict) -> None:
    """Create every spec file the plan names that does not exist yet."""
    for t in plan["tasks"]:
        spec = Path(t["spec"]) if Path(t["spec"]).is_absolute() else Path(plan["run_dir"]) / t["spec"]
        spec.parent.mkdir(parents=True, exist_ok=True)
        if not spec.exists():
            spec.write_text(f"# Task {t['id']}\n")


def make_worktrees(plan: dict) -> None:
    """Create the plan's repository (when missing) and every task's worktree that does not exist yet."""
    repo = Path(plan["repo"])
    if not (repo / ".git").exists():
        make_repo(repo)
    prefix = plan["branch_prefix"]
    for t in plan["tasks"]:
        wt = t.get("worktree") or os.path.join(plan["worktree_parent"], f"{prefix.replace('/', '-')}-{t['id']}")
        if not Path(wt, ".git").exists():
            git("worktree", "add", "-q", "-b", f"{prefix}/{t['id']}", wt, "main", cwd=repo)


def run_plan(*args):
    return subprocess.run([sys.executable, str(PLAN), *map(str, args)], capture_output=True, text=True)


def run_task(*args):
    proc = subprocess.run([sys.executable, str(TASK), *map(str, args)], capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, obj, name="plan.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(obj))
        return path

    def errors(self, plan) -> str:
        result = planmod.check_plan(plan, ROUTING)
        return "\n".join(result["errors"])

    def assertRejected(self, plan, *needles):
        text = self.errors(plan)
        self.assertTrue(text, "the plan was accepted")
        for needle in needles:
            self.assertIn(needle, text)

    def assertAccepted(self, plan):
        self.assertEqual(self.errors(plan), "")


class CheckCliTests(TempDirCase):
    def test_valid_plan_prints_order_and_waves(self):
        plan = make_plan(self.root, task("d", depends_on=["b", "c"]), task("b", depends_on=["a"]), task("a"),
                         task("c", depends_on=["a"]), task("e"))
        proc = run_plan("check", self.write(plan))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)
        self.assertEqual(out, {"ok": True, "order": ["a", "e", "b", "c", "d"], "waves": [["a", "e"], ["b", "c"], ["d"]]})

    def test_invalid_plan_exits_one_with_every_error(self):
        plan = make_plan(self.root, task("a", tier=5), task("b", scope=["src/*.txt"]), goal="", harness="other")
        proc = run_plan("check", self.write(plan))
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["errors"]), 4, out["errors"])

    def test_unreadable_plan_is_an_error(self):
        path = self.root / "plan.json"
        path.write_text("{not json")
        proc = run_plan("check", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not valid JSON", json.loads(proc.stdout)["errors"][0])

    def test_check_files_accepts_existing_repo_run_dir_and_specs(self):
        make_repo(self.root / "repo")
        (self.root / "run").mkdir()
        (self.root / "run" / "task-a.md").write_text("spec")
        (self.root / "b.md").write_text("spec")
        plan = make_plan(self.root, task("a"), task("b", spec=str(self.root / "b.md")))
        proc = run_plan("check", self.write(plan), "--check-files")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_check_files_rejects_missing_repo_run_dir_and_spec(self):
        plan = make_plan(self.root, task("a"))
        self.assertEqual(run_plan("check", self.write(plan)).returncode, 0)
        proc = run_plan("check", self.write(plan), "--check-files")
        self.assertEqual(proc.returncode, 1)
        errors = "\n".join(json.loads(proc.stdout)["errors"])
        self.assertIn("is not a Git work tree", errors)
        self.assertIn("run_dir", errors)
        self.assertIn("task-a.md does not exist", errors)


class PlanFieldRuleTests(TempDirCase):
    def test_top_level_fields(self):
        self.assertRejected(make_plan(self.root, schema="orchestrate-plan/v2"), "schema must be")
        self.assertRejected(make_plan(self.root, harness="other-harness"), "harness must be one of")
        self.assertRejected(make_plan(self.root, goal="  "), "goal must be")
        self.assertRejected(make_plan(self.root, repo="repo"), "repo must be an absolute path")
        self.assertRejected(make_plan(self.root, run_dir="run"), "run_dir must be an absolute path")
        self.assertRejected(make_plan(self.root, worktree_parent="wt"), "worktree_parent must be an absolute path")
        self.assertRejected(make_plan(self.root, branch_prefix="feature run"), "branch_prefix")
        self.assertRejected(make_plan(self.root, branch_prefix=""), "branch_prefix")
        self.assertRejected(make_plan(self.root, base=""), "base must be")

    def test_task_count_is_one_to_thirty(self):
        self.assertRejected(make_plan(self.root, tasks=[]), "1 to 30 tasks")
        many = [task(f"t{n}", scope=[f"src/{n}.txt"]) for n in range(31)]
        self.assertRejected(make_plan(self.root, tasks=many), "1 to 30 tasks")
        self.assertAccepted(make_plan(self.root, tasks=many[:30]))

    def test_every_error_is_collected(self):
        plan = make_plan(self.root, task("A"), task("b", title=""), task("c", gate=[]), schema="x")
        errors = planmod.check_plan(plan, ROUTING)["errors"]
        self.assertEqual(len(errors), 4, errors)

    def test_id_pattern_and_uniqueness(self):
        for bad in ("A1", "-a", "a_b", "a" * 42, "", "t1\n"):
            with self.subTest(id=bad):
                self.assertRejected(make_plan(self.root, task(bad)), "id must match")
        self.assertAccepted(make_plan(self.root, task("a" * 41)))
        self.assertRejected(make_plan(self.root, task("a"), task("a", scope=["src/other.txt"])), "task a: duplicate id")

    def test_title_and_spec_are_required(self):
        self.assertRejected(make_plan(self.root, task("a", title="")), "title must be")
        self.assertRejected(make_plan(self.root, task("a", spec="")), "spec must be")

    def test_scope_paths_must_be_literal_and_repo_relative(self):
        cases = {"/abs/a.txt": "not absolute", "src/../a.txt": "'..' component", ".git/config": "'.git' component",
                 "src/*.txt": "not a glob", "src/a?.txt": "not a glob", "src/[ab].txt": "not a glob", ".": "repository root",
                 "./": "repository root", "src//a.txt": "empty or '.' component", "./src/a.txt": "empty or '.' component",
                 ":(exclude)src": "pathspec magic"}
        for path, needle in cases.items():
            with self.subTest(path=path):
                self.assertRejected(make_plan(self.root, task("a", scope=[path])), needle)
        self.assertRejected(make_plan(self.root, task("a", scope=[])), "scope must be a non-empty list")
        self.assertAccepted(make_plan(self.root, task("a", scope=["src/", "docs/guide.md"])))

    def test_trailing_slash_is_dropped(self):
        result = planmod.check_plan(make_plan(self.root, task("a", scope=["src/", "lib//"])), ROUTING)
        self.assertEqual(result["tasks"][0]["scope"], ["src", "lib"])

    def test_tier_must_be_one_to_three(self):
        for bad in (0, 4, "2", True, 2.0, None):
            with self.subTest(tier=bad):
                self.assertRejected(make_plan(self.root, task("a", tier=bad)), "tier must be 1, 2 or 3")

    def test_reviewer_tier_defaults_and_may_not_be_below_tier(self):
        self.assertRejected(make_plan(self.root, task("a", tier=3, reviewer_tier=2)), "reviewer_tier 2 is below the implementer tier 3")
        self.assertRejected(make_plan(self.root, task("a", reviewer_tier=4)), "reviewer_tier must be 1, 2 or 3")
        result = planmod.check_plan(make_plan(self.root, task("a", tier=3)), ROUTING)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["tasks"][0]["reviewer_tier"], ROUTING["claude"]["reviewer_default_tier"])

    def test_dependencies_must_be_known_and_not_self(self):
        self.assertRejected(make_plan(self.root, task("a", depends_on=["zz"])), "depends on unknown task 'zz'")
        self.assertRejected(make_plan(self.root, task("a", depends_on=["a"])), "task a: depends on itself")

    def test_cycle_is_named(self):
        plan = make_plan(self.root, task("a", depends_on=["c"]), task("b", depends_on=["a"]), task("c", depends_on=["b"]), task("d"))
        self.assertRejected(plan, "dependency cycle: a -> c -> b -> a")
        proc = run_plan("check", self.write(plan))
        self.assertEqual(proc.returncode, 1)

    def test_gate_must_be_non_empty_commands(self):
        self.assertRejected(make_plan(self.root, task("a", gate=[])), "gate must be a non-empty list")
        self.assertRejected(make_plan(self.root, task("a", gate=["make test", " "])), "gate must be a non-empty list")
        self.assertRejected(make_plan(self.root, task("a", gate="make test")), "gate must be a non-empty list")

    def test_risk_tags_must_be_known(self):
        self.assertRejected(make_plan(self.root, task("a", risk=["async", "speed"])), "unknown risk tags ['speed']")
        self.assertAccepted(make_plan(self.root, task("a", risk=["async", "docs"])))

    def test_worktree_override_must_be_absolute(self):
        self.assertRejected(make_plan(self.root, task("a", worktree="wt/a")), "worktree override must be an absolute path")


class WorktreeAndGateLineRuleTests(TempDirCase):
    def computed(self, tid):
        return str(self.root / "wt" / f"feature-run-{tid}")

    def test_two_tasks_may_not_share_a_worktree(self):
        plan = make_plan(self.root, task("a"), task("b", worktree=self.computed("a")))
        self.assertRejected(plan, f"tasks a and b share the worktree {self.computed('a')}")
        plan = make_plan(self.root, task("a", worktree=str(self.root / "x")), task("b", worktree=str(self.root / "x") + "/"))
        self.assertRejected(plan, "tasks a and b share the worktree")

    def test_worktree_may_not_be_the_repository_or_inside_or_around_it(self):
        repo = str(self.root / "repo")
        self.assertRejected(make_plan(self.root, task("a", worktree=repo)), f"task a: worktree {repo} is the repository itself")
        self.assertRejected(make_plan(self.root, task("a"), worktree_parent=repo + "/.worktrees"),
                            f"task a: worktree {repo}/.worktrees/feature-run-a is inside the repository {repo}")
        self.assertRejected(make_plan(self.root, task("a", worktree=str(self.root))),
                            f"task a: worktree {self.root} contains the repository {repo}")
        self.assertAccepted(make_plan(self.root, task("a", worktree=str(self.root / "repo-a"))))

    def test_worktrees_may_not_nest(self):
        plan = make_plan(self.root, task("a"), task("b", worktree=self.computed("a") + "/sub"))
        self.assertRejected(plan, f"task b: worktree {self.computed('a')}/sub is nested in the worktree of task a")
        plan = make_plan(self.root, task("a", worktree=self.computed("b") + "/sub"), task("b"))
        self.assertRejected(plan, "task a: worktree")
        self.assertIn("is nested in the worktree of task b", self.errors(plan))

    def test_gate_command_is_one_line(self):
        for char, name in (("\n", "newline"), ("\r", "carriage return"), ("\0", "NUL")):
            with self.subTest(char=name):
                plan = make_plan(self.root, task("a", gate=["make test", f"echo one{char}echo two"]))
                self.assertRejected(plan, "task a: gate command 2 contains a newline, carriage return or NUL")
        self.assertAccepted(make_plan(self.root, task("a", gate=["make test && echo 'two\\nlines'"])))


class ScopeOverlapTests(TempDirCase):
    def test_equal_paths_in_unordered_tasks_are_rejected(self):
        plan = make_plan(self.root, task("a", scope=["src/x.txt"]), task("b", scope=["docs/b.md", "src/x.txt"]))
        self.assertRejected(plan, "tasks a and b have overlapping scopes (src/x.txt ~ src/x.txt)")

    def test_directory_prefix_overlap_is_rejected(self):
        plan = make_plan(self.root, task("a", scope=["src/"]), task("b", scope=["src/deep/b.txt"]))
        self.assertRejected(plan, "tasks a and b have overlapping scopes (src ~ src/deep/b.txt)")

    def test_overlap_ordered_through_a_transitive_dependency_is_accepted(self):
        plan = make_plan(self.root, task("a", scope=["src"]), task("m", scope=["docs/m.md"], depends_on=["a"]),
                         task("b", scope=["src/b.txt"], depends_on=["m"]))
        self.assertAccepted(plan)

    def test_shared_name_prefix_is_not_a_directory_overlap(self):
        self.assertAccepted(make_plan(self.root, task("a", scope=["src/a"]), task("b", scope=["src/ab.txt"])))


class RenderTests(TempDirCase):
    def test_table_in_topological_order_with_cost_line(self):
        plan = make_plan(self.root, task("b", depends_on=["a"], gate=["pytest -q | tail -1"], risk=["async"]),
                         task("a", title="first | one", tier=1, reviewer_tier=2), task("c", tier=3))
        proc = run_plan("render", self.write(plan))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], "| # | Task | Scope | Tier | Depends on | Gate | Reviewer tier |")
        rows = [line for line in lines[2:] if line.startswith("| ")]
        self.assertEqual([row.split(" | ")[0] for row in rows], ["| a", "| c", "| b"])
        self.assertIn("| a | first \\| one | `src/a.txt` | 1 | - | `true` | 2 |", lines)
        self.assertIn("| b | task b | `src/b.txt` | 2 | a | `pytest -q \\| tail -1` | 3 |", lines)
        m1, m2, m3 = (model("claude", tier) for tier in (1, 2, 3))
        self.assertEqual(lines[-1], f"Cost: implementers tier 1 ({m1}) x1, tier 2 ({m2}) x1, tier 3 ({m3}) x1; "
                                    f"reviewers tier 2 ({m2}) x1, tier 3 ({m3}) x2; adversary reviewers x1.")

    def test_render_refuses_an_invalid_plan(self):
        proc = run_plan("render", self.write(make_plan(self.root, task("a", depends_on=["a"]))))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("depends on itself", proc.stdout)


class WorkflowArgsTests(TempDirCase):
    def args_for(self, plan, agents_dir=None, skill_dir=None) -> dict:
        agents = agents_dir or (self.root / "no-agents")
        extra = ["--skill-dir", skill_dir] if skill_dir else []
        write_specs(plan)
        make_worktrees(plan)
        proc = run_plan("workflow-args", self.write(plan), "--agents-dir", agents, *extra)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def by_id(self, out) -> dict:
        return {t["id"]: t for t in out["tasks"]}

    def test_top_level_fields(self):
        out = self.args_for(make_plan(self.root))
        self.assertEqual(out["schema"], "orchestrate-execute-args/v1")
        self.assertRegex(out["version"], r"^\d+\.\d+\.\d+$")
        self.assertIn(f"version: {out['version']}", (SKILL / "SKILL.md").read_text())
        self.assertEqual((out["harness"], out["repo"], out["base"], out["run_dir"]),
                         ("claude", str(self.root / "repo"), "main", str(self.root / "run")))
        self.assertEqual(out["scripts"], {"task": str(TASK.resolve()), "python": "python3"})
        self.assertEqual(out["reviewer_brief"], str(self.root / "run" / "reviewer-brief.md"))
        self.assertEqual(out["limits"], {"rework_rounds": ROUTING["rework_rounds"]})
        self.assertEqual(out["utility"], ROUTING["claude"]["utility"])
        self.assertEqual(out["adversary_variations"], ROUTING["adversary_variations"])
        self.assertEqual(sorted(out["schemas"]), sorted(["report", "verdict", "gate", "finish", "scaffold", "confirm"]))
        for name, schema in out["schemas"].items():
            self.assertEqual(schema, json.loads((SCHEMAS / f"{name}.schema.json").read_text()))

    def test_worktree_branch_spec_patch_and_label_per_task(self):
        override = str(self.root / "elsewhere" / "b")
        plan = make_plan(self.root, task("a"), task("b", spec=str(self.root / "specs" / "b.md"), worktree=override, scope=["src/b/"]))
        tasks = self.by_id(self.args_for(plan))
        self.assertEqual(tasks["a"]["worktree"], str(self.root / "wt" / "feature-run-a"))
        self.assertEqual(tasks["a"]["branch"], "feature/run/a")
        self.assertEqual(tasks["a"]["spec"], str(self.root / "run" / "task-a.md"))
        self.assertEqual(tasks["a"]["patch"], str(self.root / "run" / "patches" / "task-a.patch"))
        self.assertEqual(tasks["a"]["gate_label_prefix"], "task-a")
        self.assertEqual(tasks["b"]["worktree"], override)
        self.assertEqual(tasks["b"]["spec"], str(self.root / "specs" / "b.md"))
        self.assertEqual(tasks["b"]["scope"], ["src/b"])

    def test_worktree_name_matches_prepare_worktrees(self):
        repo = make_repo(self.root / "repo")
        proc = subprocess.run([str(SCRIPTS / "prepare_worktrees.sh"), "--repo", str(repo), "--base", "main", "--parent",
                               str(self.root / "wt"), "--prefix", "feature/run", "a"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tasks = self.by_id(self.args_for(make_plan(self.root, task("a"))))
        self.assertEqual(tasks["a"]["worktree"], proc.stdout.strip())
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=tasks["a"]["worktree"]).strip(), tasks["a"]["branch"])

    def test_tasks_in_topological_order_and_scaffold_from_is_transitive(self):
        plan = make_plan(self.root, task("d", depends_on=["c", "a"]), task("c", depends_on=["b"]), task("b", depends_on=["a"]),
                         task("a"), task("e"))
        out = self.args_for(plan)
        self.assertEqual([t["id"] for t in out["tasks"]], ["a", "e", "b", "c", "d"])
        tasks = self.by_id(out)
        self.assertEqual(tasks["c"]["scaffold_from"], ["a", "b"])
        self.assertEqual(tasks["d"]["scaffold_from"], ["a", "b", "c"])
        self.assertEqual(tasks["d"]["depends_on"], ["c", "a"])
        self.assertEqual(tasks["e"]["scaffold_from"], [])

    def test_chain_runs_from_the_task_tier_to_three(self):
        tasks = self.by_id(self.args_for(make_plan(self.root, task("a", tier=1), task("c", tier=3))))
        self.assertEqual(tasks["a"]["chain"], [{"tier": tier, **step("claude", tier)} for tier in (1, 2, 3)])
        self.assertEqual(tasks["c"]["chain"], [{"tier": 3, **step("claude", 3)}])

    def test_lenses_without_and_with_risky_tags(self):
        plan = make_plan(self.root, task("safe", risk=["docs", "ui"]), task("risky", risk=["docs", "async"]))
        tasks = self.by_id(self.args_for(plan))
        self.assertNotEqual(adversary("claude")["model"], model("claude", 3))  # the routing this case relies on
        self.assertEqual(tasks["safe"]["lenses"], [lens("conformance", "claude", 3)])
        self.assertEqual(tasks["risky"]["lenses"], [lens("conformance", "claude", 3), adversary("claude")])
        self.assertEqual(tasks["risky"]["confirm"], step("claude", 3))

    def test_adversary_moves_off_the_conformance_model(self):
        self.assertEqual(adversary("claude")["model"], model("claude", 2))  # the routing this case relies on
        tasks = self.by_id(self.args_for(make_plan(self.root, task("a", reviewer_tier=2, risk=["security"]))))
        self.assertEqual(tasks["a"]["lenses"], [lens("conformance", "claude", 2), lens("adversary", "claude", 3)])
        self.assertEqual(tasks["a"]["confirm"], step("claude", 2))

    def test_adversary_equal_to_tier_three_conformance_uses_tier_two(self):
        skill = self.root / "skill"
        shutil.copytree(SKILL, skill)
        routing = copy.deepcopy(ROUTING)
        routing["claude"]["adversary"] = step("claude", 3)
        (skill / "routing.json").write_text(json.dumps(routing))
        tasks = self.by_id(self.args_for(make_plan(self.root, task("a", risk=["data"])), skill_dir=skill))
        self.assertEqual(tasks["a"]["lenses"], [lens("conformance", "claude", 3), lens("adversary", "claude", 2)])
        out = self.args_for(make_plan(self.root, task("a")), skill_dir=skill)
        self.assertEqual(out["scripts"]["task"], str(skill / "scripts" / "task.py"))

    def test_codex_lenses_and_efforts(self):
        plan = make_plan(self.root, task("a", risk=["migration"]), task("b", reviewer_tier=2, risk=["concurrency"]), harness="codex")
        out = self.args_for(plan)
        tasks = self.by_id(out)
        self.assertEqual(adversary("codex")["model"], model("codex", 2))  # the routing this case relies on
        self.assertEqual(tasks["a"]["lenses"], [lens("conformance", "codex", 3), adversary("codex")])
        self.assertEqual(tasks["b"]["lenses"], [lens("conformance", "codex", 2), lens("adversary", "codex", 3)])
        self.assertEqual(tasks["a"]["chain"][0], {"tier": 2, **step("codex", 2)})
        self.assertIsNone(out["utility"])

    def test_missing_roles_give_null_agent_types_and_inline_role_text(self):
        out = self.args_for(make_plan(self.root))
        self.assertEqual(out["agent_types"], {"implementer": None, "reviewer": None})
        implementer = (SKILL / "claude" / "agents" / "orchestrate-implementer.md").read_text()
        self.assertEqual(out["role_text"]["implementer"], implementer.split("---", 2)[2].strip())
        self.assertTrue(out["role_text"]["reviewer"].startswith("You are an independent reviewer."))
        self.assertNotIn("name: orchestrate-reviewer", out["role_text"]["reviewer"])

    def test_installed_roles_are_named_and_not_inlined(self):
        agents = self.root / "agents"
        agents.mkdir()
        for name in ("orchestrate-implementer", "orchestrate-reviewer"):
            (agents / f"{name}.md").write_text("---\nname: x\n---\nbody\n")
        out = self.args_for(make_plan(self.root), agents_dir=agents)
        self.assertEqual(out["agent_types"], {"implementer": "orchestrate-implementer", "reviewer": "orchestrate-reviewer"})
        self.assertEqual(out["role_text"], {"implementer": "", "reviewer": ""})
        (agents / "orchestrate-reviewer.md").unlink()
        out = self.args_for(make_plan(self.root), agents_dir=agents)
        self.assertEqual(out["agent_types"], {"implementer": "orchestrate-implementer", "reviewer": None})
        self.assertEqual(out["role_text"]["implementer"], "")
        self.assertTrue(out["role_text"]["reviewer"])

    def test_codex_always_names_the_roles(self):
        out = self.args_for(make_plan(self.root, harness="codex"))
        self.assertEqual(out["agent_types"], {"implementer": "orchestrate-implementer", "reviewer": "orchestrate-reviewer"})
        self.assertEqual(out["role_text"], {"implementer": "", "reviewer": ""})

    def test_start_head_and_worktree_id_per_task(self):
        plan = make_plan(self.root)
        tasks = self.by_id(self.args_for(plan))
        for tid, t in tasks.items():
            with self.subTest(task=tid):
                wt = t["worktree"]
                self.assertEqual(t["start_head"], git("rev-parse", "HEAD", cwd=wt).strip())
                st = os.stat(os.path.join(wt, ".git"))
                layout = os.path.abspath(wt).encode("utf-8") + b"\0" + str(st.st_ino).encode() + b"\0" + str(st.st_ctime_ns).encode()
                self.assertEqual(t["worktree_id"], hashlib.sha256(layout).hexdigest())
                self.assertEqual(planmod.worktree_id(wt), t["worktree_id"])
        self.assertNotEqual(tasks["a"]["worktree_id"], tasks["b"]["worktree_id"])
        git("commit", "-q", "--allow-empty", "-m", "moved", cwd=tasks["a"]["worktree"])
        again = self.by_id(self.args_for(plan))
        self.assertEqual(again["a"]["start_head"], git("rev-parse", "HEAD", cwd=tasks["a"]["worktree"]).strip())
        self.assertNotEqual(again["a"]["start_head"], tasks["a"]["start_head"])
        self.assertEqual(again["b"]["start_head"], tasks["b"]["start_head"])

    def test_recreated_worktree_gets_a_new_id_at_the_same_commit(self):
        plan = make_plan(self.root, task("a"))
        before = self.by_id(self.args_for(plan))["a"]
        git("worktree", "remove", "--force", before["worktree"], cwd=self.root / "repo")
        git("branch", "-D", "feature/run/a", cwd=self.root / "repo")
        after = self.by_id(self.args_for(plan))["a"]  # args_for prepares the worktree again
        self.assertEqual((after["worktree"], after["start_head"]), (before["worktree"], before["start_head"]))
        self.assertNotEqual(after["worktree_id"], before["worktree_id"])

    def test_missing_worktree_is_an_error(self):
        plan = make_plan(self.root)
        write_specs(plan)
        make_repo(self.root / "repo")
        proc = run_plan("workflow-args", self.write(plan), "--agents-dir", self.root / "no-agents")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["errors"], [
            f"task {tid}: worktree {self.root / 'wt' / f'feature-run-{tid}'} does not exist or has no commit; prepare the "
            "worktrees first (scripts/prepare_worktrees.sh), then run workflow-args" for tid in ("a", "b")])

    def test_spec_sha256_and_missing_spec(self):
        plan = make_plan(self.root)
        out = self.args_for(plan)
        for t in out["tasks"]:
            self.assertEqual(t["spec_sha256"], hashlib.sha256(Path(t["spec"]).read_bytes()).hexdigest())
        (self.root / "run" / "task-b.md").write_text("# Task b, edited\n")
        self.assertNotEqual(self.by_id(self.args_for(plan))["b"]["spec_sha256"], self.by_id(out)["b"]["spec_sha256"])
        (self.root / "run" / "task-b.md").unlink()
        proc = run_plan("workflow-args", self.write(plan), "--agents-dir", self.root / "no-agents")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["errors"], [f"task b: spec file {self.root / 'run' / 'task-b.md'} does not exist"])

    def test_invalid_plan_prints_errors(self):
        proc = run_plan("workflow-args", self.write(make_plan(self.root, task("a", risk=["nope"]))))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown risk tags", proc.stdout)


class OnlyTests(TempDirCase):
    """workflow-args --only / --previous: rerun part of a plan on top of an earlier result."""

    def setUp(self):
        super().setUp()
        # a <- b <- c, a <- d <- f, e alone
        self.plan = make_plan(self.root, task("a"), task("b", depends_on=["a"]), task("c", depends_on=["b"]),
                              task("d", depends_on=["a"]), task("e"), task("f", depends_on=["d"]))
        write_specs(self.plan)
        make_worktrees(self.plan)
        self.plan_path = self.write(self.plan)
        patches = self.root / "run" / "patches"
        patches.mkdir(parents=True)
        self.entries = {}
        for tid, status in (("a", "PASS"), ("b", "PASS"), ("d", "BLOCKED")):
            entry = {"id": tid, "status": status, "reason": ""}
            if status == "PASS":
                (patches / f"task-{tid}.patch").write_text(f"diff --git a/src/{tid}.txt b/src/{tid}.txt\n")
                entry.update(patch=str(patches / f"task-{tid}.patch"), sha256="x", tree="t")
            self.entries[tid] = entry
        self.previous = self.write({"schema": "orchestrate-execute-result/v1", "version": "0.0.0",
                                    "tasks": list(self.entries.values())}, "previous.json")

    def workflow_args(self, *extra):
        proc = run_plan("workflow-args", self.plan_path, "--agents-dir", self.root / "no-agents", *extra)
        return proc.returncode, json.loads(proc.stdout)

    def test_selected_tasks_run_and_needed_ones_are_carried(self):
        rc, out = self.workflow_args("--only", "c", "--previous", self.previous)
        self.assertEqual(rc, 0, out)
        tasks = {t["id"]: t for t in out["tasks"]}
        self.assertEqual([t["id"] for t in out["tasks"]], ["a", "b", "c"])
        for tid in ("a", "b"):
            self.assertIs(tasks[tid]["done"], True)
            self.assertEqual(tasks[tid]["previous"], self.entries[tid])
            self.assertEqual(tasks[tid]["patch"], str(self.root / "run" / "patches" / f"task-{tid}.patch"))
            self.assertIn("spec_sha256", tasks[tid])
        self.assertNotIn("done", tasks["c"])
        self.assertNotIn("previous", tasks["c"])
        self.assertEqual(tasks["c"]["scaffold_from"], ["a", "b"])

    def test_carried_tasks_need_no_worktree(self):
        for tid in ("a", "b"):
            git("worktree", "remove", "--force", str(self.root / "wt" / f"feature-run-{tid}"), cwd=self.root / "repo")
        rc, out = self.workflow_args("--only", "c", "--previous", self.previous)
        self.assertEqual(rc, 0, out)
        tasks = {t["id"]: t for t in out["tasks"]}
        for tid in ("a", "b"):
            self.assertNotIn("start_head", tasks[tid])
            self.assertNotIn("worktree_id", tasks[tid])
        self.assertIn("start_head", tasks["c"])
        self.assertIn("worktree_id", tasks["c"])

    def test_several_selected_tasks(self):
        rc, out = self.workflow_args("--only", "e", "--only", "b", "--previous", self.previous)
        self.assertEqual(rc, 0, out)
        self.assertEqual([(t["id"], t.get("done", False)) for t in out["tasks"]], [("a", True), ("e", False), ("b", False)])

    def test_needed_task_must_be_pass_with_a_patch(self):
        rc, out = self.workflow_args("--only", "f", "--previous", self.previous)
        self.assertEqual(rc, 1)
        self.assertEqual(out["errors"], [f"task d is needed by f but has no PASS result with a patch in {self.previous}"])
        (self.root / "run" / "patches" / "task-a.patch").unlink()
        rc, out = self.workflow_args("--only", "b", "--previous", self.previous)
        self.assertEqual(rc, 1)
        self.assertIn("task a is needed by b but its previous patch", out["errors"][0])
        entries = [dict(self.entries["b"], patch="")]
        empty = self.write({"schema": "orchestrate-execute-result/v1", "tasks": entries}, "empty.json")
        rc, out = self.workflow_args("--only", "c", "--previous", empty)
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out["errors"]), [f"task a is needed by c but has no PASS result with a patch in {empty}",
                                                 f"task b is needed by c but has no PASS result with a patch in {empty}"])

    def test_only_without_previous(self):
        rc, out = self.workflow_args("--only", "e", "--only", "a")
        self.assertEqual(rc, 0, out)
        self.assertEqual([t["id"] for t in out["tasks"]], ["a", "e"])
        self.assertFalse(any("done" in t for t in out["tasks"]))
        rc, out = self.workflow_args("--only", "b", "--only", "c")
        self.assertEqual(rc, 1)
        self.assertEqual(out["errors"], ["task a is needed by b, c but not selected; select it or pass --previous with its PASS result"])

    def test_selected_dependency_of_a_selected_task_is_warned_about(self):
        proc = run_plan("workflow-args", self.plan_path, "--agents-dir", self.root / "no-agents",
                        "--only", "c", "--only", "a", "--only", "e", "--previous", self.previous)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        out = json.loads(proc.stdout)  # stdout stays one JSON document
        self.assertEqual(len(out["warnings"]), 1, out["warnings"])
        warning = out["warnings"][0]
        self.assertTrue(warning.startswith("task c depends on selected task a:"), warning)
        self.assertIn("recreate the worktree of task c", warning)
        self.assertEqual(proc.stderr, f"warning: {warning}\n")
        self.assertEqual([t["id"] for t in out["tasks"]], ["a", "e", "b", "c"])

    def test_no_warning_without_selected_dependencies(self):
        for extra in (("--only", "c", "--previous", self.previous), ()):
            with self.subTest(extra=extra):
                proc = run_plan("workflow-args", self.plan_path, "--agents-dir", self.root / "no-agents", *extra)
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertEqual(json.loads(proc.stdout)["warnings"], [])
                self.assertEqual(proc.stderr, "")

    def test_unknown_task_and_misused_previous(self):
        rc, out = self.workflow_args("--only", "zz")
        self.assertEqual((rc, out["errors"]), (1, ["--only names unknown task 'zz'"]))
        rc, out = self.workflow_args("--previous", self.previous)
        self.assertEqual((rc, out["errors"]), (1, ["--previous needs --only: without a selection every task runs"]))
        not_a_result = self.write({"tasks": []}, "not-a-result.json")
        rc, out = self.workflow_args("--only", "b", "--previous", not_a_result)
        self.assertEqual(rc, 1)
        self.assertIn(f"{not_a_result} is not an execute result", out["errors"][0])


class ResultTests(TempDirCase):
    """verify-result and report against a real worktree, gate attempt and exported patch."""

    def setUp(self):
        super().setUp()
        (self.root / "run").mkdir()
        self.gate_commands = ["grep -q changed a.txt", "test -f a.txt"]
        plan = make_plan(self.root, task("a", scope=["a.txt"], gate=self.gate_commands), task("b", scope=["b.txt"], depends_on=["a"]))
        write_specs(plan)
        make_worktrees(plan)
        self.plan_path = self.write(plan)
        self.args_path = self.root / "args.json"
        proc = run_plan("workflow-args", self.plan_path, "--agents-dir", self.root / "no-agents")
        self.args_path.write_text(proc.stdout)
        self.args = json.loads(proc.stdout)
        self.wt = self.args["tasks"][0]["worktree"]
        Path(self.wt, "a.txt").write_text("alpha changed\n")
        git("add", "-A", cwd=self.wt)
        rc, self.gate = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", "task-a-L0r0", "--worktree", self.wt,
                                 "--", *self.gate_commands)
        self.assertEqual(rc, 0, self.gate)
        rc, finish = run_task("finish", "--worktree", self.wt, "--patch", self.args["tasks"][0]["patch"], "--scope", "a.txt")
        self.assertEqual(rc, 0, finish)
        self.result = {"schema": "orchestrate-execute-result/v1", "version": self.args["version"], "tasks": [
            {"id": "a", "status": "PASS", "reason": "", "tier": 3, "level": 1, "rounds": 1, "gate": self.gate,
             "reviews": [{"lens": "conformance", "model": model("claude", 3), "verdict": "PASS", "defects": 0}],
             "patch": finish["patch"], "sha256": finish["sha256"], "tree": finish["tree"], "files": finish["files"],
             "history": [{"level": 0, "round": 0, "gate_exit": 1, "defects": []}]},
            {"id": "b", "status": "SKIPPED", "reason": "dependency did not pass: x"}]}

    def verify(self, result):
        path = self.write(result, "result.json")
        proc = run_plan("verify-result", self.args_path, path)
        return proc.returncode, json.loads(proc.stdout)

    def test_pass(self):
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["tasks"]["a"], {"ok": True, "status": "PASS", "problems": []})
        self.assertEqual(out["tasks"]["b"], {"ok": True, "status": "SKIPPED", "problems": []})

    def test_sha_mismatch(self):
        self.result["tasks"][0]["sha256"] = hashlib.sha256(b"other").hexdigest()
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertFalse(out["ok"])
        self.assertIn("differs from the result's", out["tasks"]["a"]["problems"][0])

    def test_failing_gate_result(self):
        rc, red = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", "task-a-L0r1", "--worktree", self.wt,
                           "--", *self.gate_commands, "exit 4")
        self.assertEqual(rc, 1)
        self.result["tasks"][0]["gate"] = red
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertIn(f"gate command failed: 'exit 4' (rc 4) in {red['attempt_dir']}/result.json", out["tasks"]["a"]["problems"])

    def test_gate_must_run_exactly_the_planned_commands(self):
        results = Path(self.gate["attempt_dir"]) / "result.json"
        first, second = self.gate_commands
        for label, commands in (("other", ["true"]), ("extra", [first, second, "true"]), ("fewer", [first]),
                                ("reordered", [second, first])):
            with self.subTest(label):
                rc, green = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", f"task-a-{label}",
                                     "--worktree", self.wt, "--", *commands)
                self.assertEqual((rc, green["exit_code"]), (0, 0))
                self.result["tasks"][0]["gate"] = green
                rc, out = self.verify(self.result)
                self.assertEqual(rc, 1)
                self.assertEqual(out["tasks"]["a"]["problems"],
                                 [f"gate result {green['attempt_dir']}/result.json ran {commands}, not the planned gate {self.gate_commands}"])
        self.assertTrue(results.is_file())

    def test_gate_state_lists_must_be_empty(self):
        Path(self.wt, "scratch.log").write_text("left behind by the gate\n")
        rc, seen = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", "task-a-L0r9", "--worktree", self.wt,
                            "--", *self.gate_commands)
        Path(self.wt, "scratch.log").unlink()  # removed afterwards: verify-clean passes, the attempt still saw it
        self.result["tasks"][0]["gate"] = seen
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], ["gate state untracked is ['scratch.log'], expected an empty list"])
        state_file = Path(self.gate["attempt_dir"], "state.json")
        state = json.loads(state_file.read_text())
        self.result["tasks"][0]["gate"] = self.gate
        for key in ("unstaged", "outside_scope"):
            with self.subTest(key):
                state_file.write_text(json.dumps(dict(state, **{key: ["b.txt"]})))
                rc, out = self.verify(self.result)
                self.assertEqual(out["tasks"]["a"]["problems"], [f"gate state {key} is ['b.txt'], expected an empty list"])
        state_file.write_text(json.dumps({k: v for k, v in state.items() if k != "untracked"}))
        rc, out = self.verify(self.result)
        self.assertEqual(out["tasks"]["a"]["problems"], ["gate state untracked is None, expected an empty list"])

    def test_stray_file_fails_verify_clean(self):
        Path(self.wt, "notes.log").write_text("scratch\n")
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertIn("verify-clean failed", out["tasks"]["a"]["problems"][0])
        self.assertIn("notes.log", out["tasks"]["a"]["problems"][0])

    def test_tree_mismatch_and_missing_task(self):
        self.result["tasks"][0]["tree"] = "0" * 40
        del self.result["tasks"][1]
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertTrue(any(p.startswith("worktree tree ") and "differs from the result's '0000" in p
                            for p in out["tasks"]["a"]["problems"]), out)
        self.assertEqual(out["tasks"]["b"]["problems"], ["missing from the execute result"])

    def test_gate_tree_must_equal_the_result_tree(self):
        self.result["tasks"][0]["gate"] = dict(self.gate, tree="f" * 40)
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate tree '{'f' * 40}' differs from the result's tree '{self.result['tasks'][0]['tree']}'"])

    def test_gate_attempt_must_be_this_task_in_this_run(self):
        rc, elsewhere = run_task("gate", "--run-dir", self.root / "elsewhere", "--label", "task-a-L0r5", "--worktree", self.wt, "--", *self.gate_commands)
        self.result["tasks"][0]["gate"] = elsewhere
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate attempt {elsewhere['attempt_dir']} is not directly under {self.root / 'run' / 'gates'}"])
        rc, other = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", "task-b-L0r0", "--worktree", self.wt, "--", *self.gate_commands)
        self.result["tasks"][0]["gate"] = other
        rc, out = self.verify(self.result)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate attempt {other['attempt_dir']} is not named task-a-..."])

    def test_gate_state_must_exist_and_match(self):
        state_file = Path(self.gate["attempt_dir"], "state.json")
        state = json.loads(state_file.read_text())
        state_file.write_text(json.dumps(dict(state, tree="e" * 40)))
        rc, out = self.verify(self.result)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate state tree '{'e' * 40}' differs from the result's tree '{self.result['tasks'][0]['tree']}'"])
        state_file.write_text(json.dumps(dict(state, worktree=str(self.root / "other"))))
        rc, out = self.verify(self.result)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate state worktree '{self.root / 'other'}' is not the task's worktree {self.wt}"])
        state_file.unlink()
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"gate state {state_file} is missing or unreadable"])

    def test_carried_over_task_is_verified_like_a_pass(self):
        previous = self.write({"schema": "orchestrate-execute-result/v1", "version": "0.0.0", "tasks": [self.result["tasks"][0]]}, "previous.json")
        proc = run_plan("workflow-args", self.plan_path, "--agents-dir", self.root / "no-agents", "--only", "b", "--previous", previous)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        rerun_args = self.write(json.loads(proc.stdout), "rerun-args.json")
        skipped = {"schema": "orchestrate-execute-result/v1", "version": "0", "tasks": [{"id": "b", "status": "BLOCKED", "reason": "x"}]}
        carried = dict(skipped, tasks=[dict(self.result["tasks"][0], carried=True), skipped["tasks"][0]])
        for name, result in (("absent from the result", skipped), ("carried into the result", carried)):
            with self.subTest(name):
                proc = run_plan("verify-result", rerun_args, self.write(result, "rerun-result.json"))
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertEqual(json.loads(proc.stdout)["tasks"]["a"], {"ok": True, "status": "PASS", "problems": []})
        Path(self.result["tasks"][0]["patch"]).write_text("tampered\n")
        for name, result in (("absent from the result", skipped), ("carried into the result", carried)):
            with self.subTest(name + ", tampered"):
                proc = run_plan("verify-result", rerun_args, self.write(result, "rerun-result.json"))
                self.assertEqual(proc.returncode, 1)
                problems = json.loads(proc.stdout)["tasks"]["a"]["problems"]
                self.assertTrue(any("patch sha256" in p for p in problems), problems)
        blocked = dict(skipped, tasks=[dict(self.result["tasks"][0], status="BLOCKED"), skipped["tasks"][0]])
        proc = run_plan("verify-result", rerun_args, self.write(blocked, "rerun-result.json"))
        self.assertIn("a carried-over task must be PASS, got 'BLOCKED'", json.loads(proc.stdout)["tasks"]["a"]["problems"])

    def move_head(self, message: str) -> str:
        """Add an empty commit on HEAD without committing the staged change; return its sha."""
        commit = git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", message, cwd=self.wt).strip()
        git("update-ref", "HEAD", commit, cwd=self.wt)
        return commit

    def gate_and_finish(self, label: str) -> dict:
        """Gate and export task a's current staged state; return its PASS entry."""
        rc, gate = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", label, "--worktree", self.wt,
                            "--", *self.gate_commands)
        self.assertEqual(rc, 0, gate)
        rc, finish = run_task("finish", "--worktree", self.wt, "--patch", self.args["tasks"][0]["patch"], "--scope", "a.txt")
        self.assertEqual(rc, 0, finish)
        return dict(self.result["tasks"][0], gate=gate, sha256=finish["sha256"], tree=finish["tree"], files=finish["files"])

    def test_work_committed_inside_the_worktree_fails(self):
        start = self.args["tasks"][0]["start_head"]
        git("commit", "-q", "-m", "part of the work", cwd=self.wt)
        Path(self.wt, "a.txt").write_text("alpha changed twice\n")
        git("add", "-A", cwd=self.wt)
        self.result["tasks"][0] = self.gate_and_finish("task-a-L0r1")
        head = git("rev-parse", "HEAD", cwd=self.wt).strip()
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], [
            f"gate state head {head} is neither the task's start_head {start} nor a scaffolding commit on it: "
            "work committed inside the task worktree is missing from the patch"])
        # a commit that only borrows the scaffolding subject, on top of the partial commit, fails too
        self.move_head("scaffolding (temporary)")
        self.result["tasks"][0] = self.gate_and_finish("task-a-L0r2")
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertIn("nor a scaffolding commit on it", out["tasks"]["a"]["problems"][0])

    def test_scaffolded_task_passes(self):
        b = self.args["tasks"][1]
        rc, scaffold = run_task("scaffold", "--worktree", b["worktree"], "--patch", self.result["tasks"][0]["patch"])
        self.assertEqual(rc, 0, scaffold)
        self.assertNotEqual(scaffold["head"], b["start_head"])
        Path(b["worktree"], "b.txt").write_text("beta changed\n")
        git("add", "-A", cwd=b["worktree"])
        rc, gate = run_task("gate", "--run-dir", self.root / "run" / "gates", "--label", "task-b-L0r0", "--worktree", b["worktree"],
                            "--", *b["gate"])
        self.assertEqual(rc, 0, gate)
        rc, finish = run_task("finish", "--worktree", b["worktree"], "--patch", b["patch"], "--scope", "b.txt")
        self.assertEqual(rc, 0, finish)
        self.result["tasks"][1] = {"id": "b", "status": "PASS", "reason": "", "tier": 2, "level": 0, "rounds": 0, "gate": gate,
                                   "reviews": [], "patch": finish["patch"], "sha256": finish["sha256"], "tree": finish["tree"],
                                   "files": finish["files"], "history": []}
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["tasks"]["b"], {"ok": True, "status": "PASS", "problems": []})

    def test_head_moved_after_the_gate_fails(self):
        head = self.move_head("after the gate")
        rc, out = self.verify(self.result)
        self.assertEqual(rc, 1)
        self.assertEqual(out["tasks"]["a"]["problems"], [f"worktree HEAD {head} differs from the gate state head {self.gate['head']}"])

    def test_arguments_without_start_head_fail(self):
        args = json.loads(self.args_path.read_text())
        del args["tasks"][0]["start_head"]
        proc = run_plan("verify-result", self.write(args, "old-args.json"), self.write(self.result, "result.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["tasks"]["a"]["problems"], ["the workflow arguments carry no start_head for this task"])

    def test_report_table(self):
        self.result["tasks"].append({"id": "x", "status": "BLOCKED", "reason": "not in plan"})
        self.result["tasks"][1] = {"id": "b", "status": "ESCALATE", "reason": "tier 3 did not converge",
                                   "history": [{"level": 1, "round": 2, "gate_exit": 0, "defects": [{}]}]}
        path = self.write(self.result, "result.json")
        proc = run_plan("report", self.args_path, path)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], "| # | Task | Tier | Rounds | Gate | Review | Status | Evidence |")
        sha = self.result["tasks"][0]["sha256"][:12]
        tree = self.result["tasks"][0]["tree"][:12]
        self.assertEqual(lines[2], f"| a | task a | 2 -> 3 | 1 | pass task-a-L0r0 | conformance ({model('claude', 3)}) PASS | PASS | "
                                   f"task-a.patch sha256 {sha}, 1 files, tree {tree} |")
        self.assertEqual(lines[3], "| b | task b | 2 -> 3 | - | pass | - | ESCALATE | tier 3 did not converge |")
        self.assertEqual(lines[4], "| x | x | - | - | - | - | BLOCKED | not in plan |")


class SchemaTests(unittest.TestCase):
    EXAMPLES = {
        "report": {"files_changed": [{"path": "a.txt", "change": "edited"}], "gate": [{"command": "make test", "exit_code": 0, "tail": "ok"}],
                   "tests": [{"name": "test_guard", "fails_if": "the guard is removed"}], "not_done": [], "questions": ["why?"]},
        "verdict": {"verdict": "FAIL", "defects": [{"file": "a.py", "line": 3, "kind": "test-gap", "severity": "major", "summary": "s", "scenario": "x"},
                                                   {"file": "b.py", "kind": "docs", "severity": "minor", "summary": "s", "scenario": "x"}],
                    "notes": [], "gate": [{"command": "make test", "exit_code": 1, "tail": "FAILED"}]},
        "gate": {"attempt_dir": "/run/gates/task-a", "exit_code": 1, "results": [{"command": "false", "rc": 1, "outcome": "exit 1", "log": "task-a/1.log", "seconds": 0.1}],
                 "tail": "boom", "tree": "abc", "head": "def", "dirty": False,
                 "unstaged": [], "untracked": [], "outside_scope": []},
        "finish": {"patch": "/run/patches/task-a.patch", "sha256": "00", "files": ["a.txt"], "tree": "abc", "verify_clean_exit": 0, "output": "clean"},
        "scaffold": {"exit_code": 0, "output": "head abc", "head": "abc"},
        "confirm": {"defects": [{"index": 0, "confirmed": False, "evidence": "guarded at a.py:3"}]},
    }

    def schema(self, name):
        return json.loads((SCHEMAS / f"{name}.schema.json").read_text())

    def test_schema_files_follow_the_structural_rules(self):
        self.assertEqual(sorted(p.name for p in SCHEMAS.iterdir()), sorted(f"{n}.schema.json" for n in self.EXAMPLES))
        for name in self.EXAMPLES:
            with self.subTest(schema=name):
                self.assertEqual(planmod.schema_problems(self.schema(name)), [])

    def test_structural_rules_catch_bad_schemas(self):
        self.assertIn("$: the root must have type 'object'", planmod.schema_problems({"type": "array", "items": {"type": "string"}}))
        self.assertIn("$: required 'b' is not in properties",
                      planmod.schema_problems({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]}))
        self.assertIn("$.a: unsupported keyword 'pattern'",
                      planmod.schema_problems({"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}}))
        self.assertIn("$.a: unknown type 'text'", planmod.schema_problems({"type": "object", "properties": {"a": {"type": "text"}}}))

    def test_required_fields_match_the_contract(self):
        self.assertEqual(self.schema("report")["required"], ["files_changed", "gate", "tests", "not_done", "questions"])
        self.assertEqual(self.schema("verdict")["required"], ["verdict", "defects", "notes", "gate"])
        self.assertEqual(self.schema("verdict")["properties"]["defects"]["items"]["required"], ["file", "kind", "severity", "summary", "scenario"])
        self.assertEqual(self.schema("gate")["required"], ["attempt_dir", "exit_code", "results", "tail", "tree", "head", "dirty",
                                                           "unstaged", "untracked", "outside_scope"])
        self.assertEqual(self.schema("finish")["required"], ["patch", "sha256", "files", "tree", "verify_clean_exit", "output"])
        self.assertEqual(self.schema("scaffold")["required"], ["exit_code", "output", "head"])
        self.assertEqual(self.schema("confirm")["required"], ["defects"])
        self.assertEqual(self.schema("verdict")["properties"]["gate"], self.schema("report")["properties"]["gate"])

    def test_examples_validate(self):
        for name, example in self.EXAMPLES.items():
            with self.subTest(schema=name):
                self.assertEqual(planmod.validate(example, self.schema(name)), [])

    def test_violations_are_reported_with_paths(self):
        verdict = copy.deepcopy(self.EXAMPLES["verdict"])
        verdict["verdict"] = "MAYBE"
        verdict["defects"][0]["line"] = "3"
        verdict["defects"][1]["severity"] = "critical"
        del verdict["notes"]
        errors = planmod.validate(verdict, self.schema("verdict"))
        self.assertIn("$: missing required property 'notes'", errors)
        self.assertIn("$.verdict: 'MAYBE' is not one of ['PASS', 'FAIL']", errors)
        self.assertIn("$.defects[0].line: expected integer, got string", errors)
        self.assertIn("$.defects[1].severity: 'critical' is not one of ['blocker', 'major', 'minor']", errors)
        self.assertEqual(len(errors), 4, errors)

    def test_booleans_are_not_integers_and_integers_are_not_booleans(self):
        gate = dict(self.EXAMPLES["gate"], exit_code=True, dirty=0)
        self.assertEqual(planmod.validate(gate, self.schema("gate")),
                         ["$.exit_code: expected integer, got boolean", "$.dirty: expected boolean, got integer"])
        self.assertEqual(planmod.validate([], self.schema("confirm")), ["$: expected object, got array"])


if __name__ == "__main__":
    unittest.main()
