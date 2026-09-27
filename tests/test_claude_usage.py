import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Make this runnable both via `unittest discover` (which puts tests/ on sys.path itself) and via
# `python3 -m unittest tests.test_claude_usage ...` from the repo root, where tests/ is not
# otherwise on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import REPO

SCRIPT = REPO / "evals" / "claude_usage.py"


def usage(input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }


def assistant_line(ts, agent_id, msg_id, model, u):
    return json.dumps({
        "type": "assistant",
        "agentId": agent_id,
        "timestamp": ts,
        "message": {"id": msg_id, "model": model, "type": "message", "role": "assistant", "usage": u},
    }) + "\n"


def write_meta(directory: Path, agent_id: str, description=None, agent_type="workflow-subagent", phase=None):
    meta = {"agentType": agent_type}
    if description is not None:
        meta["description"] = description
    if phase is not None:
        meta["workflowPhase"] = phase
    (directory / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))


def write_journal(run_dir: Path, entries):
    lines = [json.dumps({"type": "launched"})]
    for agent_id, label, phase in entries:
        lines.append(json.dumps({"type": "started", "key": f"k-{agent_id}", "agentId": agent_id, "label": label, "phase": phase}))
    (run_dir / "journal.jsonl").write_text("\n".join(lines) + "\n")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class ClaudeUsageTests(unittest.TestCase):
    def test_duplicate_message_ids_counted_once_with_largest_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            content = (
                assistant_line("2026-09-27T10:00:00Z", "a1", "msg-1", "claude-opus-5-5", usage(100, 8, 40000, 0))
                + assistant_line("2026-09-27T10:00:01Z", "a1", "msg-1", "claude-opus-5-5", usage(100, 16, 40000, 0))
                + assistant_line("2026-09-27T10:00:02Z", "a1", "msg-1", "claude-opus-5-5", usage(100, 347, 40000, 0))
            )
            (run_dir / "agent-a1.jsonl").write_text(content)
            write_meta(run_dir, "a1", description="solo-task")
            proc = run("--workflow-dir", str(run_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(len(data["agents"]), 1)
            agent = data["agents"][0]
            # Fails if dedup sums every line instead of keeping the largest-output line
            # (would report messages=3, output_tokens=8+16+347=371).
            self.assertEqual(agent["messages"], 1)
            self.assertEqual(agent["output_tokens"], 347)
            self.assertEqual(agent["input_tokens"], 100)

    def test_label_precedence_journal_over_meta_over_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            for agent_id in ("a1", "a2", "a3"):
                (run_dir / f"agent-{agent_id}.jsonl").write_text(
                    assistant_line("2026-09-27T10:00:00Z", agent_id, f"msg-{agent_id}", "claude-opus-5-5", usage(10, 5, 0, 0))
                )
            write_meta(run_dir, "a1", description="meta-description-a1")
            write_meta(run_dir, "a2", description="meta-description-a2")
            # a3 has no meta.json and no journal entry: falls back to its id.
            write_journal(run_dir, [("a1", "journal-label-a1", "Analyze")])
            proc = run("--workflow-dir", str(run_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            agents = {a["id"]: a for a in json.loads(proc.stdout)["agents"]}
            # Fails if the precedence order changes (e.g. meta preferred over journal,
            # or the id used even when a meta description exists).
            self.assertEqual(agents["a1"]["label"], "journal-label-a1")
            self.assertEqual(agents["a2"]["label"], "meta-description-a2")
            self.assertEqual(agents["a3"]["label"], "a3")

    def test_per_model_and_total_sums(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            (run_dir / "agent-a1.jsonl").write_text(
                assistant_line("2026-09-27T10:00:00Z", "a1", "msg-a1-1", "claude-opus-5-5", usage(1000, 100, 200, 50))
            )
            (run_dir / "agent-a2.jsonl").write_text(
                assistant_line("2026-09-27T10:05:00Z", "a2", "msg-a2-1", "claude-haiku-5", usage(500, 40, 0, 300))
            )
            write_meta(run_dir, "a1", description="task-one")
            write_meta(run_dir, "a2", description="task-two")
            proc = run("--workflow-dir", str(run_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            models = data["models"]
            # Fails if the two models' usage is merged into one bucket instead of split.
            self.assertEqual(models["claude-opus-5-5"]["input_tokens"], 1000)
            self.assertEqual(models["claude-haiku-5"]["cache_read_input_tokens"], 300)
            total = data["total"]
            self.assertEqual(total["input_tokens"], 1500)
            self.assertEqual(total["output_tokens"], 140)
            self.assertEqual(total["cache_creation_input_tokens"], 200)
            self.assertEqual(total["cache_read_input_tokens"], 350)
            self.assertEqual(total["agents"], 2)

    def test_main_flag_adds_main_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            (run_dir / "agent-a1.jsonl").write_text(
                assistant_line("2026-09-27T10:00:00Z", "a1", "msg-a1-1", "claude-opus-5-5", usage(10, 5, 0, 0))
            )
            write_meta(run_dir, "a1", description="task-one")
            main_file = Path(tmp) / "session.jsonl"
            main_file.write_text(
                assistant_line("2026-09-27T09:55:00Z", "main", "msg-main-1", "claude-opus-5-5", usage(300, 60, 100, 20))
            )
            proc = run("--workflow-dir", str(run_dir), "--main", str(main_file), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            agents = {a["id"]: a for a in data["agents"]}
            # Fails if --main is ignored or the main transcript isn't folded into the totals.
            self.assertIn("main", agents)
            self.assertEqual(agents["main"]["input_tokens"], 300)
            self.assertEqual(data["total"]["agents"], 2)
            self.assertEqual(data["total"]["input_tokens"], 310)

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            (run_dir / "agent-a1.jsonl").write_text(
                assistant_line("2026-09-27T10:00:00Z", "a1", "msg-a1-1", "claude-opus-5-5", usage(10, 5, 0, 0))
            )
            write_meta(run_dir, "a1", description="task-one")
            proc = run("--workflow-dir", str(run_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            # Fails if the top-level shape or a required per-agent field is renamed/removed.
            self.assertEqual(set(data.keys()), {"agents", "models", "total"})
            agent = data["agents"][0]
            for key in ("id", "label", "phase", "agent_type", "models", "messages",
                        "input_tokens", "output_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "first_ts", "last_ts", "duration_seconds"):
                self.assertIn(key, agent)
            self.assertIn("cache_hit_rate", data["total"])

    def test_malformed_line_skipped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "wf_run"
            run_dir.mkdir()
            content = (
                assistant_line("2026-09-27T10:00:00Z", "a1", "msg-a1-1", "claude-opus-5-5", usage(10, 5, 0, 0))
                + "{not valid json\n"
                + assistant_line("2026-09-27T10:00:01Z", "a1", "msg-a1-2", "claude-opus-5-5", usage(20, 6, 0, 0))
            )
            (run_dir / "agent-a1.jsonl").write_text(content)
            write_meta(run_dir, "a1", description="task-one")
            proc = run("--workflow-dir", str(run_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            # Fails if the malformed line crashes the run or isn't tallied in skipped_lines.
            self.assertEqual(data["total"]["skipped_lines"], 1)
            self.assertEqual(data["total"]["messages"], 2)

    def test_session_dir_discovers_nested_workflow_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            plain_dir = session_dir / "subagents"
            plain_dir.mkdir(parents=True)
            (plain_dir / "agent-p1.jsonl").write_text(
                assistant_line("2026-09-27T10:00:00Z", "p1", "msg-p1-1", "claude-opus-5-5", usage(10, 5, 0, 0))
            )
            write_meta(plain_dir, "p1", description="plain-subagent")
            wf_dir = plain_dir / "workflows" / "wf_run"
            wf_dir.mkdir(parents=True)
            (wf_dir / "agent-w1.jsonl").write_text(
                assistant_line("2026-09-27T10:01:00Z", "w1", "msg-w1-1", "claude-haiku-5", usage(20, 6, 0, 0))
            )
            write_meta(wf_dir, "w1", description="workflow-subagent-task")
            proc = run("--session-dir", str(session_dir), "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            ids = {a["id"] for a in data["agents"]}
            # Fails if the session-dir scan doesn't recurse into subagents/workflows/<runId>/.
            self.assertEqual(ids, {"p1", "w1"})

    def test_missing_path_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            proc = run("--workflow-dir", str(missing))
            self.assertEqual(proc.returncode, 2)
            self.assertTrue(proc.stderr.strip())

    def test_no_source_flag_exits_2(self):
        proc = run()
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
