"""One wrapper for running ``git`` in a repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import BinaryIO


class GitError(RuntimeError):
    """A ``git`` command failed."""


def run_git(
    repo: Path,
    *args: str,
    input: bytes | None = None,
    stdin: BinaryIO | None = None,
    error: type[GitError] = GitError,
) -> bytes:
    """Stdout of ``git -C repo args…``; a non-zero exit raises ``error`` with git's stderr.

    ``stdin`` (a real file) streams large input; ``input`` passes bytes.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, stdin=stdin, capture_output=True
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise error(f"git {' '.join(args)} failed ({proc.returncode}): {err}")
    return proc.stdout
