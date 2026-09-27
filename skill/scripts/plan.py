#!/usr/bin/env python3
"""Validate an orchestrate plan, render it, derive the execution loop's arguments, and check its result.

Usage:
  plan.py check PLAN [--check-files]
  plan.py render PLAN
  plan.py workflow-args PLAN [--skill-dir DIR] [--agents-dir DIR] [--only ID ...] [--previous RESULT]
  plan.py verify-result ARGS RESULT
  plan.py report ARGS RESULT

`check` validates plan.json (schema `orchestrate-plan/v1`) and prints {"ok": true, "order": [...],
"waves": [[...], ...]} (exit 0) or {"ok": false, "errors": [...]} (exit 1). Every error is
collected. `order` is a topological order; `waves` are the dependency levels. With
`--check-files`, `repo` must be a Git work tree, `run_dir` must exist and every spec file must
exist. Plan format:

  {"schema": "orchestrate-plan/v1", "goal": "...", "harness": "claude" | "codex",
   "repo": "/abs", "base": "main", "run_dir": "/abs", "worktree_parent": "/abs",
   "branch_prefix": "feature/run",
   "tasks": [{"id": "t1", "title": "...", "spec": "task-t1.md", "scope": ["lib/a.dart"],
              "tier": 2, "depends_on": [], "gate": ["..."], "risk": ["async"],
              "reviewer_tier": 3, "worktree": "/abs/optional-override"}]}

  `spec` is relative to `run_dir` unless absolute. `scope` holds literal repo-relative paths (a
  trailing `/` is dropped). `reviewer_tier` defaults to the harness's `reviewer_default_tier` in
  routing.json and may not be below `tier`. `depends_on` and `risk` default to []. Tasks whose
  scopes overlap (equal paths, or one a directory prefix of the other) must be ordered by the
  dependency graph. A gate command is one line: no newline, carriage return or NUL. Each task's
  effective worktree (the override, or `<worktree_parent>/<branch_prefix with / as ->-<id>`) must
  differ from every other task's, must not be nested in another one, and must neither be, lie
  inside, nor contain `repo`.

`render` prints the plan table in topological order and a cost line (Markdown).

`workflow-args` prints the arguments of the execution loop (schema `orchestrate-execute-args/v1`):
version, harness, run_dir, repo, base, scripts, reviewer_brief, limits, utility, agent_types,
role_text, schemas, adversary_variations and the tasks in topological order, each with its
worktree, branch, spec (absolute), spec_sha256 (a missing spec file is an error), scope,
depends_on, scaffold_from (all transitive dependencies in topological order), gate, risk, chain,
lenses, confirm, patch and gate_label_prefix. Every task that will run (not `done`) also gets
`start_head`, its worktree's HEAD at this moment, and `worktree_id` (see `worktree_id()` for
the bytes hashed), so the worktree must already exist: prepare the worktrees first.
`--only ID` (repeatable) reruns part of a plan: the selected tasks are emitted as usual, every
task a selected one needs (transitively) but that is not selected is emitted with
`"done": true, "previous": <its entry in --previous>` added to its fields, and every other task is
left out. Each such dependency must be PASS in `--previous RESULT` (an execute result) with a
patch that still exists, else it is an error naming the task. `--only` without `--previous` is
allowed only when the selected tasks need no unselected task; `--previous` needs `--only`. When a
selected task depends on another selected task, a warning goes to stderr and into `warnings` (a
list, empty otherwise): if the dependency's patch changes, the dependent's worktree must be
recreated, because `integrate.sh scaffold` refuses to stack on stale scaffolding.
`--skill-dir` (default: the directory above this script) supplies SKILL.md, routing.json,
schemas/, scripts/task.py and the role files; `--agents-dir` (default ~/.claude/agents) is where
Claude Code roles are installed.

`verify-result` re-checks every PASS task of an execute result against the files: the patch
exists and its sha256 matches, `<gate.attempt_dir>/result.json` ran exactly the planned gate
commands in order and every one exited 0, the gate's `tree` equals the result's `tree`, the
attempt directory lies directly in `<run_dir>/gates` and is named `<gate_label_prefix>-...`, its
state.json records the result's `tree` and the task's worktree with empty `unstaged`,
`untracked` and `outside_scope` lists and a `head` that is the task's `start_head` or a
"scaffolding (temporary)" commit whose only parent is `start_head` (so work the implementer
committed fails), the worktree's HEAD still equals that `head`, `integrate.sh verify-clean
<worktree> <patch> --scope ...` passes, and `git write-tree` of the worktree equals the result's
`tree`. Tasks marked `done` in ARGS are checked the same way (from the result, or from their
`previous` entry when the result does not list them), except for `start_head`, which carried
tasks do not have. Prints {"ok", "tasks": {id: {"ok", "status", "problems"}}}; exit 1 on any
problem. Other non-PASS tasks are listed, not checked.

`report` prints the wave table `| # | Task | Tier | Rounds | Gate | Review | Status | Evidence |`.

Execute result format (written by the workflow script and by the Codex driver):

  {"schema": "orchestrate-execute-result/v1", "version": "<skill version>",
   "tasks": [{"id", "status": "PASS" | "ESCALATE" | "BLOCKED" | "SKIPPED", "reason",
              "tier", "level", "rounds", "gate": <gate object>,
              "reviews": [{"lens", "model", "verdict", "defects": <int>}],
              "patch", "sha256", "tree", "files",
              "history": [{"level", "round", "gate_exit", "defects": [...]}]}]}

  `tier` is the tier that produced the result and `level` its index in the task's chain; the
  fields after `reason` may be absent for a task that did not pass.

`validate(instance, schema)` checks an object against the JSON Schema subset the result schemas
use (type, properties, required, items, enum) and returns a list of error strings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLAN_SCHEMA = "orchestrate-plan/v1"
ARGS_SCHEMA = "orchestrate-execute-args/v1"
RESULT_SCHEMA = "orchestrate-execute-result/v1"
HARNESSES = ("claude", "codex")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_TASKS = 30
TIERS = (1, 2, 3)
STATUSES = ("PASS", "ESCALATE", "BLOCKED", "SKIPPED")
SCHEMA_NAMES = ("report", "verdict", "gate", "finish", "scaffold", "confirm")
ROLES = {"implementer": "orchestrate-implementer", "reviewer": "orchestrate-reviewer"}
DEFAULT_SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_AGENTS_DIR = "~/.claude/agents"


class PlanError(Exception):
    """A plan or input file that cannot be used; carries every error found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ---------------------------------------------------------------- schema subset

