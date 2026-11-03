"""Dataset export from a built laws repository (``jurik export``).

The state at a date is the last commit of ``git log --first-parent main`` whose
``In-force:`` trailer is on or before that date. Committer dates are adoption dates, not
in-force dates, so ``git log --before`` would pick the wrong commit.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast, get_args

import yaml

from jurik.build.message import (
    BILL,
    EDITION,
    REGISTERED,
    STATUS,
    parse_edition_id,
    parse_trailers,
    status_label,
)
from jurik.build.refs import MAIN
from jurik.build.repo import MainCommit, RepoError, git, in_force_of, main_log
from jurik.convocation import label
from jurik.errors import JurikError
from jurik.model import LawStatus
from jurik.rada.index import parse_day

MANIFEST = "manifest.json"
SOURCES = ["https://zakon.rada.gov.ua", "https://data.rada.gov.ua"]


class ExportError(JurikError):
    """Nothing to export or the output directory cannot be used."""


@dataclass
class ExportResult:
    commit: str
    in_force: date | None
    codes: list[str]  # slugs of codes/<slug>
    files: int
    bills: list[str] = field(default_factory=list)  # exported bill files
    laws: list[str] = field(default_factory=list)  # slugs of laws/<slug>
    editions: dict[str, str] = field(default_factory=dict)  # zakon_id → edYYYYMMDD


def commits_at(repo: Path, at: date) -> list[MainCommit]:
    """``--first-parent main`` commits up to the last one with ``In-force:`` ≤ ``at``
    (oldest first; empty if none is in force on ``at``)."""
    commits = main_log(repo)
    last = -1
    for i, c in enumerate(commits):
        d = in_force_of(c)
        if d is not None and d <= at:
            last = i
    return commits[: last + 1]


def commit_at(repo: Path, at: date) -> MainCommit | None:
    """Last ``--first-parent main`` commit with ``In-force:`` ≤ ``at`` (None if none)."""
    commits = commits_at(repo, at)
    return commits[-1] if commits else None


def latest_editions(commits: list[MainCommit]) -> dict[str, str]:
    """zakon_id → its latest ``Edition:`` (``edYYYYMMDD``) along ``commits``."""
    out: dict[str, str] = {}
    for c in commits:
        for key, value in c.trailers:
            if key == EDITION and (parsed := parse_edition_id(value)):
                zakon_id, stamp = parsed
                out[zakon_id] = f"ed{stamp}"
    return out


@dataclass(frozen=True)
class BillBranch:
    ref: str
    tip: str
    convocation: int
    number: str
    status: LawStatus | None  # None: no or unknown Status: trailer
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
        conv, _, number = trailers.get(BILL, "").partition("/")
        status = trailers.get(STATUS, "")
        bills.append(
            BillBranch(
                ref=ref,
                tip=tip,
                convocation=int(conv) if conv.isdigit() else 0,
                number=number,
                status=cast(LawStatus, status) if status in get_args(LawStatus) else None,
                registered=parse_day(trailers.get(REGISTERED, "")),
                message=message,
            )
        )
    return bills


def bill_banner(bill: BillBranch) -> str:
    """Warning line put on top of an exported bill file, with the bill's real status."""
    status = status_label(bill.status) if bill.status else "невідомо"
    return (
        f"> Законопроект № {bill.number} ({label(bill.convocation)} скликання), статус: {status}."
        " Не є чинним законом.\n\n"
    )


def is_previous_export(out: Path) -> bool:
    """``out/manifest.json`` is a manifest written by :func:`export`."""
    try:
        manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(manifest, dict)
        and manifest.get("sources") == SOURCES
        and isinstance(manifest.get("commit"), str)
        and "in_force" in manifest
    )


