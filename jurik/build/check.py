"""Invariants of a built laws repository.

(a) every edition of the plan is in exactly one commit of ``main`` (``Edition:`` trailer)
    and that commit's ``In-force:`` is the edition date;
(b) ``In-force:`` along ``git log --first-parent main`` never decreases;
(c) the ``main`` files of each code hold exactly the words of the print of its latest
    edition (:func:`jurik.verify.verify_edition`);
(d) every ``bill/*`` branch that the plan merges is merged into ``main`` exactly once at its
    tip (the tip is the second parent of exactly one first-parent commit of ``main``).

Bill branch commits carry no ``In-force:``/``Edition:``, so (a) and (b) walk only the
first-parent history of ``main``.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from jurik.build.history import MAIN, Step
from jurik.build.message import parse_trailers
from jurik.verify import verify_edition

Check = Literal["a", "b", "c", "d"]


@dataclass(frozen=True)
class Violation:
    check: Check
    message: str

    def __str__(self) -> str:
        return f"({self.check}) {self.message}"


@dataclass
class _Commit:
    sha: str
    parents: list[str]
    trailers: list[tuple[str, str]]
    subject: str


class CheckError(RuntimeError):
    """The repository cannot be read by git."""


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise CheckError(f"git {' '.join(args)} failed ({proc.returncode}): {err}")
    return proc.stdout


def _main_log(repo: Path) -> list[_Commit]:
    """First-parent history of ``main``, oldest first."""
    out = _git(repo, "log", "--first-parent", "--reverse", "--format=%H%x00%P%x00%B%x1e", MAIN)
    commits: list[_Commit] = []
    for rec in out.decode("utf-8", "replace").split("\x1e"):
        if not rec.strip():
            continue
        sha, parents, body = rec.lstrip("\n").split("\x00", 2)
        subject = body.strip().split("\n", 1)[0]
        commits.append(_Commit(sha, parents.split(), parse_trailers(body), subject))
    return commits


def _in_force(c: _Commit) -> date | None:
    for key, value in c.trailers:
        if key == "In-force":
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def edition_key(zakon_id: str, d: date) -> str:
    return f"{zakon_id}/ed{d:%Y%m%d}"


def _edition_date(key: str) -> date | None:
    _, sep, stamp = key.rpartition("/ed")  # zakon_id may contain "/" (254к/96-ВР)
    try:
        return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8])) if sep else None
    except ValueError:
        return None


def check_editions(commits: Sequence[_Commit], steps: Sequence[Step]) -> list[Violation]:
    """(a) every planned edition is in exactly one ``main`` commit with its In-force date."""
    expected = {edition_key(e.zakon_id, e.date) for s in steps if s.ref == MAIN for e in s.editions}
    seen: Counter[str] = Counter()
    out: list[Violation] = []
    for c in commits:
        in_force = _in_force(c)
        for key, value in c.trailers:
            if key != "Edition":
                continue
            seen[value] += 1
            if value not in expected:
                out.append(Violation("a", f"{c.sha[:10]}: edition {value} is not in the plan"))
            elif _edition_date(value) != in_force:
                out.append(Violation("a", f"{c.sha[:10]}: edition {value} has In-force {in_force}"))
    for key in sorted(expected - seen.keys()):
        out.append(Violation("a", f"edition {key} has no commit in main"))
    for key, n in sorted(seen.items()):
        if n > 1:
            out.append(Violation("a", f"edition {key} is in {n} commits of main"))
    return out


def check_in_force(commits: Sequence[_Commit]) -> list[Violation]:
    """(b) every first-parent ``main`` commit has ``In-force:`` and it never decreases."""
    out: list[Violation] = []
    prev: date | None = None
    for c in commits:
        d = _in_force(c)
        if d is None:
            out.append(Violation("b", f"{c.sha[:10]} {c.subject!r}: no valid In-force"))
            continue
        if prev and d < prev:
            out.append(Violation("b", f"{c.sha[:10]} {c.subject!r}: In-force {d} < {prev}"))
        prev = d
    return out


def _main_files(repo: Path, prefix: str) -> dict[str, bytes]:
    """``.md`` files of ``main`` under ``prefix`` (read from git, not the work tree)."""
    names = _git(repo, "ls-tree", "-r", "-z", "--name-only", MAIN, "--", prefix)
    paths = [p for p in names.decode("utf-8").split("\x00") if p.endswith(".md")]
    if not paths:
        return {}
    batch = "".join(f"{MAIN}:{p}\n" for p in paths).encode("utf-8")
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"], input=batch, capture_output=True
    )
    if proc.returncode != 0:
        raise CheckError(f"git cat-file failed: {proc.stderr.decode('utf-8', 'replace')}")
    data, files, pos = proc.stdout, {}, 0
    for path in paths:
        header_end = data.index(b"\n", pos)
        size = int(data[pos:header_end].split()[2])
        start = header_end + 1
        files[path] = data[start : start + size]
        pos = start + size + 1
    return files


def check_text(repo: Path, prints: Mapping[str, str | Sequence[str]]) -> list[Violation]:
    """(c) ``codes/<slug>/`` of ``main`` matches the print html of its latest edition."""
    out: list[Violation] = []
    for slug, html in prints.items():
        files = _main_files(repo, f"codes/{slug}/")
        result = verify_edition(files, html)
        if not result.ok:
            out.append(Violation("c", f"{slug}: {result.summary()}"))
            out += [Violation("c", f"{slug}: {d}") for d in result.diffs]
    return out


def check_branches(
    repo: Path, commits: Sequence[_Commit], steps: Sequence[Step]
) -> list[Violation]:
    """(d) each merged ``bill/*`` branch tip is merged into ``main`` exactly once."""
    merged = Counter(p for c in commits for p in c.parents[1:])
    out: list[Violation] = []
    for ref in sorted({s.branch for s in steps if s.kind == "merge" and s.branch}):
        try:
            tip = _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{ref}^{{commit}}")
        except CheckError:
            out.append(Violation("d", f"{ref}: branch is missing"))
            continue
        n = merged[tip.decode().strip()]
        if n != 1:
            out.append(Violation("d", f"{ref}: tip is merged into main {n} times"))
    return out


def check_repo(
    repo_path: Path, steps: Sequence[Step], prints: Mapping[str, str | Sequence[str]]
) -> list[Violation]:
    """All invariants of the repository at ``repo_path`` built from ``steps``.

    ``prints`` maps the slug of every code still in force to the print html of the latest
    edition of each of its documents (a list for КУпАП).
    """
    commits = _main_log(repo_path)
    violations = [
        *check_editions(commits, steps),
        *check_in_force(commits),
        *check_text(repo_path, prints),
        *check_branches(repo_path, commits, steps),
    ]
    return violations
