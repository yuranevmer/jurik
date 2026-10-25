"""Plan the history of ``main``: one step per edition date and set of bases.

Every in-force edition of every code is an event. Events with the same date and the same
non-empty set of bases are one step (a law amending several codes is one commit). The first
edition of each document is an ``initial`` step, a repealed code gets a ``repealed`` step
that deletes its folder. A step renders the touched codes from the latest tree of each of
their documents (КУпАП = union of both parts) and diffs against the previous render.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from jurik.build.changeset import ChangeSet, diff
from jurik.build.fastimport import Ident, Stream
from jurik.build.message import step_message
from jurik.config import Code
from jurik.model import DocTree, Edition, LawMeta
from jurik.rada.index import LawIndex
from jurik.render import render
from jurik.zakon.card import CardParseError, base_kind, parse_card
from jurik.zakon.http import ZakonClient, read_gz
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree

Reason = Literal["initial", "law", "multiple", "court", "unknown", "other", "repealed"]

RADA = ("Верховна Рада України", "rada@laws.invalid")
COURT = ("Конституційний Суд України", "ccu@laws.invalid")
APPARATUS = ("Апарат Верховної Ради України", "apparat@laws.invalid")
AUTHORS: dict[str, tuple[str, str]] = {
    "initial": RADA,
    "law": RADA,
    "multiple": RADA,
    "repealed": RADA,
    "court": COURT,
    "unknown": APPARATUS,
    "other": APPARATUS,
}
MAIN = "main"

TreeLoader = Callable[[Edition], DocTree]


@dataclass
class CodeSource:
    """A code with the in-force editions of each of its documents (date order)."""

    code: Code
    editions: dict[str, list[Edition]]  # zakon_id → editions
    adopted: date | None = None


class BuildError(RuntimeError):
    """Inputs of the build are missing from the cache or cannot be parsed."""


@dataclass
class Step:
    date: date  # In-force date
    reason: Reason
    bases: tuple[str, ...]
    editions: list[Edition]
    codes: list[str]  # slugs of touched codes
    changes: ChangeSet
    author: Ident
    committer: Ident
    message: str


@dataclass(order=True)
class _Event:
    date: date
    order: int  # 0 initial, 1 edition, 2 repeal: same-day initial goes first, repeal last
    bases: tuple[str, ...]
    key: str  # zakon_id/slug for events that are never merged with other codes
    slug: str = field(compare=False)
    edition: Edition | None = field(compare=False, default=None)

    def group(self) -> tuple:
        if self.order == 0:  # both documents of КУпАП starting on one day: one initial step
            return (self.date, self.order, self.slug)
        if self.order == 1 and self.bases:
            return (self.date, self.order, self.bases)
        return (self.date, self.order, self.bases, self.key)


def reason_of(bases: Iterable[str]) -> Reason:
    bases = list(bases)
    kinds = {base_kind(b) for b in bases}
    if not bases:
        return "unknown"
    if kinds == {"court"}:
        return "court"
    if len(bases) > 1:
        return "multiple"
    return "law" if kinds == {"law"} else "other"


def _events(sources: Iterable[CodeSource]) -> list[_Event]:
    events: list[_Event] = []
    for src in sources:
        code = src.code
        for zakon_id in code.zakon_ids:
            for i, ed in enumerate(src.editions.get(zakon_id, [])):
                if code.repealed and ed.date > code.repealed:
                    continue
                order = 0 if i == 0 else 1
                bases = tuple(sorted(ed.bases))
                events.append(_Event(ed.date, order, bases, zakon_id, code.slug, ed))
        if code.repealed:
            bases = (code.repealed_by,) if code.repealed_by else ()
            events.append(_Event(code.repealed, 2, bases, code.slug, code.slug))
    return sorted(events)


def _metas(bases: Iterable[str], index: LawIndex | None) -> dict[str, LawMeta | None]:
    if index is None:
        return {}
    return {b: index.law(b) for b in bases if base_kind(b) == "law"}


def plan_linear(
    sources: Iterable[CodeSource],
    load_tree: TreeLoader,
    *,
    index: LawIndex | None = None,
) -> list[Step]:
    """Linear history of ``main``: one ``Step`` per (date, bases) group, in date order.

    ``index`` (optional) only enriches messages with law titles, bills, votes.
    """
    sources = list(sources)
    by_slug = {s.code.slug: s for s in sources}
    trees: dict[str, DocTree] = {}  # zakon_id → latest tree
    files: dict[str, dict[str, bytes]] = {}  # slug → latest render
    steps: list[Step] = []

    groups: dict[tuple, list[_Event]] = {}
    for ev in _events(sources):
        groups.setdefault(ev.group(), []).append(ev)

    for events in groups.values():
        first = events[0]
        bases = tuple(sorted({b for ev in events for b in ev.bases}))
        slugs = list(dict.fromkeys(ev.slug for ev in events))
        changes = ChangeSet()
        editions = [ev.edition for ev in events if ev.edition is not None]
        if first.order == 2:
            reason: Reason = "repealed"
            for slug in slugs:
                changes.update(ChangeSet(deleted=set(files.get(slug, {}))))
                files[slug] = {}
        else:
            reason = "initial" if first.order == 0 else reason_of(bases)
            for ed in editions:
                trees[ed.zakon_id] = load_tree(ed)
            for slug in slugs:
                src = by_slug[slug]
                current = [trees[z] for z in src.code.zakon_ids if z in trees]
                new = render(current, src.code, adopted=src.adopted)
                changes.update(diff(files.get(slug, {}), new))
                files[slug] = new
        name, email = AUTHORS[reason]
        ident = Ident(name, email, first.date)
        message = step_message(
            reason=reason,
            in_force=first.date,
            bases=list(bases),
            titles=[by_slug[s].code.title for s in slugs],
            editions=editions,
            metas=_metas(bases, index),
        )
        steps.append(
            Step(first.date, reason, bases, editions, slugs, changes, ident, ident, message)
        )
    return steps


def write_linear(steps: Iterable[Step], stream: Stream) -> int | None:
    """Write ``steps`` as a chain of commits on ``main``; returns the mark of the head."""
    head: int | None = None
    for step in steps:
        parents = [head] if head is not None else []
        head = stream.commit(MAIN, parents, step.author, step.committer, step.message, step.changes)
    return head


def sources_from_cache(
    codes: Iterable[Code], client: ZakonClient, *, today: date | None = None
) -> list[CodeSource]:
    """In-force editions of every document of ``codes`` from cached cards; every edition
    must be cached too (the history must be complete), else ``BuildError``."""
    sources: list[CodeSource] = []
    for code in codes:
        editions: dict[str, list[Edition]] = {}
        adopted: date | None = None
        for zakon_id in code.zakon_ids:
            path = client.card_path(zakon_id)
            if not path.is_file():
                raise BuildError(f"{code.slug}: card {zakon_id} not cached, run `jurik fetch`")
            try:
                card = parse_card(read_gz(path), zakon_id, today=today)
            except CardParseError as e:
                raise BuildError(f"{code.slug}: {e}") from e
            missing = [
                f"ed{e.date:%Y%m%d}"
                for e in card.current()
                if not client.edition_path(zakon_id, f"{e.date:%Y%m%d}").is_file()
            ]
            if missing:
                raise BuildError(
                    f"{code.slug} {zakon_id}: {len(missing)} edition(s) not cached "
                    f"({', '.join(missing[:5])}), run `jurik fetch --code {code.slug}`"
                )
            editions[zakon_id] = card.current()
            adopted = adopted or card.adopted
        sources.append(CodeSource(code, editions, adopted))
    return sources


def cached_tree_loader(client: ZakonClient) -> TreeLoader:
    """Parse a cached edition into a ``DocTree``."""

    def load(ed: Edition) -> DocTree:
        path = client.edition_path(ed.zakon_id, f"{ed.date:%Y%m%d}")
        try:
            return build_tree(extract_paragraphs(read_gz(path)), ed)
        except (OSError, ValueError) as e:
            raise BuildError(f"{ed.zakon_id} ed{ed.date:%Y%m%d}: {e}") from e

    return load