SCHEMA_KEYWORDS = {"type", "properties", "required", "items", "enum"}
TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_name(value) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if TYPE_CHECKS[name](value):
            return name
    return type(value).__name__


def _same(a, b) -> bool:
    return type(a) is type(b) and a == b


def validate(instance, schema: dict, path: str = "$") -> list[str]:
    """Validate `instance` against the schema subset; return one message per violation."""
    errors: list[str] = []
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(TYPE_CHECKS.get(name, lambda v: False)(instance) for name in names):
            return [f"{path}: expected {' or '.join(names)}, got {_type_name(instance)}"]
    if "enum" in schema and not any(_same(instance, option) for option in schema["enum"]):
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    return errors


def schema_problems(schema, path: str = "$", root: bool = True) -> list[str]:
    """Structural rules for a result schema: subset keywords only, root object, required within properties."""
    if not isinstance(schema, dict):
        return [f"{path}: a schema node must be an object"]
    problems = [f"{path}: unsupported keyword '{key}'" for key in sorted(set(schema) - SCHEMA_KEYWORDS)]
    types = schema.get("type")
    names = [types] if isinstance(types, str) else types
    if types is not None and (not isinstance(names, list) or not names or any(n not in TYPE_CHECKS for n in names)):
        problems.append(f"{path}: unknown type {types!r}")
    if root and types != "object":
        problems.append(f"{path}: the root must have type 'object'")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        problems.append(f"{path}: properties must be an object")
        properties = {}
    for key, sub in properties.items():
        problems.extend(schema_problems(sub, f"{path}.{key}", root=False))
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
        problems.append(f"{path}: required must be a list of names")
    else:
        problems.extend(f"{path}: required '{key}' is not in properties" for key in required if key not in properties)
    if "items" in schema:
        problems.extend(schema_problems(schema["items"], f"{path}[]", root=False))
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        problems.append(f"{path}: enum must be a non-empty list")
    return problems


# ---------------------------------------------------------------- plan checks

def load_json(path: str, what: str):
    try:
        return json.loads(Path(path).read_text())
    except OSError as error:
        raise PlanError([f"cannot read {what} {path}: {error.strerror or error}"])
    except json.JSONDecodeError as error:
        raise PlanError([f"{what} {path} is not valid JSON: {error}"])


def load_routing(skill_dir: Path) -> dict:
    return load_json(str(Path(skill_dir) / "routing.json"), "routing table")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def scope_problem(path) -> str | None:
    """Why `path` is not a literal repo-relative path, or None."""
    if not isinstance(path, str) or not path:
        return "must be a non-empty string"
    if path.startswith("/"):
        return "must be repo-relative, not absolute"
    if any(char in path for char in "*?["):
        return "must be a literal path, not a glob"
    if path.startswith(":"):
        return "must not start with ':' (pathspec magic)"
    parts = path.rstrip("/").split("/")
    if parts in ([""], ["."]):
        return "must not be the repository root"
    if ".." in parts:
        return "must not contain a '..' component"
    if ".git" in parts:
        return "must not contain a '.git' component"
    if "" in parts or "." in parts:
        return "must not contain an empty or '.' component"
    return None


def _overlap(a: str, b: str) -> bool:
    return a == b or b.startswith(a + "/") or a.startswith(b + "/")


def _find_cycles(graph: dict[str, list[str]], ids: list[str]) -> list[list[str]]:
    """Every distinct cycle reached by a depth-first search, each as [a, b, ..., a]."""
    cycles: list[list[str]] = []
    seen_keys: set[tuple[str, ...]] = set()
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str):
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                cycle = stack[stack.index(dep):]
                start = cycle.index(min(cycle))
                key = tuple(cycle[start:] + cycle[:start])
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(list(key) + [key[0]])
            elif dep not in state:
                visit(dep)
        stack.pop()
        state[node] = 2

    for node in ids:
        if node not in state:
            visit(node)
    return cycles


def _ancestors(graph: dict[str, list[str]], node: str) -> set[str]:
    found: set[str] = set()
    todo = list(graph.get(node, []))
    while todo:
        dep = todo.pop()
        if dep not in found:
            found.add(dep)
            todo.extend(graph.get(dep, []))
    return found


