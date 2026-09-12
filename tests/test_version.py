"""The version must agree between the skill frontmatter, VERSION and the newest changelog entry."""
import re
import unittest

from helpers import REPO

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def frontmatter_version() -> str:
    text = (REPO / "skill" / "SKILL.md").read_text()
    head = text.split("---", 2)[1]
    match = re.search(r"^\s+version:\s*(\S+)\s*$", head, re.M)
    return match.group(1) if match else ""


def file_version() -> str:
    return (REPO / "VERSION").read_text().strip()


def changelog_version() -> str:
    match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (REPO / "CHANGELOG.md").read_text(), re.M)
    return match.group(1) if match else ""


class VersionTests(unittest.TestCase):
    def test_versions_agree(self):
        self.assertEqual(frontmatter_version(), file_version())
        self.assertEqual(changelog_version(), file_version())

    def test_version_is_semver(self):
        self.assertRegex(file_version(), SEMVER)


if __name__ == "__main__":
    unittest.main()
