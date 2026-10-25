"""Text completeness invariant: rendered files hold exactly the words of the edition.

The edition side is the :func:`extract_paragraphs` output without ``header`` and ``note``
paragraphs (the document title box and editorial notes are not rendered). The rendered side
is every ``.md`` file of the code: articles, level headings (``provisions.md``), preamble
and closing. Both sides go through :func:`normalize_words`, so markdown markup, punctuation,
case, apostrophe variants and inline ``{…}`` notes do not matter.

Two known asymmetries are tolerated:

- an excluded-article stub (``# Стаття 5. Виключена`` + «Виключена на підставі …») is built
  from an editorial note that is not part of the edition text: its words may be ``extra``;
- an empty excluded level («Глава 1 виключена.» followed by a new «Глава 1 …») is dropped by
  the tree: its heading words may be ``missing``.

The check first compares word multisets (linear time). Only when they differ, paragraphs
are matched one by one to name the files where text was lost or added.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from jurik.zakon.paragraphs import Para, extract_paragraphs

MAX_DIFFS = 5

_NOTE = re.compile(r"\{[^{}]*\}")
_APOSTROPHES = re.compile(r"['’ʼ`]")
_WORD = re.compile(r"[^\W_]+")
_EXCLUDED_STUB = re.compile(r"^# Стаття [\d-]+\. (?:Виключена|Втратила чинність)\n")
_EXCLUDED_LEVEL = re.compile(r"^(виключен[аоий]|виключено)\.?$", re.I)
_TEXT_KINDS = ("body", "heading", "article_start")


def normalize_words(text: str) -> list[str]:
    """Words of ``text`` without ``{…}`` notes, punctuation, markup and case."""
    text = _NOTE.sub(" ", text)
    text = _APOSTROPHES.sub("", text)
    return _WORD.findall(text.casefold())


@dataclass(frozen=True)
class Discrepancy:
    kind: Literal["missing", "extra"]
    file: str  # rendered file that lost / gained the text ("" if unknown)
    text: str  # the paragraph
    context: str  # the paragraph before it (edition text for missing, file text for extra)

    def __str__(self) -> str:
        where = self.file or "?"
        ctx = f" (after: {_short(self.context)})" if self.context else ""
        return f"{self.kind} in {where}: {_short(self.text)}{ctx}"


@dataclass
class VerifyResult:
    words: int  # words in the edition text
    files: int  # rendered markdown files
    missing: Counter[str] = field(default_factory=Counter)
    extra: Counter[str] = field(default_factory=Counter)
    diffs: list[Discrepancy] = field(default_factory=list)  # first MAX_DIFFS

    @property
    def ok(self) -> bool:
        return not self.missing and not self.extra

    def summary(self) -> str:
        if self.ok:
            return f"ok ({self.words} words, {self.files} files)"
        return (
            f"FAILED: {self.missing.total()} words missing, {self.extra.total()} extra "
            f"({self.words} words, {self.files} files)"
        )


def _short(text: str, n: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _edition_paras(html: str | Sequence[str]) -> list[Para]:
    htmls = [html] if isinstance(html, str) else list(html)
    return [p for h in htmls for p in extract_paragraphs(h) if p.kind in _TEXT_KINDS]


def _md_files(files: Mapping[str, bytes]) -> dict[str, str]:
    return {p: c.decode("utf-8") for p, c in files.items() if p.endswith(".md")}


def _count(texts: Iterable[str]) -> Counter[str]:
    c: Counter[str] = Counter()
    for t in texts:
        c.update(normalize_words(t))
    return c


def verify_paragraphs(paras: Sequence[Para], files: Mapping[str, bytes]) -> VerifyResult:
    """Compare edition paragraphs (``extract_paragraphs`` output) with rendered files."""
    paras = [p for p in paras if p.kind in _TEXT_KINDS]
    md = _md_files(files)
    expected = _count(p.text for p in paras)
    rendered = _count(md.values())
    missing = expected - rendered
    extra = rendered - expected
    if extra:
        extra -= _count(t for t in md.values() if _EXCLUDED_STUB.match(t))
    if missing:
        missing -= _count(
            p.text for p in paras if p.kind == "heading" and _EXCLUDED_LEVEL.match(p.title)
        )
    result = VerifyResult(words=expected.total(), files=len(md), missing=missing, extra=extra)
    if not result.ok:
        result.diffs = _locate(paras, md, missing, extra)
    return result


def verify_edition(files: Mapping[str, bytes], html: str | Sequence[str]) -> VerifyResult:
    """Check that ``files`` (``render`` output) hold exactly the text of the edition ``html``.

    ``html`` may be several documents rendered into one code folder (КУпАП).
    """
    return verify_paragraphs(_edition_paras(html), files)


def _blocks(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if b.strip()]


def _locate(
    paras: Sequence[Para],
    md: Mapping[str, str],
    missing: Counter[str],
    extra: Counter[str],
) -> list[Discrepancy]:
    """Match paragraphs to file blocks to name where words were lost or added."""
    where: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    blocks: dict[str, list[str]] = {}
    for path in sorted(md):
        blocks[path] = _blocks(md[path])
        for i, b in enumerate(blocks[path]):
            where.setdefault(tuple(normalize_words(b)), []).append((path, i))
    used: set[tuple[str, int]] = set()
    diffs: list[Discrepancy] = []
    file = ""
    prev = ""
    for p in paras:
        key = tuple(normalize_words(p.text))
        hits = [h for h in where.get(key, []) if h not in used]
        if hits:
            used.add(hits[0])
            file = hits[0][0]
        elif key and missing.keys() & set(key) and len(diffs) < MAX_DIFFS:
            diffs.append(Discrepancy("missing", file, p.text, prev))
        prev = p.text
    for path, bs in blocks.items():
        for i, b in enumerate(bs):
            if len(diffs) >= MAX_DIFFS:
                return diffs
            words = set(normalize_words(b))
            if (path, i) not in used and words & extra.keys():
                diffs.append(Discrepancy("extra", path, b, bs[i - 1] if i else ""))
    return diffs
