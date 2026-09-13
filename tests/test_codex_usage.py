import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

SCRIPT = REPO / "evals" / "codex_usage.py"
ROOT = "01aa0001-0000-7000-8000-000000000001"


def line(ts, kind, payload):
    return json.dumps({"timestamp": ts, "type": kind, "payload": payload}) + "\n"


def usage_record(ts, sid, tid, inp, cached, out):
    return line(ts, "token_usage_record", {"thread_id": tid, "session_id": sid, "usage": {
        "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "reasoning_output_tokens": 0,
        "total_tokens": inp + out}})


def token_count(ts, used):
    return line(ts, "event_msg", {"type": "token_count", "rate_limits": {
        "limit_id": "codex", "plan_type": "pro", "secondary": None,
        "primary": {"used_percent": used, "window_minutes": 10080, "resets_at": 0}}})


def make_sessions(root: Path) -> Path:
    day = root / "2026" / "09" / "12"
    day.mkdir(parents=True)
    # root thread
    (day / "rollout-2026-09-12T08-54-53-root.jsonl").write_text(
        line("2026-09-12T08:54:53Z", "session_meta", {"id": ROOT, "session_id": ROOT, "timestamp": "2026-09-12T08:54:53Z",
             "cwd": "/tmp/work", "cli_version": "0.153.0", "source": "exec", "thread_source": "user"})
        + line("2026-09-12T08:54:54Z", "turn_context", {"model": "gpt-6-astra", "effort": "high"})
        + usage_record("2026-09-12T08:55:00Z", ROOT, ROOT, 1000, 800, 50) + token_count("2026-09-12T08:55:00Z", 1.0)
        + usage_record("2026-09-12T09:10:00Z", ROOT, ROOT, 3000, 2900, 70) + token_count("2026-09-12T09:10:00Z", 4.0))
    # spawned subagent
    sub = "01aa0002-0000-7000-8000-000000000002"
    (day / "rollout-2026-09-12T08-56-00-sub.jsonl").write_text(
        line("2026-09-12T08:56:00Z", "session_meta", {"id": sub, "session_id": ROOT, "parent_thread_id": ROOT, "timestamp": "2026-09-12T08:56:00Z",
             "cwd": "/tmp/work", "cli_version": "0.153.0", "thread_source": "subagent",
             "source": {"subagent": {"thread_spawn": {"parent_thread_id": ROOT, "depth": 1, "agent_path": "/root/task_1",
                                                      "agent_nickname": "Ada", "agent_role": "orchestrate-implementer"}}}})
        + line("2026-09-12T08:56:01Z", "turn_context", {"model": "gpt-5.6-luna", "effort": "xhigh"})
        + usage_record("2026-09-12T09:00:00Z", ROOT, sub, 500, 100, 20))
    # guardian thread
    guard = "01aa0003-0000-7000-8000-000000000003"
    (day / "rollout-2026-09-12T08-57-00-guard.jsonl").write_text(
        line("2026-09-12T08:57:00Z", "session_meta", {"id": guard, "session_id": ROOT, "parent_thread_id": ROOT, "timestamp": "2026-09-12T08:57:00Z",
             "cwd": "/tmp/work", "cli_version": "0.153.0", "thread_source": "guardian_review", "source": {"subagent": {"other": "guardian"}}})
        + line("2026-09-12T08:57:01Z", "turn_context", {"model": "codex-auto-review", "effort": "low"})
        + usage_record("2026-09-12T08:58:00Z", ROOT, guard, 9000, 9000, 5))
    return root


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class CodexUsageTests(unittest.TestCase):
    def test_list_counts_subagents_without_guardian(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_sessions(Path(tmp))
            proc = run("--sessions-dir", tmp, "--list", "--date", "2026-09-12")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("01aa0001", proc.stdout)
            self.assertRegex(proc.stdout, r"01aa0001\s+1\s+/tmp/work")

    def test_report_groups_roles_models_and_rate_limit_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_sessions(Path(tmp))
            proc = run("--sessions-dir", tmp, "--root", "01aa0001", "--date", "2026-09-12")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("orchestrate-implementer (Ada)", proc.stdout)
            self.assertIn("gpt-5.6-luna / xhigh", proc.stdout)
            self.assertIn("primary 7d 1.0% -> 4.0%", proc.stdout)
            self.assertIn("guardian skipped: 1", proc.stdout)
            self.assertIn("| all | 2 | 3 |", proc.stdout)

    def test_json_totals_and_guardian_inclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_sessions(Path(tmp))
            base = json.loads(run("--sessions-dir", tmp, "--latest", "--format", "json").stdout)
            self.assertEqual(base["total"]["usage"]["input_tokens"], 4500)
            self.assertEqual(base["total"]["usage"]["output_tokens"], 140)
            self.assertEqual(base["cache_hit_percent"], round(100 * 3800 / 4500, 1))
            with_guard = json.loads(run("--sessions-dir", tmp, "--latest", "--format", "json", "--include-guardian").stdout)
            self.assertEqual(with_guard["total"]["usage"]["input_tokens"], 13500)
            self.assertEqual(with_guard["total"]["threads"], 3)

    def test_ambiguous_or_missing_root_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_sessions(Path(tmp))
            self.assertEqual(run("--sessions-dir", tmp, "--root", "zzz").returncode, 1)
            self.assertEqual(run("--sessions-dir", tmp + "/empty", "--list").returncode, 1)


if __name__ == "__main__":
    unittest.main()
