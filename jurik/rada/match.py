"""Which bills concern which code.

- Adopted laws are linked to a code through the bases (підстави) of the code's editions,
  never through titles: a law «Про внесення змін до деяких законодавчих актів» can amend
  a code without naming it, and a title can name a code the law does not change in the end.
- Bills that did not become law (rejected, withdrawn, removed, pending) have no editions, so
  they are matched by title against ``title_patterns`` from ``codes.yaml`` (regexes for the
  code name in any grammatical case). Known limitation: omnibus bills «до деяких законодавчих
  актів» that do not name the code are not found; ``omnibus_unmatched`` measures their share.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache

from jurik.config import Code
from jurik.model import LawMeta
from jurik.rada.index import LawIndex

OMNIBUS_RE = re.compile(r"деяких\s+(інших\s+)?законодавчих\s+актів")


def normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, unify apostrophes."""
    t = re.sub(r"[’ʼ`‘]", "'", title or "")
    return re.sub(r"\s+", " ", t).strip().lower()


@cache
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def title_matches(title: str, code: Code) -> bool:
    t = normalize_title(title)
    return any(p.search(t) for p in _compile(code.title_patterns))


def match_codes(title: str, codes: Iterable[Code]) -> list[str]:
    """Slugs of every code whose name occurs in the title."""
    return [c.slug for c in codes if title_matches(title, c)]


def laws_for_bases(index: LawIndex, bases: Iterable[str]) -> list[LawMeta]:
    """Adopted laws among edition bases (nreg); bases not in the index are skipped."""
    out: list[LawMeta] = []
    seen: set[str] = set()
    for nreg in bases:
        meta = index.law(nreg)
        if meta and meta.law_number not in seen:
            seen.add(meta.law_number)
            out.append(meta)
    return out


def bills_for_code(index: LawIndex, code: Code, bases: Iterable[str] = ()) -> list[LawMeta]:
    """Bills of all statuses concerning ``code``.

    Adopted laws come from ``bases`` (the підстави of the code's editions); every other bill
    is matched by title. Adopted laws whose title names the code but which are not among the
    bases are left out: they did not produce an edition of this code.
    """
    laws = laws_for_bases(index, bases)
    others = [
        m
        for m in index.bills.values()
        if m.status != "adopted" and title_matches(m.law_title, code)
    ]
    return laws + others


def omnibus_unmatched(index: LawIndex, codes: Iterable[Code]) -> tuple[int, int]:
    """``(unmatched, total)`` of not-adopted omnibus bills («до деяких законодавчих актів»).

    Unmatched ones name no code in the title, so ``bills_for_code`` cannot find them even
    if they were about a code.
    """
    codes = list(codes)
    omnibus = [
        m
        for m in index.bills.values()
        if m.status != "adopted" and OMNIBUS_RE.search(normalize_title(m.law_title))
    ]
    unmatched = sum(1 for m in omnibus if not match_codes(m.law_title, codes))
    return unmatched, len(omnibus)


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "0/0"


def coverage_report(
    index: LawIndex, codes: Iterable[Code], bases_by_code: dict[str, list[str]]
) -> list[str]:
    """Human-readable coverage lines for ``jurik rada``.

    ``bases_by_code`` maps a code slug to the law bases (nreg) of its editions; codes without
    a cached card are absent and get no line.
    """
    codes = list(codes)
    lines = []
    total = [0, 0, 0, 0]
    for n in index.convocations:
        bills = [m for m in index.bills.values() if m.bill and m.bill.convocation == n]
        laws = [m for m in index.by_law.values() if m.bill and m.bill.convocation == n]
        voted = sum(m.votes is not None for m in laws)
        initiated = sum(bool(m.initiators) for m in laws)
        for i, v in enumerate((len(bills), len(laws), voted, initiated)):
            total[i] += v
        lines.append(
            f"skl{n}: law bills {len(bills)}, laws {len(laws)},"
            f" with votes {_pct(voted, len(laws))}, with initiators {_pct(initiated, len(laws))}"
        )
    lines.append(
        f"total: law bills {total[0]}, laws {total[1]}, with votes {_pct(total[2], total[1])},"
        f" with initiators {_pct(total[3], total[1])}"
    )
    for code in codes:
        if code.slug not in bases_by_code:
            continue
        bases = list(dict.fromkeys(bases_by_code[code.slug]))
        found = laws_for_bases(index, bases)
        other = [m for m in bills_for_code(index, code) if m.status != "adopted"]
        lines.append(
            f"{code.slug}: edition law bases found {_pct(len(found), len(bases))},"
            f" not-adopted bills by title {len(other)}"
        )
    unmatched, omnibus = omnibus_unmatched(index, codes)
    lines.append(
        f"omnibus bills «до деяких законодавчих актів» naming no code (not matched):"
        f" {_pct(unmatched, omnibus)}"
    )
    return lines
