"""Split an edition HTML into classified paragraphs.

The edition HTML comes from the OpenData API (``data.rada.gov.ua/laws/show/<nreg>/ed<date>``):
the document text lives in ``<div id=article>`` as a flat sequence of
``<p><a name="nNNN"></a>…</p>`` paragraphs (no ``rvps`` classes) and
``<div><table>…</table></div>`` blocks. Older editions have plain ``<p>`` without anchors.

Structure is recognised by text patterns only:

- headings: ``Книга перша`` / ``КНИГА ПЕРША …``, ``Загальна частина`` / ``Особлива частина``,
  ``Розділ I …`` / ``РОЗДІЛ I. …``, ``Підрозділ 1. …``, ``Глава 1 …`` / ``ГЛАВА 1. …``,
  ``§ 1. …`` (also dotless ``§ 2 Назва``), unnumbered ``ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ``
  (``final``), unlabelled ``2. Повне товариство`` inside a § right before an article
  (``group``). The title is on the same line or, in old editions, in the next paragraph.
  A heading whose title is only an exclusion note (``§ 2. {Параграф 2 … виключено …}``) is a
  ``note``;
- article start: ``Стаття 39 - 1 . Назва`` (index with spaces, as OpenData renders
  superscripts) or ``Стаття 39-1. Назва``;
- editorial notes ``{…}``, possibly spanning several paragraphs;
- the document header (title table, official publication reference) before the first
  heading/article.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from lxml import html as lxml_html
from lxml.html import HtmlElement

from jurik.model import UnitKind

ParaKind = Literal["heading", "article_start", "body", "note", "header"]


class ParagraphWarning(UserWarning):
    """The HTML has no recognisable document text."""


@dataclass(frozen=True)
class Para:
    text: str
    kind: ParaKind
    anchor: str = ""  # "n123" from <a name="n123">, "" if absent
    unit_kind: UnitKind | None = None  # heading only
    number: str = ""  # heading / article_start: "I", "1-1", "39-1"
    title: str = ""  # heading / article_start


# --- text normalisation --------------------------------------------------------

_WS = re.compile(r"\s+")
# OpenData has no superscript markup: an index 39¹ is rendered as "39 - 1 " (spaces on both
# sides, so it reads like a range). A space before punctuation marks the superscript:
# "Стаття 39 - 1 ." -> "39-1.", point "3 - 1 )" -> "3-1)", "статті 49 - 2 , " -> "49-2, ";
# a two-level index (КК «Стаття 96 - 10-1 .») -> "96-10-1.". A genuine range never has a
# space before punctuation («статтями 73 - 76, 79 - 83», «у 2012 - 2015 роках» stay).
# after a hyphen only a point number continues («…2.1-39 - 2 .3.2.4»): «212-11 - 212-20 )»
# is a range
_SUP_INDEX = re.compile(r"(?<![\d,])(?:(?<!-)|(?=[\d -]+ \.\d))(\d+) - (\d+(?:-\d+)?) ([.,;:)])")
# Before a word the spacing does not tell a superscript from a range: «статтею 6 - 1 згідно»,
# «статтями 255 - 1 і 255 - 2 цього», but «статтях 51 - 53 цього», «у 2012 - 2015 роках».
# A range always grows (M > N), so "N - M" with M <= N is an index; M > N is an index only
# right after a reference word that is unambiguously singular («пунктом 13 - 14 частини»).
# A dotted base compares its last number and never takes the singular-word exception
# («підпунктом 14.1.1 - 1 згідно» is an index, «підпунктах 54.3.1 - 54.3.6» a range).
_SUP_BEFORE_WORD = re.compile(r"(?<![\w,.-])((?:\d+\.)*)(\d+) - (\d+)(-\d+)?(?= [^\s-]| - |$)")
_SINGULAR_REF = re.compile(
    r"(?:\b(?:стат(?:тя|тю|тею)|(?:під)?пункт(?:ом|у|і)?|глав(?:а|у|ою|і)|"
    r"(?:під)?розділ(?:у|ом|і)?) )$",
    re.I,
)
_INDEX = r"\d+(?:\s*-\s*\d+){0,2}"


def _join_sup_before_word(m: re.Match[str]) -> str:
    dotted, base, sup, sub = m[1], int(m[2]), int(m[3]), m[4] or ""
    if sup < base or (
        not sub and (sup == base or (not dotted and _SINGULAR_REF.search(m.string, 0, m.start())))
    ):
        return f"{m[1]}{m[2]}-{m[3]}{sub}"
    return m[0]


def normalize_text(text: str) -> str:
    """Collapse whitespace (incl. nbsp), glue superscript indices, keep apostrophes as is."""
    text = _WS.sub(" ", text.replace("\xa0", " ")).strip()
    text = _SUP_INDEX.sub(r"\1-\2\3", text)
    return _SUP_BEFORE_WORD.sub(_join_sup_before_word, text)


def norm_index(number: str) -> str:
    """``39 - 1`` → ``39-1``: an article/level index without spaces around dashes."""
    return re.sub(r"\s*-\s*", "-", number.strip())


# --- patterns ------------------------------------------------------------------

ORDINALS = {
    "перша": 1,
    "друга": 2,
    "третя": 3,
    "четверта": 4,
    "п'ята": 5,
    "п’ята": 5,
    "шоста": 6,
    "сьома": 7,
    "восьма": 8,
    "дев'ята": 9,
    "дев’ята": 9,
    "десята": 10,
}
_ORDINAL_RE = "|".join(re.escape(o) for o in ORDINALS)

_ARTICLE = re.compile(rf"^Стаття\s+({_INDEX})\s*\.\s*(.*)$", re.S)
_BOOK = re.compile(rf"^Книга\s+({_ORDINAL_RE})\b\.?\s*(.*)$", re.I | re.S)
_PART = re.compile(r"^(Загальна|Особлива)\s+частина\.?\s*(.*)$", re.I | re.S)
_TAIL = r"(?=[\s.]|$)\s*(\.?)\s*(.*)$"
# Митний кодекс mixes Cyrillic І/Х into Roman numerals: «Розділ XVІ», «Розділ XІX»
_SECTION = re.compile(rf"^Розділ\s+([IVXLCІХ]+(?:\s*-\s*\d+)?){_TAIL}", re.I | re.S)
_CYRILLIC_ROMAN = str.maketrans("ІХіх", "IXix")
_SECTION_NUMERAL = re.compile(r"^((?:Розділ|Глава)\s+)([IVXLCІХ]+)(?=[\s.\-]|$)", re.I)
_SUBSECTION = re.compile(rf"^Підрозділ\s+({_INDEX}){_TAIL}", re.I | re.S)
# КЗпП: Roman chapter numbers with letter suffixes «Глава III-А», «ГЛАВА III-Б»
_CHAPTER = re.compile(
    rf"^Глава\s+((?:\d+|[IVXLCІХ]+)(?:\s*-\s*(?:\d+|[А-ЯІЇЄҐ]))?){_TAIL}", re.I | re.S
)
_PARAGRAPH = re.compile(rf"^§\s*({_INDEX}){_TAIL}", re.S)
# "{…}", also "/- {Зміни …, прийняті Законом …}" (pending amendment marker)
_NOTE_START = re.compile(r"^[/\-\s]*\{")
# inline editorial note: "{Статтю 196 виключено …}" (shared with the tree and the verifier)
NOTE = re.compile(r"\{[^{}]*\}")
# unnumbered top-level heading: «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ», «ПЕРЕХІДНІ ПОЛОЖЕННЯ»
_FINAL = re.compile(
    r"^(?:ПРИКІНЦЕВІ|ПЕРЕХІДНІ)(?:\s+(?:ТА|І|Й)\s+(?:ПРИКІНЦЕВІ|ПЕРЕХІДНІ))?\s+ПОЛОЖЕННЯ\.?$"
)
# КУпАП розділ II: «I. ЗАГАЛЬНА ЧАСТИНА», «II. ОСОБЛИВА ЧАСТИНА» group chapters inside the
# section, so they are a level of subsection rank (a ``part`` would swallow later sections)
_SECTION_PART = re.compile(r"^(I|II)\.\s+((?:ЗАГАЛЬНА|ОСОБЛИВА)\s+ЧАСТИНА)$")
# a level or article title that is only «Виключена.» / «виключено»
EXCLUDED_WORD = re.compile(r"^(виключен[аоий]|виключено)\.?$", re.I)
# unlabelled sub-heading inside a § right before an article: «2. Повне товариство»
_GROUP = re.compile(r"^(\d+)\.\s+([А-ЯІЇЄҐ][^.;:,!?]{0,100})$")


def _is_upper(text: str) -> bool:
    text = NOTE.sub("", text)
    return any(c.isalpha() for c in text) and text == text.upper()


_NUMBERED: tuple[tuple[UnitKind, re.Pattern[str]], ...] = (
    ("section", _SECTION),
    ("subsection", _SUBSECTION),
    ("chapter", _CHAPTER),
    ("paragraph", _PARAGRAPH),
)


def is_excluded_heading(text: str) -> bool:
    """A level heading whose title is only an editorial note:
    «Розділ XVIII - 2 . {Розділ XVIII - 2 виключено …}», «§ 4. {Параграф 4 глави 74 виключено …}».
    """
    for _, rx in _NUMBERED:
        if (m := rx.match(text)) and (title := (m.group(3) or "").strip()):
            return (
                title.startswith("{")
                and not NOTE.sub("", title).strip()
                and bool(re.search(r"виключ|втрати", title, re.I))
            )
    return False


def _title_ok(kind: UnitKind, dot: str, title: str) -> bool:
    """Heading title guard that keeps body sentences like «Розділ V "…" зберігає …» out."""
    if not title:
        return True
    if _is_upper(title) or EXCLUDED_WORD.match(title):
        return True
    # "Підрозділ 1. Особливості …", "§ 1. Господарські товариства"; ЦК also has a dotless
    # "§ 2 Правові наслідки недодержання …" (a body sentence never starts with "§ N")
    if kind == "chapter" and not dot:
        # Земельний кодекс: «Глава 5 Землі сільськогосподарського призначення» (no dot); a
        # body sentence ends with punctuation
        return title[0].isupper() and title[-1] not in ".;:,!?"
    return (bool(dot) or kind == "paragraph") and title[0].isupper()


def match_heading(text: str) -> tuple[UnitKind, str, str] | None:
    """Return ``(unit_kind, number, title)`` if ``text`` is a structural heading."""
    if _FINAL.match(text):
        return "final", "", text.rstrip(".")
    if m := _BOOK.match(text):
        rest = m.group(2).strip()
        if not rest or _is_upper(rest):
            return "book", str(ORDINALS[m.group(1).lower()]), rest
        return None
    if m := _SECTION_PART.match(text):
        return "subsection", m.group(1), m.group(2)
    if m := _PART.match(text):
        rest = m.group(2).strip()
        if rest and not _is_upper(rest):
            return None
        number = "1" if m.group(1).lower() == "загальна" else "2"
        return "part", number, " ".join(f"{m.group(1)} частина".split()).capitalize()
    for kind, rx in _NUMBERED:
        if m := rx.match(text):
            title = (m.group(3) or "").strip()
            if not _title_ok(kind, m.group(2), title):
                return None
            number = norm_index(m.group(1))
            if kind in ("section", "chapter") and not number[0].isdigit():
                head, sep, idx = number.partition("-")
                number = head.upper().translate(_CYRILLIC_ROMAN) + sep + idx.upper()
            return kind, number, title  # type: ignore[return-value]
    return None


def match_article(text: str) -> tuple[str, str] | None:
    """Return ``(number, rest)`` if ``text`` starts an article."""
    if m := _ARTICLE.match(text):
        return norm_index(m.group(1)), m.group(2).strip()
    return None


# --- html walking ---------------------------------------------------------------


@dataclass
class _Block:
    text: str
    anchor: str
    is_table: bool = False


# a linked superscript is a separate link: «<a>статтями 166</a><a> - 10</a>»
_SUP_LINK = re.compile(r"^ +- +(\d+)")
# a dotted base («підпунктами 39.2.1.1») is followed by a range («- 39.2.1.3») unless the index
# does not grow (Податковий кодекс «пункті 43.4</a><a> - 1»)
_DOTTED_BASE = re.compile(r"\d\.(\d+)$")


def _text_chunks(el: HtmlElement) -> Iterator[tuple[HtmlElement, str]]:
    """``(node, attr)`` for every text chunk of ``el`` in document order."""
    if isinstance(el.tag, str):  # comments / processing instructions carry no text
        yield el, "text"
        for child in el:
            yield from _text_chunks(child)
    yield el, "tail"


def _join_split_superscripts(el: HtmlElement) -> None:
    """Glue a superscript index that the source put into its own element to its base:
    «<a>статтями 166</a><a> - 10</a>» -> «статтями 166-10». A range is one text chunk
    («<a>статтями 73 - 76</a>»), so it is not touched; a dash right after a glued index is
    a range again («<a>127</a><a> - 1</a><a> - 128</a><a> - 1</a>» -> «127-1 - 128-1»)."""
    prev = ""
    after_sup = False
    for node, attr in _text_chunks(el):
        if node is el and attr == "tail":
            break
        text = getattr(node, attr) or ""
        if not text:
            continue
        sup = attr == "text" and _SUP_LINK.match(text)
        base = _DOTTED_BASE.search(prev)
        if (
            sup
            and prev[-1:].isdigit()
            and not after_sup
            and (not base or int(sup[1]) <= int(base[1]))
        ):
            text = node.text = _SUP_LINK.sub(r"-\1", text, count=1)
            after_sup = text[1:].isdigit()  # the whole chunk is the index
        else:
            after_sup = False
        prev = text


def _element_text(el: HtmlElement) -> str:
    for br in el.iter("br"):
        br.tail = " " + (br.tail or "")
    _join_split_superscripts(el)
    return normalize_text(el.text_content())


def _anchor(el: HtmlElement) -> str:
    for a in el.iter("a"):
        name = a.get("name")
        if name:
            return name
    return ""


def _cell_text(cell: HtmlElement) -> str:
    return _element_text(cell).replace("|", r"\|")


def table_to_text(table: HtmlElement) -> list[str]:
    """Convert a table to text blocks.

    Single-column tables (title boxes, signatures) yield one block per non-empty cell.
    Rectangular tables become one markdown table, others one block of ``a | b`` lines.
    """
    rows = []
    spans = False
    for tr in table.iter("tr"):
        cells = [c for c in tr if c.tag in ("td", "th")]
        if not cells:
            continue
        spans = spans or any(
            int(c.get("colspan", "1") or 1) > 1 or int(c.get("rowspan", "1") or 1) > 1
            for c in cells
        )
        rows.append([_cell_text(c) for c in cells])
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    widths = {len(r) for r in rows}
    if widths == {1}:
        return [r[0] for r in rows]
    if len(widths) == 1 and not spans:
        width = widths.pop()
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return ["\n".join(lines)]
    return ["\n".join(" | ".join(r) for r in rows)]


def _blocks(container: HtmlElement) -> list[_Block]:
    out: list[_Block] = []
    for el in container:
        if not isinstance(el.tag, str):
            continue
        tables = [el] if el.tag == "table" else list(el.iter("table"))
        if tables:
            anchor = _anchor(el)
            for table in tables:
                if next(table.iterancestors("table"), None) is not None:
                    continue  # nested table, rendered by the outer one
                for i, text in enumerate(table_to_text(table)):
                    out.append(_Block(text, anchor if i == 0 else "", is_table=True))
            continue
        text = _element_text(el)
        if text:
            out.append(_Block(text, _anchor(el)))
    return out


# --- classification ---------------------------------------------------------------


def _doc_title(doc: HtmlElement) -> str:
    for xp in ("//h1", "//title"):
        found = doc.xpath(xp)
        if found:
            return normalize_text(found[0].text_content())
    return ""


def _is_header_text(text: str, title: str) -> bool:
    if text == "[ image ]":
        return True
    if title and text.casefold() == title.casefold():
        return True
    return text.startswith("(") and text.endswith(")")


def _next_text(blocks: list[_Block], i: int) -> str:
    """Text of the first block from ``i`` on that is not an editorial note ("" at the end)."""
    for b in blocks[i:]:
        if b.is_table or not _NOTE_START.match(b.text):
            return b.text
    return ""


def extract_paragraphs(html: str, *, titled_articles: bool | None = None) -> list[Para]:
    """Split edition HTML into classified paragraphs.

    ``titled_articles``: whether the text after ``Стаття N.`` is the article title (codes)
    or the first sentence of the article (the Constitution has untitled articles). ``None``
    detects it from the document title: untitled for «Конституція …». For untitled articles
    the rest of the paragraph is emitted as a separate ``body`` right after ``article_start``.
    """
    doc = lxml_html.fromstring(html)
    containers = doc.xpath('//div[@id="article"]')
    if not containers:
        warnings.warn("no <div id=article> with document text", ParagraphWarning, stacklevel=2)
        return []
    title = _doc_title(doc)
    if titled_articles is None:
        titled_articles = not title.casefold().startswith("конституція")

    blocks = _blocks(containers[0])
    paras: list[Para] = []
    note_depth = 0
    note_open = False
    seen_structure = False
    level: UnitKind | None = None  # kind of the last heading
    i = 0
    while i < len(blocks):
        b = blocks[i]
        i += 1
        text = b.text
        if note_open or (_NOTE_START.match(text) and not b.is_table):
            # a note may span several paragraphs ("{У тексті Кодексу:" / "слова …;" / "…}");
            # some notes in the source lack the closing brace, so only a list-like
            # paragraph (ending with ":" or ";") keeps an unclosed note open
            note_depth = max(note_depth + _open_braces(text), 0)
            note_open = note_depth > 0 and text.endswith((":", ";"))
            if not note_open:
                note_depth = 0
            paras.append(Para(text, "note", b.anchor))
            continue
        if b.is_table:
            kind = "header" if not seen_structure and _is_header_text(text, title) else "body"
            paras.append(Para(text, kind, b.anchor))
            continue
        if is_excluded_heading(text):
            # an excluded level leaves only its heading with a note; it holds no text
            paras.append(Para(text, "note", b.anchor))
            continue
        if art := match_article(text):
            seen_structure = True
            number, rest = art
            if titled_articles or not rest:
                paras.append(Para(text, "article_start", b.anchor, number=number, title=rest))
            else:
                paras.append(Para(f"Стаття {number}.", "article_start", b.anchor, number=number))
                paras.append(Para(rest, "body", b.anchor))
            continue
        if head := match_heading(text):
            seen_structure = True
            level = head[0]
            para, i = _heading_para(b, head, blocks, i)
            paras.append(para)
            continue
        if level in ("paragraph", "group") and (group := _group_para(b, blocks, i)):
            level = "group"
            paras.append(group)
            continue
        kind = "header" if not seen_structure and _is_header_text(text, title) else "body"
        paras.append(Para(text, kind, b.anchor))

    if not paras:
        warnings.warn("document text has no paragraphs", ParagraphWarning, stacklevel=2)
    return _join_split_notes(paras)


def _heading_para(
    b: _Block, head: tuple[UnitKind, str, str], blocks: list[_Block], i: int
) -> tuple[Para, int]:
    """The heading paragraph of block ``b`` (``blocks[i]`` is the next one) and the index of
    the block after it: an untitled heading takes an upper-case next line as its title
    («РОЗДІЛ I» + «ЗАГАЛЬНІ ПОЛОЖЕННЯ»)."""
    unit_kind, number, htitle = head
    text = b.text
    if unit_kind in ("section", "chapter"):  # the heading text carries Latin numerals
        text = _SECTION_NUMERAL.sub(lambda m: m[1] + m[2].translate(_CYRILLIC_ROMAN), text)
    if not htitle and i < len(blocks):
        nxt = blocks[i]
        if (
            not nxt.is_table
            and not _NOTE_START.match(nxt.text)
            and _is_upper(nxt.text)
            and (not match_heading(nxt.text) or _FINAL.match(nxt.text))
            and not match_article(nxt.text)
        ):
            htitle = nxt.text
            text = f"{text} {htitle}"
            i += 1
    para = Para(text, "heading", b.anchor, unit_kind=unit_kind, number=number, title=htitle)
    return para, i


def _group_para(b: _Block, blocks: list[_Block], i: int) -> Para | None:
    """An unlabelled numbered sub-heading right before an article, if ``b`` is one (ЦК § 1
    «Господарські товариства»: «1. Загальні положення», «2. Повне товариство» …)."""
    m = _GROUP.match(b.text)
    if not m or not match_article(_next_text(blocks, i)):
        return None
    return Para(
        b.text, "heading", b.anchor, unit_kind="group", number=m.group(1), title=m.group(2).strip()
    )


def _open_braces(text: str) -> int:
    return text.count("{") - text.count("}")


def _join_split_notes(paras: list[Para]) -> list[Para]:
    """Glue a body paragraph ending in an unclosed ``{`` note to the body paragraph that
    closes it (КУпАП 2009–2012: «… доходів громадян. { Кодекс доповнено статтею 186-3 згідно
    із Законом» + «N 2992-12 від 04.02.93, … від 13.07.2000 }»), so the note is one inline
    note that the tree strips."""
    out: list[Para] = []
    for p in paras:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev.kind == "body"
            and p.kind == "body"
            and _open_braces(prev.text) > 0
            and _open_braces(p.text) < 0
        ):
            out[-1] = Para(f"{prev.text} {p.text}", "body", prev.anchor)
            continue
        out.append(p)
    return out
