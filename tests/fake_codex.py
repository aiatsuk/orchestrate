#!/usr/bin/env python3
"""A stand-in for `codex exec` in tests: no model, a scripted answer chosen from the prompt.

  fake_codex.py exec --skip-git-repo-check [--ephemeral] -C DIR -s SANDBOX -m MODEL [-c KEY=VALUE ...]
                [--add-dir DIR ...] [--output-schema FILE] -o FILE --json PROMPT

The role comes from the prompt:
  relay        "Run exactly this command once"  exit 4: the runtime must run relays itself
  confirm      "disagreed"                      confirms every listed defect
  impl/rework  "You are the tier"               applies the scenario edit, runs `git add -A`, writes a report
  review       "independent reviewer"           writes the scenario verdict (default PASS, no defects)

Checks: the sandbox must match the role and -C must be the worktree the prompt names, quoted or
not (exit 3); the output schema must be strict, every object closed and every property required
(exit 6). Every call appends one JSON line (argv, kind, worktree, sandbox, model, effort,
add_dirs, prompt, start, end, exit, failed) to $FAKE_CODEX_CALLS; a hanging call writes its line
before it hangs (end and exit null), since it is killed. $FAKE_CODEX_SLEEP delays every answer by
that many seconds. The scenario file $FAKE_CODEX_SCENARIO maps a worktree basename to:

  {"edits": [{"path": "content"}, ...],  the n-th successful implementer call uses edits[min(n, len - 1)]
   "verdicts": [{"verdict": ...}, ...],   the n-th successful review call; PASS when absent
   "fail_times": {"impl": 1},             the first N calls of that kind (impl, rework, review, confirm) fail
   "fail_mode": "invalid",                invalid (schema-violating JSON), garbage (not JSON), exit (status 1),
                                          hang (silent) or chatter (one event every 0.1 s)
   "ignore_term": false}                  a hanging call ignores SIGTERM (only SIGKILL ends it)

A hanging call starts `sleep` as a child in its process group and appends that child's pid to
$FAKE_CODEX_GRANDCHILD when set, then runs for $FAKE_CODEX_HANG seconds (default 30) and exits 1.
"""
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

FLAGS = {"--skip-git-repo-check", "--ephemeral", "--json"}
VALUED = {"-C": "cwd", "-s": "sandbox", "-m": "model", "--output-schema": "schema", "-o": "last"}
SANDBOX = {"impl": "workspace-write", "rework": "workspace-write", "review": "read-only", "confirm": "read-only"}
PASS = {"verdict": "PASS", "defects": [], "notes": [], "gate": []}
# A path as the workflow writes it: single-quoted ('a'\''b'), or a plain word
PATH = r"('[^']*'(?:\\''[^']*')*|[^\s'`;]+)"


def parse(argv):
    if not argv or argv[0] != "exec":
        raise ValueError("the first argument must be exec")
    opts = {"flags": set(), "config": [], "add_dirs": [], "prompt": None}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in FLAGS:
            opts["flags"].add(arg)
            i += 1
        elif arg in VALUED or arg in ("-c", "--add-dir"):
            if i + 1 >= len(argv):
                raise ValueError(f"{arg} needs a value")
            if arg == "-c":
                opts["config"].append(argv[i + 1])
            elif arg == "--add-dir":
                opts["add_dirs"].append(argv[i + 1])
            else:
                opts[VALUED[arg]] = argv[i + 1]
            i += 2
        elif arg.startswith("-"):
            raise ValueError(f"unknown flag {arg}")
        elif i != len(argv) - 1:
            raise ValueError("the prompt must be the last argument")
        else:
            opts["prompt"] = arg
            i += 1
    missing = [f for f in ("--skip-git-repo-check", "--json") if f not in opts["flags"]]
    missing += [f for f, key in VALUED.items() if key != "schema" and not opts.get(key)]
    if missing or not opts["prompt"]:
        raise ValueError(f"missing {missing or 'the prompt'}")
    return opts


def kind_of(prompt):
    if "Run exactly this command once" in prompt:
        return "relay"
    if "disagreed" in prompt:
        return "confirm"
    if "You are the tier" in prompt:
        return "rework" if "for rework round" in prompt else "impl"
    if "independent reviewer" in prompt:
        return "review"
    return "unknown"


def worktree_in(prompt, kind):
    """The worktree the prompt names: implementers are told where to work, reviewers what to diff."""
    patterns = {
        "impl": rf"work only inside the worktree {PATH} \(branch",
        "rework": rf"work only inside the worktree {PATH} \(branch",
        "review": rf"git -C {PATH} diff --cached",
        "confirm": rf"staged diff of the worktree {PATH};",
    }
    match = re.search(patterns[kind], prompt)
    if not match:
        return None
    words = shlex.split(match.group(1))
    return words[0] if len(words) == 1 else None


def strict(node):
    if not isinstance(node, dict):
        return True
    if "properties" in node or node.get("type") == "object":
        props = node.get("properties", {})
        if node.get("additionalProperties") is not False or sorted(node.get("required", [])) != sorted(props):
            return False
        if not all(strict(sub) for sub in props.values()):
            return False
    return strict(node["items"]) if "items" in node else True


