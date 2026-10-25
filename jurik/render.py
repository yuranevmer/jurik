"""Render document trees into the markdown file layout of build/laws."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import yaml

from jurik.config import Code
from jurik.model import Article, DocTree, Unit
from jurik.translit import roman_to_int, slug

SOURCE_URL = "https://zakon.rada.gov.ua/laws/show/{}"

UNIT_LABEL = {
    "book": "Книга",
    "part": "Частина",
    "section": "Розділ",
    "subsection": "Підрозділ",
    "chapter": "Глава",
    "paragraph": "§",
}


class RenderError(ValueError):
    """Two items of a code map to the same file."""


def _number_parts(number: str) -> list[str]:
    parts = [p.strip() for p in number.split("-")]
    if not parts or not all(parts):
        raise RenderError(f"bad number {number!r}")
    return parts


def unit_prefix(number: str) -> str:
    """'I' → '01', 'XIV' → '14', '1-1' → '01-1', 'XVIII-1' → '18-1'."""
    head, *rest = _number_parts(number)
    n = int(head) if head.isdigit() else roman_to_int(head)
    return "-".join([f"{n:02d}", *rest])


def unit_dir(unit: Unit) -> str:
    s = slug(unit.title)
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
    if unit.kind == "part" and "частина" in title.lower():
        return f"# {title}"
    head = f"{UNIT_LABEL[unit.kind]} {unit.number}"
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
            _put(files, f"{base}/{article_file(item.number)}", article_text(item))
            continue
        path = f"{base}/{unit_dir(item)}"
        if item.provisions.strip():
            _put(files, f"{path}/provisions.md", _doc(unit_heading(item), item.provisions))
        _render_items(item.children, path, files)


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

    Paths are relative to the repository root (``codes/<slug>/...``). Several trees
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
