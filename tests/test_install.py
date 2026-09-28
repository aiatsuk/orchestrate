import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

INSTALL = REPO / "install.sh"


def run(*args, home=None):
    env = {**os.environ, "HOME": home} if home else os.environ
    return subprocess.run([str(INSTALL), *args], capture_output=True, text=True, env=env)


class InstallTests(unittest.TestCase):
    def test_user_scope_links_skill_and_roles_and_uninstalls(self):
        with tempfile.TemporaryDirectory() as home:
            proc = run(home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for rel in (".claude/skills/orchestrate", ".agents/skills/orchestrate", ".claude/agents/orchestrate-reviewer.md"):
                self.assertTrue((Path(home) / rel).is_symlink(), rel)
            codex_role = Path(home) / ".codex/agents/orchestrate-reviewer.toml"
            self.assertTrue(codex_role.is_file() and not codex_role.is_symlink(), "codex roles must be real files")
            workflow = Path(home) / ".claude/workflows/orchestrate-execute.js"
            self.assertTrue(workflow.is_file() and not workflow.is_symlink(), "saved workflows must be real files")
            self.assertEqual(workflow.read_text(), (REPO / "skill/workflows/orchestrate-execute.js").read_text())
            self.assertEqual(run(home=home).returncode, 0)  # idempotent
            self.assertEqual(run("--uninstall", home=home).returncode, 0)
            self.assertFalse((Path(home) / ".claude/skills/orchestrate").exists())
            self.assertFalse((Path(home) / ".codex/agents/orchestrate-reviewer.toml").exists())
            self.assertFalse(workflow.exists())

    def test_user_scope_refreshes_its_workflow_and_leaves_foreign_ones(self):
        with tempfile.TemporaryDirectory() as home:
            workflows = Path(home) / ".claude/workflows"
            workflows.mkdir(parents=True)
            stale = workflows / "orchestrate-execute.js"
            stale.write_text("export const meta = {\n  name: 'orchestrate-execute',\n}\nreturn 1\n")
            foreign = workflows / "release-audit.js"
            foreign.write_text("export const meta = {\n  name: 'release-audit',\n}\nreturn 1\n")
            self.assertEqual(run(home=home).returncode, 0)
            self.assertEqual(stale.read_text(), (REPO / "skill/workflows/orchestrate-execute.js").read_text())
            stale.write_text("// someone else's file with the same name\n")
            proc = run(home=home)
            self.assertIn("not ours, left alone", proc.stdout)
            self.assertEqual(stale.read_text(), "// someone else's file with the same name\n")
            run("--uninstall", home=home)
            self.assertTrue(stale.exists() and foreign.exists())

    def test_repo_scope_copies_and_appends_agents_block_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "proj"; repo.mkdir()
            (repo / "AGENTS.md").write_text("# Project rules\n")
            proc = run("--repo", str(repo))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((repo / ".agents/skills/orchestrate/SKILL.md").is_file())
            self.assertTrue((repo / ".codex/agents/orchestrate-explorer.toml").is_file())
            self.assertTrue((repo / ".claude/agents/orchestrate-explorer.md").is_file())
            self.assertTrue((repo / ".claude/workflows/orchestrate-execute.js").is_file())
            text = (repo / "AGENTS.md").read_text()
            self.assertIn("# Project rules", text)
            self.assertEqual(text.count("<!-- orchestrate: begin -->"), 1)
            (repo / ".codex/agents/orchestrate-explorer.toml").write_text("custom\n")
            run("--repo", str(repo))
            self.assertEqual((repo / ".codex/agents/orchestrate-explorer.toml").read_text(), "custom\n")
            self.assertEqual((repo / "AGENTS.md").read_text().count("<!-- orchestrate: begin -->"), 1)
            run("--repo", str(repo), "--force")
            self.assertIn("name = \"orchestrate-explorer\"", (repo / ".codex/agents/orchestrate-explorer.toml").read_text())


if __name__ == "__main__":
    unittest.main()
