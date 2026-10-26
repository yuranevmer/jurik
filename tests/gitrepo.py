"""Helpers for tests that inspect git repositories built by jurik (every test git call
goes through here)."""

import subprocess
from pathlib import Path


def git_bytes(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    """Stdout of ``git -C repo args…``; a failure raises ``CalledProcessError``."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True, check=True
    ).stdout


def git(repo: Path, *args: str) -> str:
    return git_bytes(repo, *args).decode("utf-8")


def git_ok(repo: Path, *args: str) -> bool:
    """Whether ``git -C repo args…`` exits with 0 (for predicates like ``merge-base``)."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


def tree_files(repo: Path, rev: str = "main") -> dict[str, bytes]:
    paths = git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()
    return {p: git_bytes(repo, "show", f"{rev}:{p}") for p in paths}
