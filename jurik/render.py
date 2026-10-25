"""Render document trees into the markdown file layout of build/laws."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import yaml

from jurik.config import Code
from jurik.model import Article, DocTree, Unit
from jurik.translit import roman_to_int, slug
from jurik.zakon.paragraphs import ORDINALS

SOURCE_URL = "https://zakon.rada.gov.ua/laws/show/{}"

UNIT_LABEL = {
    "final": "",
    "book": "Книга",
    "part": "Частина",
    "section": "Розділ",
    "subsection": "Підрозділ",
    "chapter": "Глава",
    "paragraph": "§",
    "group": "",
}


# "1" -> "перша": the book heading keeps the ordinal word of the source text
BOOK_ORDINAL = {str(n): word for word, n in reversed(ORDINALS.items())}


class RenderError(ValueError):
    """Two items of a code map to the same file."""


def _number_parts(number: str) -> list[str]:
    parts = [p.strip() for p in number.split("-")]
    if not parts or not all(parts):
        raise RenderError(f"bad number {number!r}")
    return parts


def unit_prefix(number: str) -> str:
    """'I' → '01', 'XIV' → '14', '1-1' → '01-1', 'XVIII-1' → '18-1', 'III-А' → '03-a'."""
    head, *rest = _number_parts(number)
    n = int(head) if head.isdigit() else roman_to_int(head)
    return "-".join([f"{n:02d}", *(p if p.isdigit() else slug(p) for p in rest)])


def unit_dir(unit: Unit) -> str:
    s = slug(unit.title)
    if not unit.number:  # unnumbered final provisions: "prykintsevi-ta-perekhidni-polozhennia"
        if not s:
            raise RenderError(f"unnumbered {unit.kind} without title")
        return s
    prefix = unit_prefix(unit.number)
    return f"{prefix}-{s}" if s else prefix


def article_file(number: str) -> str:
    """'1' → 'art-0001.md', '39-1' → 'art-0039-1.md'."""
    head, *rest = _number_parts(number)
    if not head.isdigit():
        raise RenderError(f"bad article number {number!r}")
    return "-".join([f"art-{int(head):04d}", *rest]) + ".md"


def unit_heading(unit: Unit) -> str:
    title = unit.title.strip()
    if not unit.number or (unit.kind == "part" and "частина" in title.lower()):
        return f"# {title}"
    if unit.kind == "group":  # "# 2. Повне товариство", as in the source
        return f"# {unit.number}. {title}"
    number = BOOK_ORDINAL.get(unit.number, unit.number) if unit.kind == "book" else unit.number
    head = f"{UNIT_LABEL[unit.kind]} {number}"
    return f"# {head}. {title}" if title else f"# {head}"


def _doc(*blocks: str) -> bytes:
    text = "\n\n".join(b.strip() for b in blocks if b.strip())
    return (text + "\n").encode("utf-8")


def article_text(art: Article) -> bytes:
    head = f"# Стаття {art.number}."
    if art.title.strip():
        head += f" {art.title.strip()}"
    return _doc(head, art.body)


def _put(files: dict[str, bytes], path: str, content: bytes) -> None:
    if path in files:
        raise RenderError(f"duplicate path {path}")
    files[path] = content


def _render_items(items: list[Unit | Article], base: str, files: dict[str, bytes]) -> None:
    for item in items:
        if isinstance(item, Article):
            name = article_file(item.number)
            if item.variant:
                name = name.removesuffix(".md") + f"_{item.variant}.md"
            _put(files, f"{base}/{name}", article_text(item))
            continue
        path = f"{base}/{unit_dir(item)}"
        _put_level(files, f"{path}/provisions.md", unit_heading(item), item.provisions)
        _render_items(item.children, path, files)


def _put_level(files: dict[str, bytes], path: str, heading: str, provisions: str) -> None:
    """Every level gets ``provisions.md``: its heading, then the level text (if any).

    A level present in two merged documents (КУпАП) yields the same heading twice; a
    heading-only file then gives way to the one with text, two texts are a conflict.
    """
    content = _doc(heading, provisions)
    old = files.get(path)
    if old is None or old == _doc(heading):
        files[path] = content
    elif content != _doc(heading) and content != old:
        raise RenderError(f"duplicate path {path}")


def _joined(texts: list[str]) -> str:
    seen: list[str] = []
    for t in texts:
        t = t.strip()
        if t and t not in seen:
            seen.append(t)
    return "\n\n".join(seen)


def meta_yaml(trees: Sequence[DocTree], code: Code, *, adopted: date | None, status: str) -> bytes:
    ids = [t.zakon_id for t in trees]
    meta = {
        "title": code.title,
        "zakon_id": ids[0] if len(ids) == 1 else ids,
        "adopted": adopted,
        "source_url": SOURCE_URL.format(ids[0])
        if len(ids) == 1
        else [SOURCE_URL.format(i) for i in ids],
        "status": status,
        "edition_date": max(t.edition.date for t in trees),
    }
    return yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).encode("utf-8")


def render(
    trees: DocTree | Sequence[DocTree],
    code: Code,
    *,
    adopted: date | None = None,
    status: str = "in_force",
) -> dict[str, bytes]:
    """Render one code (one or several zakon documents) to {repo path: content}.

    Paths are relative to the repository root (``codes/<slug>/...``). Every structural
    level has a ``provisions.md`` starting with its heading, so the level title survives
    even when the level holds only articles. Several trees
    (КУпАП) are merged into one folder; a file produced by two trees → ``RenderError``.
    """
    trees = [trees] if isinstance(trees, DocTree) else list(trees)
    if not trees:
        raise RenderError(f"{code.slug}: nothing to render")
    base = f"codes/{code.slug}"
    files: dict[str, bytes] = {}
    for tree in trees:
        _render_items(tree.units, base, files)
    preamble = _joined([t.preamble for t in trees])
    if preamble:
        _put(files, f"{base}/preamble.md", _doc(preamble))
    closing = _joined([t.closing for t in trees])
    if closing:
        _put(files, f"{base}/closing.md", _doc(closing))
    _put(files, f"{base}/_meta.yaml", meta_yaml(trees, code, adopted=adopted, status=status))
    return dict(sorted(files.items()))