def check_plan(plan, routing: dict, check_files: bool = False) -> dict:
    """Validate a plan. Returns {"errors", "tasks", "order", "waves", "ancestors"}; tasks are normalized."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return {"errors": ["the plan must be a JSON object"], "tasks": [], "order": [], "waves": [], "ancestors": {}}
    if plan.get("schema") != PLAN_SCHEMA:
        errors.append(f"schema must be '{PLAN_SCHEMA}', got {plan.get('schema')!r}")
    harness = plan.get("harness")
    if harness not in HARNESSES:
        errors.append(f"harness must be one of {list(HARNESSES)}, got {harness!r}")
    if not _text(plan.get("goal")):
        errors.append("goal must be a non-empty string")
    for key in ("repo", "run_dir", "worktree_parent"):
        value = plan.get(key)
        if not isinstance(value, str) or not os.path.isabs(value):
            errors.append(f"{key} must be an absolute path, got {value!r}")
    if not _text(plan.get("base")):
        errors.append("base must be a non-empty string")
    prefix = plan.get("branch_prefix")
    if not _text(prefix) or any(ch.isspace() for ch in prefix):
        errors.append(f"branch_prefix must be non-empty and without spaces, got {prefix!r}")

    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_TASKS:
        count = len(raw_tasks) if isinstance(raw_tasks, list) else "no list of"
        errors.append(f"tasks must be a list of 1 to {MAX_TASKS} tasks, got {count}")
        raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []

    harness_routing = (routing.get(harness) if harness in HARNESSES else None) or routing.get("claude") or {}
    default_reviewer = harness_routing.get("reviewer_default_tier", 3)
    risk_tags = set(routing.get("risk_tags", []))
    tasks: list[dict] = []
    ids_seen: set[str] = set()
    for position, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            errors.append(f"task #{position}: must be an object")
            continue
        tid = raw.get("id")
        label = f"task {tid}" if isinstance(tid, str) and tid else f"task #{position}"
        valid_id = isinstance(tid, str) and bool(ID_RE.fullmatch(tid))
        if not valid_id:
            errors.append(f"{label}: id must match {ID_RE.pattern}, got {tid!r}")
        elif tid in ids_seen:
            errors.append(f"{label}: duplicate id")
            valid_id = False
        else:
            ids_seen.add(tid)
        if not _text(raw.get("title")):
            errors.append(f"{label}: title must be a non-empty string")
        if not _text(raw.get("spec")):
            errors.append(f"{label}: spec must be a non-empty string")
        scope = raw.get("scope")
        normalized: list[str] = []
        if not isinstance(scope, list) or not scope:
            errors.append(f"{label}: scope must be a non-empty list of paths")
        else:
            for path in scope:
                problem = scope_problem(path)
                if problem:
                    errors.append(f"{label}: scope path {path!r} {problem}")
                else:
                    normalized.append(path.rstrip("/"))
        tier = raw.get("tier")
        if not _is_int(tier) or tier not in TIERS:
            errors.append(f"{label}: tier must be 1, 2 or 3, got {tier!r}")
            tier = None
        reviewer_tier = raw.get("reviewer_tier", default_reviewer)
        if reviewer_tier is None:
            reviewer_tier = default_reviewer
        if not _is_int(reviewer_tier) or reviewer_tier not in TIERS:
            errors.append(f"{label}: reviewer_tier must be 1, 2 or 3, got {reviewer_tier!r}")
        elif tier is not None and reviewer_tier < tier:
            errors.append(f"{label}: reviewer_tier {reviewer_tier} is below the implementer tier {tier}")
        depends_on = raw.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
            errors.append(f"{label}: depends_on must be a list of task ids")
            depends_on = []
        gate = raw.get("gate")
        if not isinstance(gate, list) or not gate or not all(_text(cmd) for cmd in gate):
            errors.append(f"{label}: gate must be a non-empty list of non-empty commands")
        else:
            for number, cmd in enumerate(gate, start=1):
                if any(char in cmd for char in "\n\r\0"):
                    errors.append(f"{label}: gate command {number} contains a newline, carriage return or NUL; "
                                  "use one list entry per command")
        risk = raw.get("risk", [])
        if not isinstance(risk, list) or not all(isinstance(r, str) for r in risk):
            errors.append(f"{label}: risk must be a list of tags")
            risk = []
        unknown_risk = [r for r in risk if r not in risk_tags]
        if unknown_risk:
            errors.append(f"{label}: unknown risk tags {unknown_risk}; allowed: {sorted(risk_tags)}")
        worktree = raw.get("worktree")
        if worktree is not None and (not isinstance(worktree, str) or not os.path.isabs(worktree)):
            errors.append(f"{label}: worktree override must be an absolute path, got {worktree!r}")
        if valid_id:
            tasks.append({**raw, "scope": normalized, "reviewer_tier": reviewer_tier,
                          "depends_on": list(depends_on), "risk": list(risk)})

    ids = [t["id"] for t in tasks]
    known = set(ids)
    graph: dict[str, list[str]] = {}
    for t in tasks:
        deps: list[str] = []
        for dep in t["depends_on"]:
            if dep == t["id"]:
                errors.append(f"task {t['id']}: depends on itself")
            elif dep not in known:
                errors.append(f"task {t['id']}: depends on unknown task {dep!r}")
            elif dep not in deps:
                deps.append(dep)
        graph[t["id"]] = deps
    cycles = _find_cycles(graph, ids)
    for cycle in cycles:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    ancestors = {tid: _ancestors(graph, tid) for tid in ids}

    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            if b["id"] in ancestors[a["id"]] or a["id"] in ancestors[b["id"]]:
                continue
            pairs = [f"{p} ~ {q}" for p in a["scope"] for q in b["scope"] if _overlap(p, q)]
            if pairs:
                errors.append(f"tasks {a['id']} and {b['id']} have overlapping scopes ({', '.join(pairs)}) "
                              "but neither depends on the other; serialize them with depends_on")

    waves: list[list[str]] = []
    order: list[str] = []
    if not cycles:
        level: dict[str, int] = {}

        def depth(tid: str) -> int:
            if tid not in level:
                level[tid] = 1 + max((depth(dep) for dep in graph[tid]), default=-1)
            return level[tid]

        for tid in ids:
            depth(tid)
        for n in range(max(level.values(), default=-1) + 1):
            waves.append([tid for tid in ids if level[tid] == n])
        order = [tid for wave in waves for tid in wave]

    errors.extend(_worktree_errors(plan, tasks))
    if check_files:
        errors.extend(_file_errors(plan, tasks))
    return {"errors": errors, "tasks": tasks, "order": order, "waves": waves,
            "ancestors": {tid: [x for x in order if x in found] for tid, found in ancestors.items()}}


def _inside(child: str, parent: str) -> bool:
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def effective_worktree(plan: dict, task: dict) -> str | None:
    """The task's worktree: its override, or `<worktree_parent>/<branch_prefix with / as ->-<id>`."""
    override = task.get("worktree")
    if override is not None:
        return os.path.normpath(override) if isinstance(override, str) and os.path.isabs(override) else None
    parent, prefix = plan.get("worktree_parent"), plan.get("branch_prefix")
    if not isinstance(parent, str) or not os.path.isabs(parent) or not _text(prefix):
        return None
    return os.path.normpath(os.path.join(parent, f"{prefix.replace('/', '-')}-{task['id']}"))


