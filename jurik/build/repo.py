"""Read a built laws repository: ``git`` calls, the first-parent log of ``main`` and its
trailers (shared by the invariant checks, ``jurik export`` and ``jurik report``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jurik.build.gitcmd import GitError, run_git
from jurik.build.message import IN_FORCE, parse_trailers
from jurik.build.refs import MAIN


class RepoError(GitError):
    """The repository cannot be read by git."""


@dataclass
class MainCommit:
    sha: str
    parents: list[str]
    trailers: list[tuple[str, str]]
    subject: str


def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    return run_git(repo, *args, input=input, error=RepoError)


def main_log(repo: Path) -> list[MainCommit]:
    """First-parent history of ``main``, oldest first."""
    out = git(repo, "log", "--first-parent", "--reverse", "--format=%H%x00%P%x00%B%x1e", MAIN)
    commits: list[MainCommit] = []
    for rec in out.decode("utf-8", "replace").split("\x1e"):
        if not rec.strip():
            continue
        sha, parents, body = rec.lstrip("\n").split("\x00", 2)
        subject = body.strip().split("\n", 1)[0]
        commits.append(MainCommit(sha, parents.split(), parse_trailers(body), subject))
    return commits


def in_force_of(c: MainCommit) -> date | None:
    """The ``In-force:`` date of ``c``; None if missing or not a date."""
    for key, value in c.trailers:
        if key == IN_FORCE:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None
