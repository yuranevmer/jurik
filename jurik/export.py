"""Dataset export from a built laws repository (``jurik export``).

The state at a date is the last commit of ``git log --first-parent main`` whose
``In-force:`` trailer is on or before that date. Committer dates are adoption dates, not
in-force dates, so ``git log --before`` would pick the wrong commit.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jurik.build.check import CheckError, git, in_force_of, main_log
from jurik.build.history import MAIN, ROMAN
from jurik.build.message import parse_trailers, status_label

MANIFEST = "manifest.json"
SOURCES = ["https://zakon.rada.gov.ua", "https://data.rada.gov.ua"]


class ExportError(RuntimeError):
    """Nothing to export or the output directory cannot be used."""


@dataclass
class ExportResult:
    commit: str
    in_force: date | None
    codes: list[str]
    files: int
    bills: list[str] = field(default_factory=list)  # exported bill files


def commit_at(repo: Path, at: date) -> str | None:
    """Last ``--first-parent main`` commit with ``In-force:`` ≤ ``at`` (None if none)."""
    found: str | None = None
    for c in main_log(repo):
        d = in_force_of(c)
        if d is not None and d <= at:
            found = c.sha
    return found


@dataclass(frozen=True)
class BillBranch:
    ref: str
    tip: str
    convocation: int
    number: str
    status: str
    registered: date | None
    message: str = ""


def unmerged_bills(repo: Path) -> list[BillBranch]:
    """``bill/*`` branches never merged into ``main`` (bills that did not become law)."""
    out = git(
        repo,
        "for-each-ref",
        f"--no-merged={MAIN}",
        "--format=%(refname:short)%00%(objectname)",
        "refs/heads/bill/",
    )
    bills: list[BillBranch] = []
    for line in out.decode("utf-8").splitlines():
        if not line.strip():
            continue
        ref, tip = line.split("\x00")
        message = git(repo, "log", "-1", "--format=%B", tip).decode("utf-8")
        trailers = dict(parse_trailers(message))
        conv, _, number = trailers.get("Bill", "").partition("/")
        registered = trailers.get("Registered")
        bills.append(
            BillBranch(
                ref,
                tip,
                int(conv) if conv.isdigit() else 0,
                number,
                trailers.get("Status", ""),
                date.fromisoformat(registered) if registered else None,
                message,
            )
        )
    return bills


def bill_banner(bill: BillBranch) -> str:
    """Warning line put on top of an exported bill file, with the bill's real status."""
    conv = ROMAN.get(bill.convocation, str(bill.convocation))
    status = status_label(bill.status) if bill.status else "невідомо"
    return (
        f"> Законопроект № {bill.number} ({conv} скликання), статус: {status}."
        " Не є чинним законом.\n\n"
    )


def _prepare_out(out: Path) -> None:
    """``out`` must be absent, empty or a previous export (it is then replaced)."""
    if out.exists():
        if not out.is_dir():
            raise ExportError(f"{out} is not a directory")
        if any(out.iterdir()):
            if not (out / MANIFEST).is_file():
                raise ExportError(f"{out} is not empty and is not a previous export")
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)


def _extract(repo: Path, rev: str, out: Path, paths: list[str] | None = None) -> list[str]:
    data = git(repo, "archive", "--format=tar", rev, *(["--", *paths] if paths else []))
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
        tar.extractall(out, filter="data")
    return names


def export(repo: Path, at: date, out: Path, *, with_bills: bool = False) -> ExportResult:
    """Write the ``main`` tree in force on ``at`` into ``out`` with ``manifest.json``.

    ``with_bills`` adds ``bills/**/*.md`` of bill branches without merge registered on or
    before ``at`` (unknown registration date: included), each prefixed with a warning that
    it is not law.
    """
    try:
        sha = commit_at(repo, at)
    except CheckError as e:
        raise ExportError(str(e)) from e
    if sha is None:
        raise ExportError(f"no commit of {MAIN} is in force on {at.isoformat()}")
    commit = next(c for c in main_log(repo) if c.sha == sha)
    _prepare_out(out)
    names = _extract(repo, sha, out)
    codes = sorted({n.split("/")[1] for n in names if n.startswith("codes/") and n.count("/") > 1})
    result = ExportResult(sha, in_force_of(commit), codes, len(names))
    if with_bills:
        for bill in sorted(unmerged_bills(repo), key=lambda b: (b.convocation, b.ref)):
            if bill.registered and bill.registered > at:
                continue
            changed = git(repo, "diff-tree", "--no-commit-id", "-r", "-z", "--name-only", bill.tip)
            for path in changed.decode("utf-8").split("\x00"):
                if not (path.startswith("bills/") and path.endswith(".md")):
                    continue
                text = git(repo, "show", f"{bill.tip}:{path}").decode("utf-8")
                dest = out / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(bill_banner(bill) + text, encoding="utf-8")
                result.bills.append(path)
    manifest = {
        "at": at.isoformat(),
        "commit": sha,
        "in_force": result.in_force.isoformat() if result.in_force else None,
        "codes": codes,
        "files": result.files,
        "sources": SOURCES,
    }
    if with_bills:
        manifest["bills"] = result.bills
    (out / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result
