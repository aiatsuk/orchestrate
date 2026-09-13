import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SCRIPTS

GATE = SCRIPTS / "gate.py"


class GateRunTests(unittest.TestCase):
    def test_run_records_logs_json_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            proc = subprocess.run([sys.executable, str(GATE), "run", "--run-dir", str(run_dir), "--label", "t1",
                                   "--", "echo hello", "echo oops >&2; exit 3"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual((run_dir / "t1" / "1.log").read_text(), "hello\n")
            self.assertIn("oops", (run_dir / "t1" / "2.log").read_text())
            results = json.loads((run_dir / "t1" / "result.json").read_text())
            self.assertEqual([r["rc"] for r in results], [0, 3])
            status = (run_dir / "status.md").read_text().splitlines()
            self.assertEqual(len(status), 2)
            self.assertIn("exit 3", status[1])

    def test_run_exit_zero_when_all_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, str(GATE), "run", "--run-dir", tmp, "--label", "ok", "--", "true"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)


class GateDeltaTests(unittest.TestCase):
    def _delta(self, baseline: str, current: str):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "b.log"; c = Path(tmp) / "c.log"
            b.write_text(baseline); c.write_text(current)
            return subprocess.run([sys.executable, str(GATE), "delta", "--baseline", str(b), "--current", str(c)],
                                  capture_output=True, text=True)

    def test_new_issue_fails(self):
        proc = self._delta("   info • old one • a.dart:1\n", "   info • old one • a.dart:1\n   info • new one • b.dart:2\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NEW: info • new one • b.dart:2", proc.stdout)

    def test_same_or_fewer_issues_pass(self):
        proc = self._delta("   warning • x • a.dart:1\n   info • y • c.dart:3\n", "   info • y • c.dart:3\nAnalyzing...\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("resolved=1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
