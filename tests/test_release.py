"""scripts/release.py: version, check and notes, against the real repository and temporary copies."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import REPO

RELEASE = REPO / "scripts" / "release.py"
SKILL = "---\nname: orchestrate\nmetadata:\n  version: {v}\n---\n\n# Orchestrate\n"
SCRIPT = "'use strict'\nconst SCRIPT_VERSION = '{v}'\n"
CHANGELOG = (
    "# Changelog\n\nIntro text.\n\n"
    "## [{v}] - 2026-10-01\n\nNewest summary.\n\n### Changed\n- one\n- two\n\n"
    "## [0.1.0] - 2026-01-01\n\n### Added\n- first\n"
)


def make_repo(root: Path, version: str = "0.2.0") -> Path:
    (root / "skill" / "workflows").mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n")
    (root / "skill" / "SKILL.md").write_text(SKILL.format(v=version))
    (root / "skill" / "workflows" / "orchestrate-execute.js").write_text(SCRIPT.format(v=version))
    (root / "CHANGELOG.md").write_text(CHANGELOG.format(v=version))
    return root


def run(*args, root=None):
    cmd = [sys.executable, str(RELEASE)] + (["--root", str(root)] if root else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_repository_agrees_with_its_version(self):
        version = (REPO / "VERSION").read_text().strip()
        out = run("version")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), version)
        out = run("check", "--tag", f"v{version}")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_agreement_passes(self):
        out = run("check", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(run("version", root=self.root).stdout, "0.2.0\n")

    def test_each_mismatched_version_file_fails(self):
        edits = {
            "VERSION": ("0.2.0", "0.2.1"),
            "skill/SKILL.md": ("version: 0.2.0", "version: 0.2.1"),
            "skill/workflows/orchestrate-execute.js": ("'0.2.0'", "'0.2.1'"),
        }
        for name, (old, new) in edits.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = make_repo(Path(tmp) / "repo")
                path = root / name
                path.write_text(path.read_text().replace(old, new))
                out = run("check", "--tag", "v0.2.0", root=root)
                self.assertEqual(out.returncode, 1)
                self.assertIn(f"{name}: version is 0.2.1, expected 0.2.0", out.stderr)

    def test_missing_version_file_fails(self):
        (self.root / "skill" / "workflows" / "orchestrate-execute.js").unlink()
        out = run("check", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("orchestrate-execute.js: file is missing", out.stderr)

    def test_missing_changelog_section_fails(self):
        path = self.root / "CHANGELOG.md"
        path.write_text(path.read_text().replace("## [0.2.0] - 2026-10-01", "## Unreleased"))
        out = run("check", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("no '## [0.2.0]' section", out.stderr)
        out = run("notes", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("no '## [0.2.0]' section", out.stderr)

    def test_section_that_is_not_newest_fails(self):
        path = self.root / "CHANGELOG.md"
        path.write_text(path.read_text().replace("Intro text.\n", "Intro text.\n\n## [0.3.0] - 2026-11-01\n\n- later\n"))
        out = run("check", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("newest section is 0.3.0, expected 0.2.0", out.stderr)

    def test_missing_changelog_fails(self):
        (self.root / "CHANGELOG.md").unlink()
        out = run("check", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("CHANGELOG.md is missing", out.stderr)

    def test_bad_tag_fails(self):
        for tag in ("0.2.0", "v0.2", "release-0.2.0"):
            with self.subTest(tag=tag):
                out = run("check", "--tag", tag, root=self.root)
                self.assertEqual(out.returncode, 1)
                self.assertIn("is not of the form vX.Y.Z", out.stderr)

    def test_notes_return_exactly_the_section_body(self):
        out = run("notes", "--tag", "v0.2.0", root=self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "Newest summary.\n\n### Changed\n- one\n- two\n")
        out = run("notes", "--tag", "v0.1.0", root=self.root)
        self.assertEqual(out.stdout, "### Added\n- first\n")


if __name__ == "__main__":
    unittest.main()