def _worktree_errors(plan: dict, tasks: list[dict]) -> list[str]:
    """Worktrees must be distinct, apart from the repository, and not nested in each other."""
    errors: list[str] = []
    repo = plan.get("repo")
    repo = os.path.normpath(repo) if isinstance(repo, str) and os.path.isabs(repo) else None
    worktrees = [(t["id"], wt) for t in tasks if (wt := effective_worktree(plan, t))]
    for tid, wt in worktrees:
        if repo and wt == repo:
            errors.append(f"task {tid}: worktree {wt} is the repository itself")
        elif repo and _inside(wt, repo):
            errors.append(f"task {tid}: worktree {wt} is inside the repository {repo}")
        elif repo and _inside(repo, wt):
            errors.append(f"task {tid}: worktree {wt} contains the repository {repo}")
    for i, (a, wa) in enumerate(worktrees):
        for b, wb in worktrees[i + 1:]:
            if wa == wb:
                errors.append(f"tasks {a} and {b} share the worktree {wa}")
            elif _inside(wb, wa):
                errors.append(f"task {b}: worktree {wb} is nested in the worktree of task {a} ({wa})")
            elif _inside(wa, wb):
                errors.append(f"task {a}: worktree {wa} is nested in the worktree of task {b} ({wb})")
    return errors


def _file_errors(plan: dict, tasks: list[dict]) -> list[str]:
    errors: list[str] = []
    repo = plan.get("repo")
    if isinstance(repo, str) and os.path.isabs(repo):
        proc = subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            errors.append(f"repo {repo} is not a Git work tree")
    run_dir = plan.get("run_dir")
    if isinstance(run_dir, str) and os.path.isabs(run_dir):
        if not os.path.isdir(run_dir):
            errors.append(f"run_dir {run_dir} does not exist")
        for t in tasks:
            if _text(t.get("spec")):
                spec = spec_path(run_dir, t["spec"])
                if not os.path.isfile(spec):
                    errors.append(f"task {t['id']}: spec file {spec} does not exist")
    return errors


def spec_path(run_dir: str, spec: str) -> str:
    return os.path.normpath(spec if os.path.isabs(spec) else os.path.join(run_dir, spec))


def checked_plan(path: str, routing: dict, check_files: bool = False) -> tuple[dict, dict]:
    plan = load_json(path, "plan")
    result = check_plan(plan, routing, check_files)
    if result["errors"]:
        raise PlanError(result["errors"])
    return plan, result


# ---------------------------------------------------------------- routing per task

def _step(harness_routing: dict, tier: int) -> dict:
    entry = harness_routing["tiers"][str(tier)]
    return {"model": entry["model"], "effort": entry.get("effort")}


def chain_for(harness_routing: dict, tier: int) -> list[dict]:
    return [{"tier": t, **_step(harness_routing, t)} for t in range(tier, 4)]


def lenses_for(harness_routing: dict, routing: dict, reviewer_tier: int, risk: list[str]) -> list[dict]:
    conformance = _step(harness_routing, reviewer_tier)
    lenses = [{"key": "conformance", **conformance}]
    if set(risk) & set(routing.get("risky_tags", [])):
        adversary = harness_routing["adversary"]
        adversary = {"model": adversary["model"], "effort": adversary.get("effort")}
        if adversary["model"] == conformance["model"]:
            top = _step(harness_routing, 3)
            adversary = _step(harness_routing, 2) if conformance["model"] == top["model"] else top
        lenses.append({"key": "adversary", **adversary})
    return lenses


# ---------------------------------------------------------------- render

def _cell(value: str) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _code(value: str) -> str:
    return f"`` {value} ``" if "`" in value else f"`{value}`"


