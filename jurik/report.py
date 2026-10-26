"""Coverage report (``jurik report`` → ``report/coverage.md``).

Per code: in-force editions, how many parse and pass the completeness check, how many have
a law basis found in Rada open data (a bill) and a recorded vote, the share of law bases
without ``LawMeta``, and the branches of bills that did not become law (in the built
repository, and candidates matched by title in the index).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from jurik.build.repo import RepoError, git, main_log
from jurik.config import Code
from jurik.export import BillBranch, unmerged_bills
from jurik.model import Edition
from jurik.parse import EditionCheck
from jurik.rada.index import LawIndex
from jurik.rada.match import bills_for_code
from jurik.zakon.card import base_kind

CONCERNS = "Стосується:"


@dataclass
class CodeCoverage:
    slug: str
    title: str
    editions: int  # in-force editions of all documents
    cached: int  # editions whose print is in cache/zakon/
    parsed: int  # editions that parse and pass the completeness check
    with_bill: int | None  # editions with a law basis whose bill is in open data
    with_votes: int | None  # editions with a law basis that has a recorded vote
    laws: int  # distinct law bases (nreg)
    laws_without_meta: int | None  # law bases missing from the index
    branches: int | None  # unmerged bill branches in the repository concerning the code
    bills_by_title: int | None  # not adopted bills matched by title in the index


@dataclass
class RepoStats:
    main_commits: int  # --first-parent main
    bill_branches: int  # refs/heads/bill/*
    bills: list[BillBranch]  # bill branches never merged (bills that did not become law)

    @property
    def unmerged(self) -> int:
        return len(self.bills)


def code_coverage(
    code: Code,
    editions: Sequence[Edition],
    checks: Sequence[EditionCheck],
    index: LawIndex | None,
    branches: int | None = None,
) -> CodeCoverage:
    """Coverage of one code; ``index=None`` (no open data) leaves its columns empty."""
    checks = [c for c in checks if c.edition != "card"]
    cached = len(editions) - sum(c.not_cached for c in checks)
    parsed = sum(c.ok for c in checks)
    laws = list(dict.fromkeys(b for ed in editions for b in ed.bases if base_kind(b) == "law"))
    with_bill = with_votes = without_meta = by_title = None
    if index is not None:
        with_bill = with_votes = 0
        for ed in editions:
            metas = [m for b in ed.bases if base_kind(b) == "law" and (m := index.law(b))]
            with_bill += any(m.bill is not None for m in metas)
            with_votes += any(m.votes is not None for m in metas)
        without_meta = sum(index.law(b) is None for b in laws)
        by_title = sum(
            m.status != "adopted" and m.bill is not None for m in bills_for_code(index, code)
        )
    return CodeCoverage(
        slug=code.slug,
        title=code.title,
        editions=len(editions),
        cached=cached,
        parsed=parsed,
        with_bill=with_bill,
        with_votes=with_votes,
        laws=len(laws),
        laws_without_meta=without_meta,
        branches=branches,
        bills_by_title=by_title,
    )


def branches_by_code(bills: Iterable[BillBranch], codes: Iterable[Code]) -> dict[str, int]:
    """Unmerged bill branches per code slug, from the «Стосується: …» line of the message."""
    by_title = {c.title: c.slug for c in codes}
    out = dict.fromkeys(by_title.values(), 0)
    for bill in bills:
        for line in bill.message.splitlines():
            if line.startswith(CONCERNS):
                for title in line.removeprefix(CONCERNS).strip().rstrip(".").split(", "):
                    if (slug := by_title.get(title.strip())) is not None:
                        out[slug] += 1
    return out


def repo_stats(repo: Path) -> RepoStats | None:
    """Commit and branch counts of a built repository; None if it is not one."""
    if not (repo / ".git").exists():
        return None
    try:
        commits = main_log(repo)
        refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/bill/").split()
        bills = unmerged_bills(repo)
    except RepoError:
        return None
    return RepoStats(main_commits=len(commits), bill_branches=len(refs), bills=bills)


def _n(value: int | None) -> str:
    return "—" if value is None else str(value)


def _share(part: int | None, whole: int) -> str:
    """``9 (90%)`` of a table cell; ``—`` when unknown."""
    if part is None:
        return "—"
    return f"{part} ({100 * part / whole:.0f}%)" if whole else str(part)


def coverage_markdown(
    rows: Sequence[CodeCoverage],
    *,
    repo: RepoStats | None = None,
    repo_path: str = "build/laws",
    open_data: Sequence[str] = (),
    missing: Sequence[str] = (),
) -> str:
    """Markdown of ``report/coverage.md``."""
    lines = ["# Coverage", ""]
    lines += [
        "| Code | Editions | Cached | Parsed | With bill in open data | With vote | Law bases"
        " | Laws without LawMeta | Bill branches (not law) | Not adopted bills by title |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def total(attr: str) -> int | None:
        values = [getattr(r, attr) for r in rows]
        return None if any(v is None for v in values) else sum(values)

    for r in rows:
        lines.append(
            f"| {r.slug} | {r.editions} | {_share(r.cached, r.editions)}"
            f" | {_share(r.parsed, r.editions)}"
            f" | {_share(r.with_bill, r.editions)} | {_share(r.with_votes, r.editions)}"
            f" | {r.laws} | {_share(r.laws_without_meta, r.laws)} | {_n(r.branches)}"
            f" | {_n(r.bills_by_title)} |"
        )
    if rows:
        eds, laws = total("editions") or 0, total("laws") or 0
        lines.append(
            f"| **total** | {eds} | {_share(total('cached'), eds)} | {_share(total('parsed'), eds)}"
            f" | {_share(total('with_bill'), eds)} | {_share(total('with_votes'), eds)}"
            f" | {laws} | {_share(total('laws_without_meta'), laws)} | {_n(total('branches'))}"
            f" | {_n(total('bills_by_title'))} |"
        )
    lines += [
        "",
        "Editions = in-force editions of all documents of the code. Cached = the print is in"
        " `cache/zakon/` (`jurik fetch`). Parsed = parse and pass the text completeness"
        " check (failures of cached editions: `report/parse-failures.md`). Law bases are"
        " counted once per law; a bill can concern several codes, so totals of the last two"
        " columns may count a bill more than once.",
    ]
    if missing:
        lines += ["", f"Not fetched (no cached card): {', '.join(missing)}."]
    lines += ["", "## Repository", ""]
    if repo is None:
        lines.append(f"`{repo_path}` is not built.")
    else:
        lines += [
            f"- `{repo_path}`: {repo.main_commits} commits in `--first-parent main`",
            f"- `bill/*` branches: {repo.bill_branches}"
            f" ({repo.bill_branches - repo.unmerged} merged, {repo.unmerged} without merge)",
        ]
    lines += ["", "## Rada open data", ""]
    if open_data:
        lines += [f"- {line}" for line in open_data]
    else:
        lines.append("No law index (run `jurik rada`).")
    return "\n".join(lines) + "\n"