def previous_calls(worktree):
    path = os.environ.get("FAKE_CODEX_CALLS")
    if not path or not os.path.exists(path):
        return []
    lines = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return [c for c in lines if c.get("worktree") == worktree]


def write_record(record):
    calls = os.environ.get("FAKE_CODEX_CALLS")
    if calls and not record.get("written"):
        with open(calls, "a") as handle:
            handle.write(json.dumps(record) + "\n")
        record["written"] = True


def emit(event):
    print(json.dumps(event), flush=True)


def hang(mode, scenario, record):
    """Run like a stuck agent until killed; the runtime's timeouts must end this."""
    grandchild = subprocess.Popen(["sleep", "60"])
    pid_file = os.environ.get("FAKE_CODEX_GRANDCHILD")
    if pid_file:
        with open(pid_file, "a") as handle:
            handle.write(f"{grandchild.pid}\n")
    if scenario.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    record.update(end=None, exit=None)
    write_record(record)
    deadline = time.time() + float(os.environ.get("FAKE_CODEX_HANG") or 30)
    while time.time() < deadline:
        if mode == "chatter":
            emit({"type": "item.updated"})
        time.sleep(0.1)
    grandchild.kill()
    return 1


def answer(argv, record):
    try:
        opts = parse(argv)
    except ValueError as error:
        print(f"fake codex: {error}", file=sys.stderr)
        return 2
    prompt = opts["prompt"]
    kind = kind_of(prompt)
    effort = next((c.split("=", 1)[1] for c in opts["config"] if c.startswith("model_reasoning_effort=")), None)
    cwd = opts["cwd"]
    record.update(kind=kind, cwd=cwd, worktree=os.path.basename(os.path.realpath(cwd)), sandbox=opts["sandbox"],
                  model=opts["model"], effort=effort, add_dirs=opts["add_dirs"], prompt=prompt)
    emit({"type": "thread.started", "thread_id": f"fake-{os.getpid()}"})
    if kind == "relay":
        print("fake codex: relay prompts are run by the runtime, never by codex", file=sys.stderr)
        return 4
    if kind == "unknown":
        print("fake codex: no role recognized in the prompt", file=sys.stderr)
        return 5
    named = worktree_in(prompt, kind)
    if opts["sandbox"] != SANDBOX[kind] or not named or os.path.realpath(named) != os.path.realpath(cwd):
        print(f"fake codex: {kind} got sandbox {opts['sandbox']} in {cwd}; the prompt names {named}", file=sys.stderr)
        return 3
    try:
        schema = json.loads(Path(opts["schema"]).read_text()) if opts.get("schema") else None
    except (OSError, json.JSONDecodeError):
        schema = None
    if schema is None or not strict(schema):
        print("fake codex: the output schema is missing or not strict", file=sys.stderr)
        return 6
    delay = float(os.environ.get("FAKE_CODEX_SLEEP") or 0)
    if delay:
        time.sleep(delay)

    scenario_path = os.environ.get("FAKE_CODEX_SCENARIO")
    scenario = json.loads(Path(scenario_path).read_text()).get(record["worktree"], {}) if scenario_path else {}
    previous = previous_calls(record["worktree"])
    if sum(1 for c in previous if c.get("kind") == kind) < int(scenario.get("fail_times", {}).get(kind, 0)):
        record["failed"] = True
        mode = scenario.get("fail_mode", "invalid")
        if mode in ("hang", "chatter"):
            return hang(mode, scenario, record)
        if mode == "exit":
            print("fake codex: scripted failure", file=sys.stderr)
            return 1
        Path(opts["last"]).write_text("this is not JSON" if mode == "garbage" else json.dumps({"unexpected": True}))
        emit({"type": "turn.completed"})
        return 0

    done = [c for c in previous if not c.get("failed")]
    if kind in ("impl", "rework"):
        count = sum(1 for c in done if c.get("kind") in ("impl", "rework"))
        edits_list = scenario.get("edits") or [{}]
        edits = edits_list[min(count, len(edits_list) - 1)]
        for rel, content in edits.items():
            target = Path(cwd) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True)
        result = {"files_changed": [{"path": rel, "change": "edited"} for rel in edits], "gate": [], "tests": [],
                  "not_done": [], "questions": []}
    elif kind == "review":
        count = sum(1 for c in done if c.get("kind") == "review")
        verdicts = scenario.get("verdicts") or []
        result = {**PASS, **(verdicts[count] if count < len(verdicts) else {})}
    else:
        listed = len(re.findall(r"^\d+: \{", prompt, re.M))
        result = {"defects": [{"index": i, "confirmed": True, "evidence": "confirmed"} for i in range(listed)]}
    Path(opts["last"]).write_text(json.dumps(result))
    emit({"type": "turn.completed"})
    return 0


def main(argv):
    record = {"argv": argv, "start": time.time()}
    code = answer(argv, record)
    record.update(end=time.time(), exit=code)
    write_record(record)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
