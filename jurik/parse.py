"""Parse cached editions of a code and check text completeness (``jurik parse``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jurik.config import Code
from jurik.render import RenderError, render
from jurik.verify import VerifyResult, verify_paragraphs
from jurik.zakon.card import CardParseError, parse_card
from jurik.zakon.http import ZakonClient, read_gz
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import TreeError, build_tree


@dataclass
class EditionCheck:
    slug: str
    zakon_id: str
    edition: str  # "YYYYMMDD" or "card"
    result: VerifyResult | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.result is not None and self.result.ok

    def line(self) -> str:
        what = self.error or (self.result.summary() if self.result else "")
        return f"{self.slug} {self.zakon_id} ed{self.edition}: {what}"


def _cached(client: ZakonClient, path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not cached, run `jurik fetch` first")
    return read_gz(path)


def parse_code(
    code: Code,
    client: ZakonClient,
    *,
    edition: str | None = None,
    all_editions: bool = False,
    echo: Callable[[str], None] = print,
) -> list[EditionCheck]:
    """Parse, render and verify editions of ``code`` from the cache only (no downloads).

    Default: the latest in-force edition of each document; ``edition`` picks one date,
    ``all_editions`` every in-force edition. Each document of a multi-document code (КУпАП)
    is checked on its own.
    """
    checks: list[EditionCheck] = []
    for zakon_id in code.zakon_ids:
        try:
            card = parse_card(_cached(client, client.card_path(zakon_id)), zakon_id)
        except (FileNotFoundError, CardParseError) as e:
            checks.append(EditionCheck(code.slug, zakon_id, "card", error=str(e)))
            echo(checks[-1].line())
            continue
        editions = card.current()
        if edition:
            editions = [e for e in editions if e.date.strftime("%Y%m%d") == edition]
            if not editions:
                checks.append(EditionCheck(code.slug, zakon_id, edition, error="no such edition"))
                echo(checks[-1].line())
                continue
        elif not all_editions:
            editions = editions[-1:]
        for ed in editions:
            ed_date = ed.date.strftime("%Y%m%d")
            check = EditionCheck(code.slug, zakon_id, ed_date)
            try:
                html = _cached(client, client.edition_path(zakon_id, ed_date))
                paras = extract_paragraphs(html)
                files = render(build_tree(paras, ed), code, adopted=card.adopted)
                check.result = verify_paragraphs(paras, files)
            except (FileNotFoundError, TreeError, RenderError, ValueError) as e:
                check.error = f"{type(e).__name__}: {e}"
            checks.append(check)
            echo(check.line())
            if check.result:
                for d in check.result.diffs:
                    echo(f"  {d}")
    return checks


def failures_report(checks: list[EditionCheck]) -> str:
    """Markdown for ``report/parse-failures.md``."""
    failed = [c for c in checks if not c.ok]
    lines = ["# Parse failures", ""]
    if not failed:
        lines.append(f"No failures in {len(checks)} edition(s).")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(failed)} of {len(checks)} edition(s) failed.")
    for c in failed:
        lines += ["", f"## {c.slug} {c.zakon_id} ed{c.edition}", ""]
        if c.error:
            lines.append(f"- error: {c.error}")
            continue
        assert c.result is not None
        r = c.result
        lines.append(f"- {r.summary()}")
        if r.missing:
            lines.append(f"- missing: {_top(r.missing)}")
        if r.extra:
            lines.append(f"- extra: {_top(r.extra)}")
        lines += [f"- {d}" for d in r.diffs]
    return "\n".join(lines) + "\n"


def _top(words, n: int = 10) -> str:
    return ", ".join(f"{w}×{k}" for w, k in words.most_common(n))
