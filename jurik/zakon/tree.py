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

from jurik.errors import JurikError
from jurik.model import Article, DocTree, Edition, Unit, UnitKind
from jurik.zakon.paragraphs import EXCLUDED_WORD, NOTE, Para, norm_index

RANK: dict[UnitKind, int] = {
    "final": -1,  # unnumbered «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ»: closes every level
    "appendix": -1,  # «Додаток N» of a statute: closes every level
    "book": 0,
    "part": 1,
    "section": 2,
    "subsection": 3,
    "chapter": 4,
    "paragraph": 5,
    "group": 6,
    "topic": 6,
}

EXCLUDED_TITLE = "Виключена"
LAPSED_TITLE = "Втратила чинність"

# "{Статтю 196 виключено на підставі Закону № 3585-IX від 22.02.2024 }"; the source also has
# "{С таттю 115 виключено …}" (split word), "{Cтаттю 990 …}" (Latin C) and
# "{Стаття 209 втратила чинність з 1 січня 2017 року - див. …}"
_EXCLUSION_NOTE = re.compile(
    r"^[/\-\s]*\{\s*[СC]\s*татт[юя]\s+(?P<num>\d+(?:\s*-\s*\d+)?)\s+"
    r"(?P<verb>виключено|втратила\s+чинність)\s*(?P<basis>[^{}]*?)\s*\.?\s*\}?$",
    re.I | re.S,
)
# inline editorial note inside a text paragraph: "174.5. {Абзац перший … виключено …}"
_INLINE_NOTE = re.compile(r"\s*" + NOTE.pattern)
# title of a level of final/transitional provisions («ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ»)
_FINAL_TITLE = re.compile(r"прикінцев|перехідн", re.I)
# first paragraph of the closing block: signature line/table or the "***" separator
_CLOSING_START = re.compile(r"^(?:\|\s*)?(?:Президент України|Голова Верховної Ради)\b|^\*\*\*$")


class TreeError(JurikError):
    """The paragraphs do not form a consistent document tree."""


def _where(p: Para, pos: int) -> str:
    """``paragraph 12 (#n34)``: position of ``p`` for error messages."""
    return f"paragraph {pos}" + (f" (#{p.anchor})" if p.anchor else "")


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)


def strip_notes(text: str) -> str:
    """Remove inline ``{…}`` editorial notes (the plan: notes are not part of the text)."""
    if "{" not in text:
        return text
    prev = None
    while prev != text:
        prev, text = text, _INLINE_NOTE.sub("", text)
    # keep line breaks (markdown tables), collapse spaces left by the removed notes
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _exclusion(text: str, number: str) -> tuple[str, str] | None:
    """``(title, body)`` of the stub if ``text`` is an exclusion note for article ``number``.

    ``("Виключена", "Виключена на підставі …")`` or ``("Втратила чинність", "Втратила
    чинність з … - див. …")``.
    """
    m = _EXCLUSION_NOTE.match(text)
    if not m or norm_index(m.group("num")) != number:
        return None
    title = EXCLUDED_TITLE if m.group("verb").lower() == "виключено" else LAPSED_TITLE
    basis = " ".join(m.group("basis").split()).rstrip(".").strip()
    return title, f"{title} {basis}." if basis else f"{title}."


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


def _key(unit: Unit) -> tuple[str, str]:
    """Identity of a level among its siblings (unnumbered final provisions: by title)."""
    return unit.kind, unit.number or unit.title.casefold()


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
        self.last_article: Article | None = None

    # -- containers --

    def _siblings(self) -> list[Unit | Article]:
        return self.stack[-1].children if self.stack else self.top

    def _close_article(self) -> None:
        art = self.article
        if art is None:
            return
        art.body = _join(self.article_body)
        stub = next((x for n in self.article_notes if (x := _exclusion(n, art.number))), None)
        # "Стаття 5. Виключена" or an article whose only content is the exclusion note
        if art.excluded or (stub and not art.body and not art.title):
            art.excluded = True
            if not art.title or EXCLUDED_WORD.match(art.title):
                art.title = stub[0] if stub else EXCLUDED_TITLE
            if not art.body:
                art.body = stub[1] if stub else ""
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
        unit = Unit(kind=p.unit_kind, number=p.number, title=strip_notes(p.title))
        for i, other in enumerate(siblings):
            if isinstance(other, Unit) and _key(other) == _key(unit):
                if (
                    not other.children
                    and not self.provisions.get(id(other))
                    and (EXCLUDED_WORD.match(other.title))
                ):
                    del siblings[i]  # Tax XIV: "Глава 1 виключена." then "Глава 1 СПРОЩЕНА …"
                    break
                raise TreeError(f"duplicate {unit.kind} {unit.number} at {_where(p, pos)}")
        siblings.append(unit)
        self.stack.append(unit)

    def _in_final_provisions(self) -> bool:
        return any(u.kind == "final" or _FINAL_TITLE.search(u.title) for u in self.stack)

    def article_start(self, p: Para, pos: int) -> None:
        if p.number in self.seen_articles and self.article is None and self._in_final_provisions():
            # amending text quoted in the final provisions («статті 26-28 викласти в такій
            # редакції: "Стаття 26 …" Стаття 27. …», Виборчий кодекс): not a code article
            self.body(p.text)
            return
        self._close_article()
        if p.number in self.seen_articles and self.stack and self._lead_articles_to_preamble():
            self.seen_articles = {}
        variant = 0
        last = self.last_article
        if p.number in self.seen_articles and last is not None and last.number == p.number:
            # consecutive articles with one number: a real collision in the source
            variant = max(last.variant, 1) + 1
        elif p.number in self.seen_articles:
            raise TreeError(
                f"duplicate article {p.number} at {_where(p, pos)}, "
                f"first at paragraph {self.seen_articles[p.number]}"
            )
        self.seen_articles.setdefault(p.number, pos)
        title = p.title
        excluded = bool(EXCLUDED_WORD.match(title))
        if not excluded and (stub := _exclusion(title, p.number)):
            # ЦК: "Стаття 196. {Статтю 196 виключено на підставі Закону № …}"
            excluded = True
            self.article_notes.append(title)
            title = stub[0]
        self.article = Article(
            number=p.number, title=strip_notes(title), body="", excluded=excluded, variant=variant
        )
        self.last_article = self.article
        self._siblings().append(self.article)

    def _lead_articles_to_preamble(self) -> bool:
        """Articles before the first level belong to an enacting act printed before the
        code (КЗпП 1996–1998: «Закон УРСР Про затвердження Кодексу … Стаття 1. Затвердити
        Кодекс …»): when the code's own numbering repeats them, they become preamble text.
        Only applies when every top-level item before the first level is such an article."""
        lead: list[Article] = []
        for item in self.top:
            if isinstance(item, Unit):
                break
            lead.append(item)
        if not lead or len(lead) == len(self.top):
            return False
        for art in lead:
            text = f"Стаття {art.number}. {art.title}".rstrip()
            self.preamble.extend([text, art.body] if art.body else [text])
        del self.top[: len(lead)]
        return True

    def body(self, text: str) -> None:
        text = strip_notes(text)
        if not text:
            return
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
    closing = _join([t for p in paragraphs[end:] if p.kind == "body" if (t := strip_notes(p.text))])
    return DocTree(
        zakon_id=edition.zakon_id,
        edition=edition,
        preamble=preamble,
        units=units,
        closing=closing,
    )
