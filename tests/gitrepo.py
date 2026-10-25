"""Helpers for tests that inspect git repositories built by jurik."""

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def tree_files(repo: Path, rev: str = "main") -> dict[str, bytes]:
    paths = git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()
    return {
        p: subprocess.run(
            ["git", "-C", str(repo), "show", f"{rev}:{p}"], capture_output=True, check=True
        ).stdout
        for p in paths
    }