def render(plan: dict, info: dict, routing: dict) -> str:
    harness_routing = routing[plan["harness"]]
    by_id = {t["id"]: t for t in info["tasks"]}
    lines = ["| # | Task | Scope | Tier | Depends on | Gate | Reviewer tier |",
             "|---|------|-------|------|------------|------|---------------|"]
    implementers: dict[int, int] = {}
    reviewers: dict[int, int] = {}
    adversaries = 0
    for tid in info["order"]:
        t = by_id[tid]
        lines.append("| " + " | ".join(_cell(c) for c in (
            tid, t["title"], ", ".join(_code(p) for p in t["scope"]), t["tier"],
            ", ".join(t["depends_on"]) or "-", "; ".join(_code(g) for g in t["gate"]), t["reviewer_tier"])) + " |")
        implementers[t["tier"]] = implementers.get(t["tier"], 0) + 1
        reviewers[t["reviewer_tier"]] = reviewers.get(t["reviewer_tier"], 0) + 1
        if len(lenses_for(harness_routing, routing, t["reviewer_tier"], t["risk"])) > 1:
            adversaries += 1

    def per_tier(counts: dict[int, int]) -> str:
        return ", ".join(f"tier {tier} ({harness_routing['tiers'][str(tier)]['model']}) x{counts[tier]}"
                         for tier in sorted(counts))

    lines.append("")
    lines.append(f"Cost: implementers {per_tier(implementers)}; reviewers {per_tier(reviewers)}; "
                 f"adversary reviewers x{adversaries}.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- workflow-args

SCAFFOLD_SUBJECT = "scaffolding (temporary)"


def _git(worktree: str, *args: str) -> str | None:
    proc = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def worktree_id(path: str) -> str:
    """Identify one worktree instance, so a removed and recreated worktree gets a new id.

    The hex sha256 of these bytes: the worktree's absolute path (os.path.abspath, which
    normalizes but does not resolve symlinks; Node's path.resolve does the same) encoded as
    UTF-8, one NUL byte, the decimal ASCII st_ino of `<path>/.git`, one NUL byte, and the decimal
    ASCII st_ctime_ns of `<path>/.git` (stat follows symlinks; in Node, fs.statSync(p,
    {bigint: true}).ino and .ctimeNs). For a linked worktree `.git` is the small file that points
    at the repository, created anew with every `git worktree add`.
    """
    absolute = os.path.abspath(path)
    st = os.stat(os.path.join(absolute, ".git"))
    data = absolute.encode("utf-8") + b"\0" + str(st.st_ino).encode("ascii") + b"\0" + str(st.st_ctime_ns).encode("ascii")
    return hashlib.sha256(data).hexdigest()


def skill_version(skill_md: Path) -> str:
    try:
        text = skill_md.read_text()
    except OSError:
        raise PlanError([f"cannot read {skill_md}"])
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PlanError([f"{skill_md} has no frontmatter"])
    in_metadata = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if re.match(r"^metadata:\s*$", line):
            in_metadata = True
            continue
        if in_metadata and line and not line[0].isspace():
            in_metadata = False
        match = re.match(r"^\s+version:\s*(\S+)\s*$", line)
        if in_metadata and match:
            return match.group(1).strip("'\"")
    raise PlanError([f"{skill_md} has no metadata.version"])


def role_body(path: Path) -> str:
    """The role file's text without its frontmatter; empty when the file is missing."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1:]
                break
    return "\n".join(lines).strip()


def select_tasks(info: dict, only: list[str] | None, previous: dict | None,
                 previous_name: str) -> tuple[list[str], dict, list[str]]:
    """The task ids to emit (topological order), the carried-over PASS entries of unselected
    dependencies, and warnings about selected tasks that depend on other selected tasks."""
    if only is None:
        if previous is not None:
            raise PlanError(["--previous needs --only: without a selection every task runs"])
        return list(info["order"]), {}, []
    known = set(info["order"])
    errors = [f"--only names unknown task {tid!r}" for tid in only if tid not in known]
    selected = {tid for tid in only if tid in known}
    needed = set().union(*(set(info["ancestors"][tid]) for tid in selected)) - selected
    earlier: dict[str, dict] = {}
    if previous is not None:
        if previous.get("schema") != RESULT_SCHEMA or not isinstance(previous.get("tasks"), list):
            errors.append(f"{previous_name} is not an execute result ({RESULT_SCHEMA})")
        else:
            earlier = {e.get("id"): e for e in previous["tasks"] if isinstance(e, dict)}
    carried: dict[str, dict] = {}
    for tid in info["order"]:
        if tid not in needed:
            continue
        users = sorted(t for t in selected if tid in info["ancestors"][t])
        if previous is None:
            errors.append(f"task {tid} is needed by {', '.join(users)} but not selected; select it or pass --previous "
                          "with its PASS result")
            continue
        entry = earlier.get(tid)
        patch = entry.get("patch") if entry else None
        if not entry or entry.get("status") != "PASS" or not isinstance(patch, str) or not patch:
            errors.append(f"task {tid} is needed by {', '.join(users)} but has no PASS result with a patch in {previous_name}")
        elif not os.path.isfile(patch):
            errors.append(f"task {tid} is needed by {', '.join(users)} but its previous patch {patch} does not exist")
        else:
            carried[tid] = entry
    if errors:
        raise PlanError(errors)
    warnings = []
    for tid in info["order"]:
        upstream = [dep for dep in info["ancestors"][tid] if dep in selected] if tid in selected else []
        if upstream:
            warnings.append(f"task {tid} depends on selected task {', '.join(upstream)}: if that patch changes in this "
                            f"run, recreate the worktree of task {tid} before it is scaffolded again (scaffolding is "
                            "never stacked on stale scaffolding)")
    return [tid for tid in info["order"] if tid in selected or tid in needed], carried, warnings


def workflow_args(plan: dict, info: dict, routing: dict, skill_dir: Path, agents_dir: Path,
                  only: list[str] | None = None, previous: dict | None = None, previous_name: str = "--previous") -> dict:
    harness = plan["harness"]
    harness_routing = routing[harness]
    run_dir = plan["run_dir"]
    if harness == "claude":
        agent_types = {key: (name if (agents_dir / f"{name}.md").is_file() else None) for key, name in ROLES.items()}
    else:
        agent_types = dict(ROLES)
    role_text = {key: ("" if agent_types[key] else role_body(skill_dir / "claude" / "agents" / f"{name}.md"))
                 for key, name in ROLES.items()}
    schemas = {name: load_json(str(skill_dir / "schemas" / f"{name}.schema.json"), "schema") for name in SCHEMA_NAMES}
    dir_prefix = plan["branch_prefix"].replace("/", "-")
    by_id = {t["id"]: t for t in info["tasks"]}
    emitted, carried, warnings = select_tasks(info, only, previous, previous_name)
    specs = {tid: spec_path(run_dir, by_id[tid]["spec"]) for tid in emitted}
    missing = [f"task {tid}: spec file {path} does not exist" for tid, path in specs.items() if not os.path.isfile(path)]
    if missing:
        raise PlanError(missing)
    tasks = []
    for tid in emitted:
        t = by_id[tid]
        lenses = lenses_for(harness_routing, routing, t["reviewer_tier"], t["risk"])
        tasks.append({
            "id": tid,
            "title": t["title"],
            "spec": specs[tid],
            "spec_sha256": _sha256(specs[tid]),
            "worktree": t.get("worktree") or os.path.join(plan["worktree_parent"], f"{dir_prefix}-{tid}"),
            "branch": f"{plan['branch_prefix']}/{tid}",
            "scope": t["scope"],
            "depends_on": t["depends_on"],
            "scaffold_from": info["ancestors"][tid],
            "gate": t["gate"],
            "risk": t["risk"],
            "chain": chain_for(harness_routing, t["tier"]),
            "lenses": lenses,
            "confirm": {"model": lenses[0]["model"], "effort": lenses[0]["effort"]},
            "patch": os.path.join(run_dir, "patches", f"task-{tid}.patch"),
            "gate_label_prefix": f"task-{tid}",
        })
        if tid in carried:
            tasks[-1].update({"done": True, "previous": carried[tid]})
    missing = []
    for entry in tasks:
        if entry.get("done"):
            continue
        wt = entry["worktree"]
        start = _git(wt, "rev-parse", "--verify", "HEAD") if os.path.exists(os.path.join(wt, ".git")) else None
        if not start:
            missing.append(f"task {entry['id']}: worktree {wt} does not exist or has no commit; prepare the worktrees "
                           "first (scripts/prepare_worktrees.sh), then run workflow-args")
            continue
        entry["start_head"] = start
        entry["worktree_id"] = worktree_id(wt)
    if missing:
        raise PlanError(missing)
    return {
        "schema": ARGS_SCHEMA,
        "version": skill_version(skill_dir / "SKILL.md"),
        "harness": harness,
        "run_dir": run_dir,
        "repo": plan["repo"],
        "base": plan["base"],
        "scripts": {"task": str(skill_dir / "scripts" / "task.py"), "python": "python3"},
        "reviewer_brief": os.path.join(run_dir, "reviewer-brief.md"),
        "limits": {"rework_rounds": routing["rework_rounds"]},
        "utility": harness_routing.get("utility"),
        "agent_types": agent_types,
        "role_text": role_text,
        "schemas": schemas,
        "adversary_variations": routing["adversary_variations"],
        "warnings": warnings,
        "tasks": tasks,
    }


# ---------------------------------------------------------------- verify-result and report

def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _real(path) -> str:
    return os.path.realpath(str(path))


def _attempt_problems(planned: dict, done: dict, gate: dict, run_dir: str) -> list[str]:
    """The gate attempt belongs to this task and run, and its recorded state matches the result."""
    problems: list[str] = []
    if gate.get("tree") != done.get("tree"):
        problems.append(f"gate tree {gate.get('tree')!r} differs from the result's tree {done.get('tree')!r}")
    attempt = gate.get("attempt_dir")
    if not isinstance(attempt, str) or not attempt:
        return problems
    gates_dir = os.path.join(run_dir, "gates")
    if os.path.dirname(_real(attempt)) != _real(gates_dir):
        problems.append(f"gate attempt {attempt} is not directly under {gates_dir}")
    prefix = f"{planned['gate_label_prefix']}-"
    if not os.path.basename(_real(attempt)).startswith(prefix):
        problems.append(f"gate attempt {attempt} is not named {prefix}...")
    state_file = Path(attempt) / "state.json"
    try:
        state = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        state = None
    if not isinstance(state, dict):
        problems.append(f"gate state {state_file} is missing or unreadable")
    else:
        if state.get("tree") != done.get("tree"):
            problems.append(f"gate state tree {state.get('tree')!r} differs from the result's tree {done.get('tree')!r}")
        if not isinstance(state.get("worktree"), str) or _real(state["worktree"]) != _real(planned["worktree"]):
            problems.append(f"gate state worktree {state.get('worktree')!r} is not the task's worktree {planned['worktree']}")
        for key in ("unstaged", "untracked", "outside_scope"):
            if state.get(key) != []:
                problems.append(f"gate state {key} is {state.get(key)!r}, expected an empty list")
        problems.extend(_head_problems(planned, state))
    return problems


def _scaffold_patch_problems(worktree: str, head: str, patches: list[str]) -> list[str]:
    """The scaffolding commit must record exactly the planned dependency patches (an amend keeps subject and parent)."""
    missing = [patch for patch in patches if not os.path.isfile(patch)]
    if missing:
        return [f"dependency patch {patch} does not exist" for patch in missing]
    expected = "patches: " + " ".join(_sha256(patch) for patch in patches)
    message = (_git(worktree, "show", "-s", "--format=%B", head) or "").splitlines()
    if expected not in message:
        return [f"scaffolding commit {head} does not record the planned dependency patches ({expected}); "
                "it was amended or built from other patches"]
    return []


def _head_problems(planned: dict, state: dict) -> list[str]:
    """The gate saw the task's start commit, or one scaffolding commit on it, and HEAD has not moved since."""
    worktree = planned["worktree"]
    head = state.get("head")
    if not isinstance(head, str) or not head:
        return ["gate state records no head"]
    problems: list[str] = []
    start = planned.get("start_head")
    if start:
        if head != start:
            subject = _git(worktree, "show", "-s", "--format=%s", head)
            parents = (_git(worktree, "show", "-s", "--format=%P", head) or "").split()
            if subject != SCAFFOLD_SUBJECT or parents != [start]:
                problems.append(f"gate state head {head} is neither the task's start_head {start} nor a scaffolding commit "
                                "on it: work committed inside the task worktree is missing from the patch")
            else:
                problems.extend(_scaffold_patch_problems(worktree, head, planned.get("_dependency_patches", [])))
    elif not planned.get("done"):
        problems.append("the workflow arguments carry no start_head for this task")
    current = _git(worktree, "rev-parse", "--verify", "HEAD")
    if current != head:
        problems.append(f"worktree HEAD {current} differs from the gate state head {head}")
    return problems


def _pass_problems(planned: dict | None, done: dict, run_dir: str = "") -> list[str]:
    if planned is None:
        return ["the task is not in the workflow arguments"]
    problems: list[str] = []
    if done.get("status") != "PASS":
        problems.append(f"a carried-over task must be PASS, got {done.get('status')!r}")
    patch = planned["patch"]
    if done.get("patch") and os.path.abspath(done["patch"]) != os.path.abspath(patch):
        problems.append(f"result patch {done['patch']} is not the planned patch {patch}")
    if not os.path.isfile(patch):
        problems.append(f"patch {patch} does not exist")
    elif _sha256(patch) != done.get("sha256"):
        problems.append(f"patch sha256 {_sha256(patch)} differs from the result's {done.get('sha256')!r}")
    gate = done.get("gate") if isinstance(done.get("gate"), dict) else {}
    attempt = gate.get("attempt_dir")
    results_file = Path(attempt) / "result.json" if isinstance(attempt, str) and attempt else None
    if results_file is None or not results_file.is_file():
        problems.append(f"gate result {results_file or '(no attempt_dir)'} does not exist")
    else:
        try:
            entries = json.loads(results_file.read_text())
        except json.JSONDecodeError:
            entries = None
        if not isinstance(entries, list) or not entries:
            problems.append(f"gate result {results_file} lists no commands")
        else:
            commands = [entry.get("command") if isinstance(entry, dict) else entry for entry in entries]
            if commands != list(planned.get("gate", [])):
                problems.append(f"gate result {results_file} ran {commands}, not the planned gate {planned.get('gate')}")
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("rc") != 0:
                    command = entry.get("command") if isinstance(entry, dict) else entry
                    rc = entry.get("rc") if isinstance(entry, dict) else None
                    problems.append(f"gate command failed: {command!r} (rc {rc}) in {results_file}")
    problems.extend(_attempt_problems(planned, done, gate, run_dir))
    worktree = planned["worktree"]
    if os.path.isfile(patch):
        scopes = [a for s in planned["scope"] for a in ("--scope", s)]
        proc = subprocess.run([str(Path(__file__).resolve().parent / "integrate.sh"), "verify-clean", worktree, patch, *scopes],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            problems.append(f"verify-clean failed (exit {proc.returncode}): {' / '.join(proc.stdout.strip().splitlines())}")
    proc = subprocess.run(["git", "-C", worktree, "write-tree"], capture_output=True, text=True)
    tree = proc.stdout.strip() if proc.returncode == 0 else None
    if tree != done.get("tree"):
        problems.append(f"worktree tree {tree} differs from the result's {done.get('tree')!r}")
    return problems


def verify_result(args_obj: dict, result: dict) -> dict:
    planned = {t["id"]: dict(t) for t in args_obj.get("tasks", [])}
    for t in planned.values():
        t["_dependency_patches"] = [planned[d]["patch"] for d in t.get("scaffold_from", []) if d in planned]
    run_dir = str(args_obj.get("run_dir", ""))
    tasks: dict[str, dict] = {}
    for done in result.get("tasks", []):
        if not isinstance(done, dict):
            done = {"id": None, "status": None}
        tid = done.get("id")
        status = done.get("status")
        carried = bool(planned.get(tid, {}).get("done"))
        if status == "PASS" or carried:
            problems = _pass_problems(planned.get(tid), done, run_dir)
        elif status in STATUSES:
            problems = []
        else:
            problems = [f"unknown status {status!r}"]
        tasks[str(tid)] = {"ok": not problems, "status": status, "problems": problems}
    for tid, t in planned.items():
        if tid in tasks:
            continue
        if t.get("done") and isinstance(t.get("previous"), dict):
            # carried over by --only: the earlier run's PASS entry must still hold
            previous = t["previous"]
            problems = _pass_problems(t, previous, run_dir)
            tasks[tid] = {"ok": not problems, "status": previous.get("status"), "problems": problems}
        else:
            tasks[tid] = {"ok": False, "status": None, "problems": ["missing from the execute result"]}
    return {"ok": all(t["ok"] for t in tasks.values()), "tasks": tasks}


def report(args_obj: dict, result: dict) -> str:
    planned = {t["id"]: t for t in args_obj.get("tasks", [])}
    done_by_id = {d.get("id"): d for d in result.get("tasks", [])}
    ids = [tid for tid in planned if tid in done_by_id] + [tid for tid in done_by_id if tid not in planned]
    lines = ["| # | Task | Tier | Rounds | Gate | Review | Status | Evidence |",
             "|---|------|------|--------|------|--------|--------|----------|"]
    for tid in ids:
        done = done_by_id[tid]
        t = planned.get(tid, {})
        chain = t.get("chain") or []
        start = chain[0]["tier"] if chain else None
        history = done.get("history") or []
        level = done.get("level")
        if level is None and history:
            level = max(h.get("level", 0) for h in history)
        reached = done.get("tier")
        if reached is None and isinstance(level, int) and 0 <= level < len(chain):
            reached = chain[level]["tier"]
        if reached is None:
            reached = start
        tier = "-" if reached is None else (f"{start} -> {reached}" if start not in (None, reached) else str(reached))
        rounds = done.get("rounds")
        gate = done.get("gate") if isinstance(done.get("gate"), dict) else None
        if gate:
            name = os.path.basename(str(gate.get("attempt_dir", "")).rstrip("/"))
            gate_text = ("pass" if gate.get("exit_code") == 0 else f"fail (exit {gate.get('exit_code')})") + (f" {name}" if name else "")
        elif history:
            last = history[-1].get("gate_exit")
            gate_text = "pass" if last == 0 else f"fail (exit {last})"
        else:
            gate_text = "-"
        reviews = "; ".join(f"{r.get('lens')} ({r.get('model')}) {r.get('verdict')}"
                            + (f", {r.get('defects')} defects" if r.get("defects") else "")
                            for r in done.get("reviews") or []) or "-"
        status = done.get("status", "-")
        if status == "PASS":
            evidence = (f"{os.path.basename(str(done.get('patch', '')))} sha256 {str(done.get('sha256', ''))[:12]}, "
                        f"{len(done.get('files') or [])} files, tree {str(done.get('tree', ''))[:12]}")
        else:
            evidence = done.get("reason") or "-"
        lines.append("| " + " | ".join(_cell(c) for c in (
            tid, t.get("title", tid), tier, "-" if rounds is None else rounds, gate_text, reviews, status, evidence)) + " |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI

def _fail(errors: list[str]) -> int:
    print(json.dumps({"ok": False, "errors": errors}, indent=2))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("plan")
    p_check.add_argument("--check-files", action="store_true")
    p_render = sub.add_parser("render")
    p_render.add_argument("plan")
    p_args = sub.add_parser("workflow-args")
    p_args.add_argument("plan")
    p_args.add_argument("--skill-dir", default=None)
    p_args.add_argument("--agents-dir", default=None)
    p_args.add_argument("--only", action="append", default=None, metavar="ID")
    p_args.add_argument("--previous", default=None, metavar="RESULT")
    for name in ("verify-result", "report"):
        p = sub.add_parser(name)
        p.add_argument("args_file")
        p.add_argument("result_file")
    args = parser.parse_args(argv)

    skill_dir = Path(getattr(args, "skill_dir", None) or DEFAULT_SKILL_DIR).expanduser().resolve()
    try:
        if args.mode in ("check", "render", "workflow-args"):
            routing = load_routing(skill_dir)
            plan, info = checked_plan(args.plan, routing, getattr(args, "check_files", False))
            if args.mode == "check":
                print(json.dumps({"ok": True, "order": info["order"], "waves": info["waves"]}, indent=2))
            elif args.mode == "render":
                sys.stdout.write(render(plan, info, routing))
            else:
                agents_dir = Path(args.agents_dir or DEFAULT_AGENTS_DIR).expanduser()
                previous = None
                if args.previous:
                    previous = load_json(args.previous, "previous execute result")
                    if not isinstance(previous, dict):
                        raise PlanError([f"{args.previous} is not an execute result ({RESULT_SCHEMA})"])
                out = workflow_args(plan, info, routing, skill_dir, agents_dir, args.only, previous,
                                    args.previous or "--previous")
                for warning in out["warnings"]:
                    print(f"warning: {warning}", file=sys.stderr)
                print(json.dumps(out, indent=2))
            return 0
        args_obj = load_json(args.args_file, "workflow arguments")
        result = load_json(args.result_file, "execute result")
        if not isinstance(args_obj, dict) or not isinstance(result, dict):
            raise PlanError(["the workflow arguments and the execute result must be JSON objects"])
        if args.mode == "verify-result":
            outcome = verify_result(args_obj, result)
            print(json.dumps(outcome, indent=2))
            return 0 if outcome["ok"] else 1
        sys.stdout.write(report(args_obj, result))
        return 0
    except PlanError as error:
        return _fail(error.errors)


if __name__ == "__main__":
    sys.exit(main())
