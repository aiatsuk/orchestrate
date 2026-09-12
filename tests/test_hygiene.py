"""Repository hygiene: English only, no personal absolute paths, scripts executable."""
import os
import re
import unittest
from pathlib import Path

from helpers import REPO

CYRILLIC = re.compile("[\u0400-\u04FF]")
HOME_PATH = re.compile(r"/(Users|home)/[A-Za-z0-9_.-]+/")
SKIP_DIRS = {".git", "__pycache__", "runs"}


def text_files():
    for path in REPO.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


class HygieneTests(unittest.TestCase):
    def test_no_cyrillic_anywhere(self):
        offenders = [str(p.relative_to(REPO)) for p, text in text_files() if CYRILLIC.search(text)]
        self.assertEqual(offenders, [])

    def test_no_personal_absolute_paths(self):
        offenders = [str(p.relative_to(REPO)) for p, text in text_files() if HOME_PATH.search(text)]
        self.assertEqual(offenders, [])

    def test_scripts_are_executable(self):
        for script in (REPO / "skill" / "scripts").iterdir():
            self.assertTrue(os.access(script, os.X_OK), script.name)
        self.assertTrue(os.access(REPO / "install.sh", os.X_OK))


if __name__ == "__main__":
    unittest.main()
