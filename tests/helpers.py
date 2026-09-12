import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skill" / "scripts"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "a.txt").write_text("alpha\n")
    (path / "b.txt").write_text("beta\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "base", cwd=path)
    return path
