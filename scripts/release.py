#!/usr/bin/env python3
"""Release helper: read the version, check a tag against every version file, print release notes.

  release.py version                 print the version from VERSION
  release.py check --tag vX.Y.Z      fail unless every version file agrees with X.Y.Z and
                                     CHANGELOG.md has a section for it
  release.py notes --tag vX.Y.Z      print the body of that CHANGELOG.md section

`--root DIR` points at another repository root (used by the tests). Standard library only.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = "VERSION"
CHANGELOG = "CHANGELOG.md"
# The files tests/test_version.py keeps in sync, with the pattern that captures each version.
VERSION_FILES = [
    ("VERSION", re.compile(r"\A\s*(\S+)\s*\Z")),
    ("skill/SKILL.md", re.compile(r"\A---\n.*?^\s+version:\s*(\S+)\s*$.*?^---$", re.M | re.S)),
    ("skill/workflows/orchestrate-execute.js", re.compile(r"^const SCRIPT_VERSION = '([^']+)'$", re.M)),
]
# Existing changelog format: "## [X.Y.Z] - YYYY-MM-DD", newest first.
HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\][^\n]*$", re.M)
TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")


class ReleaseError(Exception):
    pass


def read_version(root: Path) -> str:
    return (root / PRIMARY).read_text().strip()


def parse_tag(tag: str) -> str:
    match = TAG.match(tag)
    if not match:
        raise ReleaseError(f"tag {tag!r} is not of the form vX.Y.Z")
    return match.group(1)


def changelog_sections(root: Path) -> list:
    """Return (version, body) pairs in file order."""
    path = root / CHANGELOG
    if not path.is_file():
        raise ReleaseError(f"{CHANGELOG} is missing")
    text = path.read_text()
    headings = list(HEADING.finditer(text))
    sections = []
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections.append((match.group(1), text[match.end():end].strip("\n")))
    return sections


def check(root: Path, version: str) -> None:
    problems = []
    for name, pattern in VERSION_FILES:
        path = root / name
        if not path.is_file():
            problems.append(f"{name}: file is missing")
            continue
        match = pattern.search(path.read_text())
        found = match.group(1) if match else None
        if found != version:
            problems.append(f"{name}: version is {found or 'not found'}, expected {version}")
    sections = changelog_sections(root)
    if not any(v == version for v, _ in sections):
        problems.append(f"{CHANGELOG}: no '## [{version}]' section")
    elif sections[0][0] != version:
        problems.append(f"{CHANGELOG}: newest section is {sections[0][0]}, expected {version}")
    if problems:
        raise ReleaseError("version check failed:\n  " + "\n  ".join(problems))


def notes(root: Path, version: str) -> str:
    for found, body in changelog_sections(root):
        if found == version:
            return body.strip() + "\n"
    raise ReleaseError(f"{CHANGELOG}: no '## [{version}]' section")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repository)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version")
    for name in ("check", "notes"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--tag", required=True, help="release tag, vX.Y.Z")
    args = parser.parse_args(argv)
    try:
        if args.command == "version":
            print(read_version(args.root))
        elif args.command == "check":
            version = parse_tag(args.tag)
            check(args.root, version)
            print(f"{args.tag}: every version file agrees and {CHANGELOG} has the section")
        else:
            sys.stdout.write(notes(args.root, parse_tag(args.tag)))
    except (ReleaseError, OSError) as exc:
        print(f"release.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
