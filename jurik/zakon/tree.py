"""Build a :class:`DocTree` from classified edition paragraphs.

Levels nest by rank (book > part > section > subsection > chapter > paragraph): a heading
closes every open level of its rank or lower and opens a new one. An article collects
``body`` paragraphs until the next article or heading; ``body`` after a heading but outside
an article goes to ``Unit.provisions``; ``body`` before the first heading/article is the
preamble. ``note`` and ``header`` paragraphs are dropped; a trailing signature block goes
to ``DocTree.closing``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from jurik.model import Article, DocTree, Edition, Unit, UnitKind
from jurik.zakon.paragraphs import Para

RANK: dict[UnitKind, int] = {
    "book": 0,
    "part": 1,
    "section": 2,
    "subsection": 3,
    "chapter": 4,
    "paragraph": 5,
}

EXCLUDED_TITLE = "Виключена"

_EXCLUDED_WORD = re.compile(r"^(виключен[аоий]|виключено)\.?$", re.I)
# "{Статтю 196 виключено на підставі Закону № 3585-IX від 22.02.2024 }"
_EXCLUSION_NOTE = re.compile(
    r"^[/\-\s]*\{\s*Статт[юя]\s+(?P<num>\d+(?:\s*-\s*\d+)?)\s+виключено\s*(?P<basis>[^{}]*?)\s*\.?\s*\}?$",
    re.I | re.S,
)
# first paragraph of the closing block: signature line/table or the "***" separator
_CLOSING_START = re.compile(r"^(?:\|\s*)?(?:Президент України|Голова Верховної Ради)\b|^\*\*\*$")


class TreeError(ValueError):
    """The paragraphs do not form a consistent document tree."""


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)


def _exclusion_basis(text: str, number: str) -> str | None:
    """``"Виключена на підставі …"`` if ``text`` is an exclusion note for article ``number``."""
    m = _EXCLUSION_NOTE.match(text)
    if not m or re.sub(r"\s*-\s*", "-", m.group("num")) != number:
        return None
    basis = m.group("basis").strip().rstrip(".").strip()
    return f"{EXCLUDED_TITLE} {basis}." if basis else f"{EXCLUDED_TITLE}."


def _closing_start(paras: Sequence[Para]) -> int:
    """Index of the trailing signature block (only body/note after it), ``len`` if none."""
    start = len(paras)
    for i in range(len(paras) - 1, -1, -1):
        p = paras[i]
        if p.kind not in ("body", "note"):
            break
        if p.kind == "body" and _CLOSING_START.match(p.text):
            start = i
    return start


class _Builder:
    def __init__(self) -> None:
        self.top: list[Unit | Article] = []
        self.stack: list[Unit] = []
        self.preamble: list[str] = []
        self.provisions: dict[int, list[str]] = {}  # id(unit) -> paragraphs
        self.article: Article | None = None
        self.article_body: list[str] = []
        self.article_notes: list[str] = []
        self.seen_articles: dict[str, int] = {}

    # -- containers --

    def _siblings(self) -> list[Unit | Article]:
        return self.stack[-1].children if self.stack else self.top

    def _close_article(self) -> None:
        art = self.article
        if art is None:
            return
        art.body = _join(self.article_body)
        basis = next(
            (b for n in self.article_notes if (b := _exclusion_basis(n, art.number))), None
        )
        # "Стаття 5. Виключена" or an article whose only content is the exclusion note
        if art.excluded or (basis and not art.body and not art.title):
            art.excluded = True
            art.title = EXCLUDED_TITLE
            if not art.body:
                art.body = basis or ""
        self.article = None
        self.article_body = []
        self.article_notes = []

    # -- events --

    def heading(self, p: Para, pos: int) -> None:
        self._close_article()
        assert p.unit_kind is not None
        rank = RANK[p.unit_kind]
        while self.stack and RANK[self.stack[-1].kind] >= rank:
            self.stack.pop()
        siblings = self._siblings()
        unit = Unit(kind=p.unit_kind, number=p.number, title=p.title)
        for i, other in enumerate(siblings):
            if isinstance(other, Unit) and (other.kind, other.number) == (unit.kind, unit.number):
                if (
                    not other.children
                    and not self.provisions.get(id(other))
                    and (_EXCLUDED_WORD.match(other.title))
                ):
                    del siblings[i]  # Tax XIV: "Глава 1 виключена." then "Глава 1 СПРОЩЕНА …"
                    break
                raise TreeError(
                    f"duplicate {unit.kind} {unit.number} at paragraph {pos}"
                    f"{f' (#{p.anchor})' if p.anchor else ''}"
                )
        siblings.append(unit)
        self.stack.append(unit)

    def article_start(self, p: Para, pos: int) -> None:
        self._close_article()
        if p.number in self.seen_articles:
            raise TreeError(
                f"duplicate article {p.number} at paragraph {pos}"
                f"{f' (#{p.anchor})' if p.anchor else ''}, "
                f"first at paragraph {self.seen_articles[p.number]}"
            )
        self.seen_articles[p.number] = pos
        title = p.title
        excluded = bool(_EXCLUDED_WORD.match(title))
        if not excluded and _exclusion_basis(title, p.number):
            # ЦК: "Стаття 196. {Статтю 196 виключено на підставі Закону № …}"
            excluded = True
            self.article_notes.append(title)
        self.article = Article(number=p.number, title=title, body="", excluded=excluded)
        self._siblings().append(self.article)

    def body(self, text: str) -> None:
        if self.article is not None:
            self.article_body.append(text)
        elif self.stack:
            self.provisions.setdefault(id(self.stack[-1]), []).append(text)
        else:
            self.preamble.append(text)

    def note(self, text: str) -> None:
        if self.article is not None:
            self.article_notes.append(text)

    def finish(self) -> tuple[list[Unit | Article], str]:
        self._close_article()

        def fill(items: list[Unit | Article]) -> None:
            for item in items:
                if isinstance(item, Unit):
                    item.provisions = _join(self.provisions.get(id(item), []))
                    fill(item.children)

        fill(self.top)
        return self.top, _join(self.preamble)


def build_tree(paragraphs: Sequence[Para], edition: Edition) -> DocTree:
    """Assemble the document tree of one edition from :func:`extract_paragraphs` output."""
    end = _closing_start(paragraphs)
    b = _Builder()
    for pos, p in enumerate(paragraphs[:end]):
        if p.kind == "heading":
            b.heading(p, pos)
        elif p.kind == "article_start":
            b.article_start(p, pos)
        elif p.kind == "body":
            b.body(p.text)
        elif p.kind == "note":
            b.note(p.text)
        # header: dropped
    units, preamble = b.finish()
    closing = _join([p.text for p in paragraphs[end:] if p.kind == "body"])
    return DocTree(
        zakon_id=edition.zakon_id,
        edition=edition,
        preamble=preamble,
        units=units,
        closing=closing,
    )