def _check_out(out: Path) -> None:
    """``out`` must be absent, empty or a previous export (it is then replaced)."""
    if out.exists():
        if not out.is_dir():
            raise ExportError(f"{out} is not a directory")
        if any(out.iterdir()) and not is_previous_export(out):
            raise ExportError(f"{out} is not empty and is not a previous export")


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _swap_in(tmp: Path, out: Path) -> None:
    """Replace ``out`` (absent, empty or a previous export) by the complete ``tmp``."""
    if not out.exists():
        tmp.rename(out)
        return
    old = Path(tempfile.mkdtemp(prefix=f".{out.name}.old-", dir=out.parent))
    old.rmdir()
    out.rename(old)
    try:
        tmp.rename(out)
    except OSError:
        old.rename(out)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _extract(repo: Path, rev: str, out: Path, paths: list[str] | None = None) -> list[str]:
    data = git(repo, "archive", "--format=tar", rev, *(["--", *paths] if paths else []))
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
        tar.extractall(out, filter="data")
    return names


def _zakon_ids(out: Path, folders: list[str]) -> set[str]:
    """zakon ids named in ``<folder>/_meta.yaml`` of the exported codes and laws."""
    ids: set[str] = set()
    for folder in folders:
        try:
            meta = yaml.safe_load((out / folder / "_meta.yaml").read_text("utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        value = meta.get("zakon_id") if isinstance(meta, dict) else None
        ids.update([value] if isinstance(value, str) else value or [])
    return ids


def _slugs(names: list[str], top: str) -> list[str]:
    """Slugs of the ``<top>/<slug>/`` folders among the exported file ``names``."""
    return sorted({n.split("/")[1] for n in names if n.startswith(f"{top}/") and n.count("/") > 1})


def export(repo: Path, at: date, out: Path, *, with_bills: bool = False) -> ExportResult:
    """Write the ``main`` tree in force on ``at`` into ``out`` with ``manifest.json``.

    ``with_bills`` adds ``bills/**/*.md`` of bill branches without merge registered on or
    before ``at`` (unknown registration date: included), each prefixed with a warning that
    it is not law.
    """
    try:
        commits = commits_at(repo, at)
    except RepoError as e:
        raise ExportError(str(e)) from e
    if not commits:
        raise ExportError(f"no commit of {MAIN} is in force on {at.isoformat()}")
    _check_out(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # written into a sibling directory and swapped in only when complete, so a failed
    # export leaves the previous one intact
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.tmp-", dir=out.parent))
    try:
        tmp.chmod(0o777 & ~_umask())  # mkdtemp makes it 0700
        result = _write(repo, at, commits, tmp, with_bills=with_bills)
        _swap_in(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return result


def _write(
    repo: Path, at: date, commits: list[MainCommit], out: Path, *, with_bills: bool
) -> ExportResult:
    """Write the export of ``commits[-1]`` into the empty directory ``out``."""
    commit = commits[-1]
    sha = commit.sha
    try:
        names = _extract(repo, sha, out)
    except RepoError as e:
        raise ExportError(str(e)) from e
    codes, laws = (_slugs(names, top) for top in ("codes", "laws"))
    result = ExportResult(sha, in_force_of(commit), codes, len(names), laws=laws)
    # the edition of each document still in the tree (a repealed code's folder is gone)
    folders = [f"codes/{s}" for s in codes] + [f"laws/{s}" for s in laws]
    exported_ids = _zakon_ids(out, folders)
    result.editions = {z: e for z, e in latest_editions(commits).items() if z in exported_ids}
    if with_bills:
        for bill in sorted(unmerged_bills(repo), key=lambda b: (b.convocation, b.ref)):
            if bill.registered and bill.registered > at:
                continue
            changed = git(
                repo, "diff-tree", "--root", "--no-commit-id", "-r", "-z", "--name-only", bill.tip
            )
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
        "laws": laws,
        "editions": result.editions,
        "files": result.files,
        "sources": SOURCES,
    }
    if with_bills:
        manifest["bills"] = result.bills
    (out / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result
