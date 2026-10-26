"""Invariants of a built laws repository.

(a) every edition of the plan is in exactly one commit of ``main`` (``Edition:`` trailer)
    and that commit's ``In-force:`` is the In-force date the plan gave it (the edition
    date, or the document's entry into force for editions dated before it);
(b) ``In-force:`` along ``git log --first-parent main`` never decreases;
(c) the ``main`` files of each code hold exactly the words of the print of its latest
    edition (:func:`jurik.verify.verify_edition`);
(d) every ``bill/*`` branch that the plan merges is merged into ``main`` exactly once at its
    tip (the tip is the second parent of exactly one first-parent commit of ``main``);
(e) every merge of ``main`` changes exactly what its branch commit changes:
    ``git diff M^2^ M^2`` == ``git diff M^1 M`` (so ``git log``/``git blame`` of a file
    never skip the ``main`` commits between the branch base and the merge).

Bill branch commits carry no ``In-force:``/``Edition:``, so (a) and (b) walk only the
first-parent history of ``main``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from jurik.build.gitcmd import GitError, run_git
from jurik.build.history import MAIN, Step
from jurik.build.message import edition_id, parse_trailers
from jurik.verify import verify_edition

Check = Literal["a", "b", "c", "d", "e"]


@dataclass(frozen=True)
class Violation:
    check: Check
    message: str

    def __str__(self) -> str:
        return f"({self.check}) {self.message}"


@dataclass
class MainCommit:
    sha: str
    parents: list[str]
    trailers: list[tuple[str, str]]
    subject: str


class CheckError(GitError):
    """The repository cannot be read by git."""


def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    return run_git(repo, *args, input=input, error=CheckError)


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
    for key, value in c.trailers:
        if key == "In-force":
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def check_editions(commits: Sequence[MainCommit], steps: Sequence[Step]) -> list[Violation]:
    """(a) every planned edition is in exactly one ``main`` commit with its In-force date."""
    expected = {edition_id(e): s.date for s in steps if s.ref == MAIN for e in s.editions}
    seen: Counter[str] = Counter()
    out: list[Violation] = []
    for c in commits:
        in_force = in_force_of(c)
        for key, value in c.trailers:
            if key != "Edition":
                continue
            seen[value] += 1
            if value not in expected:
                out.append(Violation("a", f"{c.sha[:10]}: edition {value} is not in the plan"))
            elif expected[value] != in_force:
                out.append(Violation("a", f"{c.sha[:10]}: edition {value} has In-force {in_force}"))
    for key in sorted(expected - seen.keys()):
        out.append(Violation("a", f"edition {key} has no commit in main"))
    for key, n in sorted(seen.items()):
        if n > 1:
            out.append(Violation("a", f"edition {key} is in {n} commits of main"))
    return out


def check_in_force(commits: Sequence[MainCommit]) -> list[Violation]:
    """(b) every first-parent ``main`` commit has ``In-force:`` and it never decreases."""
    out: list[Violation] = []
    prev: date | None = None
    for c in commits:
        d = in_force_of(c)
        if d is None:
            out.append(Violation("b", f"{c.sha[:10]} {c.subject!r}: no valid In-force"))
            continue
        if prev and d < prev:
            out.append(Violation("b", f"{c.sha[:10]} {c.subject!r}: In-force {d} < {prev}"))
        prev = d
    return out


def _main_files(repo: Path, prefix: str) -> dict[str, bytes]:
    """``.md`` files of ``main`` under ``prefix`` (read from git, not the work tree)."""
    names = git(repo, "ls-tree", "-r", "-z", "--name-only", MAIN, "--", prefix)
    paths = [p for p in names.decode("utf-8").split("\x00") if p.endswith(".md")]
    if not paths:
        return {}
    batch = "".join(f"{MAIN}:{p}\n" for p in paths).encode("utf-8")
    data, files, pos = git(repo, "cat-file", "--batch", input=batch), {}, 0
    for path in paths:
        header_end = data.index(b"\n", pos)
        size = int(data[pos:header_end].split()[2])
        start = header_end + 1
        files[path] = data[start : start + size]
        pos = start + size + 1
    return files


def check_text(repo: Path, prints: Mapping[str, Sequence[str]]) -> list[Violation]:
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
    repo: Path, commits: Sequence[MainCommit], steps: Sequence[Step]
) -> list[Violation]:
    """(d) each merged ``bill/*`` branch tip is merged into ``main`` exactly once."""
    merged = Counter(p for c in commits for p in c.parents[1:])
    out: list[Violation] = []
    for ref in sorted({s.branch for s in steps if s.kind == "merge" and s.branch}):
        try:
            tip = git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{ref}^{{commit}}")
        except CheckError:
            out.append(Violation("d", f"{ref}: branch is missing"))
            continue
        n = merged[tip.decode().strip()]
        if n != 1:
            out.append(Violation("d", f"{ref}: tip is merged into main {n} times"))
    return out


def _diffs(repo: Path, pairs: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Raw ``git diff-tree -r`` lines of each ``(old, new)`` commit pair, in one git call."""
    if not pairs:
        return []
    batch = "".join(f"{new} {old}\n" for old, new in pairs).encode()
    out = git(repo, "diff-tree", "--stdin", "-r", "--no-renames", "--always", input=batch)
    diffs: list[list[str]] = []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith(":"):
            diffs[-1].append(line)
        elif line.strip():  # header: the sha of ``new``
            diffs.append([])
    if len(diffs) != len(pairs):
        raise CheckError(f"git diff-tree: {len(diffs)} diffs for {len(pairs)} pairs")
    return diffs


def check_merge_diffs(repo: Path, commits: Sequence[MainCommit]) -> list[Violation]:
    """(e) each merge's first-parent diff equals the diff of its branch commit."""
    merges = [c for c in commits if len(c.parents) == 2]
    if not merges:
        return []
    bases = git(repo, "rev-parse", *(f"{c.parents[1]}^" for c in merges)).decode().split()
    branch = _diffs(repo, [(b, c.parents[1]) for b, c in zip(bases, merges, strict=True)])
    merge = _diffs(repo, [(c.parents[0], c.sha) for c in merges])
    out: list[Violation] = []
    for c, bd, md in zip(merges, branch, merge, strict=True):
        if sorted(bd) != sorted(md):
            n = len(set(bd) ^ set(md))
            out.append(
                Violation(
                    "e",
                    f"{c.sha[:10]} {c.subject!r}: branch diff differs from the merge diff "
                    f"({n} line(s))",
                )
            )
    return out


def check_repo(
    repo_path: Path, steps: Sequence[Step], prints: Mapping[str, Sequence[str]]
) -> list[Violation]:
    """All invariants of the repository at ``repo_path`` built from ``steps``.

    ``prints`` maps the slug of every code still in force to the print html of the latest
    edition of each of its documents (a list for КУпАП).
    """
    commits = main_log(repo_path)
    violations = [
        *check_editions(commits, steps),
        *check_in_force(commits),
        *check_text(repo_path, prints),
        *check_branches(repo_path, commits, steps),
        *check_merge_diffs(repo_path, commits),
    ]
    return violations
