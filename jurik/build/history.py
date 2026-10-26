"""Plan the history of ``main`` and of the bill branches.

Every in-force edition of every code is an event dated by its In-force date: the edition
date, or the document's entry into force when that is later (the first edition of most
codes is dated on adoption: Податковий ed20101202 is in force from 2011-01-01). Events of
one In-force date that share a basis (directly or through other events of that date) are
one step: a law amending several codes, or several laws in one edition, is one commit.
The first edition of each document is an ``initial`` step, a repealed code gets a
``repealed`` step that deletes its folder. A step renders the touched codes from the latest
tree of each of their documents (КУпАП = union of both parts) and diffs against the
previous render.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from heapq import heapify, heappop, heappush
from itertools import groupby, pairwise
from typing import Literal, Protocol

from jurik.build.bills import bill_markdown, bill_path
from jurik.build.changeset import ChangeSet, diff
from jurik.build.fastimport import Ident, Stream
from jurik.build.message import (
    Reason,
    branch_message,
    person_email,
    rejected_bill_message,
    step_message,
)
from jurik.build.refs import MAIN, bill_ref, law_ref
from jurik.config import Code
from jurik.errors import JurikError
from jurik.model import BillRef, DocTree, Edition, LawMeta
from jurik.rada.index import LawIndex
from jurik.rada.match import bills_for_code
from jurik.render import render
from jurik.zakon.card import base_kind

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

TreeLoader = Callable[[Edition], DocTree]
StepKind = Literal["commit", "branch", "merge"]


class BillTextSource(Protocol):
    """``jurik.rada.billtext.BillTexts`` or a test double."""

    def fetch(self, bill: BillRef) -> str | None: ...


@dataclass
class CodeSource:
    """A code with the in-force editions of each of its documents (date order)."""

    code: Code
    editions: dict[str, list[Edition]]  # zakon_id → editions
    adopted: date | None = None
    # zakon_id → entry into force of the document when later than its first edition
    in_force: dict[str, date] = field(default_factory=dict)


class BuildError(JurikError):
    """Inputs of the build are missing from the cache or cannot be parsed."""


@dataclass
class Step:
    date: date  # In-force date
    reason: Reason
    bases: tuple[str, ...]
    editions: list[Edition]
    codes: list[str]  # slugs of touched codes
    changes: ChangeSet
    author: Ident  # also the committer
    message: str
    ref: str = MAIN
    parents: tuple[int, ...] = ()  # indices of parent steps in the plan; [0] is first parent
    kind: StepKind = "commit"
    stage: int | None = None  # stage of the law (branch commits and their merges)
    branch: str | None = None  # merges: the merged branch


@dataclass(order=True)
class _Event:
    date: date  # In-force
    ed_date: date  # date of the edition (of the repeal for ``order`` 2)
    order: int  # 0 initial, 1 edition, 2 repeal: same-day initial goes first, repeal last
    bases: tuple[str, ...]
    key: str  # zakon_id/slug for events that are never merged with other codes
    slug: str = field(compare=False)
    edition: Edition | None = field(compare=False, default=None)


_NREG = re.compile(r"^(\d+)(\D*?)(?:-(\d\d)|/(\d\d)-вр)$")


# sort suffix of ``…/96-вр`` acts (no convocation suffix): right before convocation III (-14)
PRE_III_SUFFIX = 13
# sort suffix of bases that are not law numbers (court decisions, other acts): after all laws
NON_LAW_SUFFIX = 99

LawSortKey = tuple[int, int, str, str]


def law_sort_key(nreg: str) -> LawSortKey:
    """Order of law numbers: by convocation suffix, then numerically (``999-20`` before
    ``1001-20``); ``…/96-вр`` acts before convocation III (``-14``), non-laws last."""
    m = _NREG.match(nreg)
    if not m:
        return (NON_LAW_SUFFIX, 0, "", nreg)
    suffix = int(m.group(3)) if m.group(3) else PRE_III_SUFFIX
    return (suffix, int(m.group(1)), m.group(2), nreg)


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
            start = src.in_force.get(zakon_id)
            for i, ed in enumerate(src.editions.get(zakon_id, [])):
                if code.repealed and ed.date > code.repealed:
                    continue
                when = max(ed.date, start) if start else ed.date
                order = 0 if i == 0 else 1
                bases = tuple(sorted(ed.bases, key=law_sort_key))
                events.append(_Event(when, ed.date, order, bases, zakon_id, code.slug, ed))
        if code.repealed:
            bases = (code.repealed_by,) if code.repealed_by else ()
            events.append(_Event(code.repealed, code.repealed, 2, bases, code.slug, code.slug))
    return sorted(events)


def _grouped(events: list[_Event]) -> list[list[_Event]]:
    """Events of one step each: editions of one In-force date are joined when their bases
    overlap (connected components), initial editions of one code on one date are joined;
    every other event is a step of its own."""
    parent: dict[tuple[date, str], tuple[date, str]] = {}

    def find(x: tuple[date, str]) -> tuple[date, str]:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ev in events:
        if ev.order == 1 and ev.bases:
            nodes = [(ev.date, b) for b in ev.bases]
            for n in nodes:
                parent.setdefault(n, n)
            for n in nodes[1:]:
                parent[find(n)] = find(nodes[0])
    groups: dict[tuple, list[_Event]] = {}
    for ev in events:
        if ev.order == 0:  # both documents of КУпАП starting on one day: one initial step
            key: tuple = (ev.date, 0, ev.slug)
        elif ev.order == 1 and ev.bases:
            key = (ev.date, 1, find((ev.date, ev.bases[0])))
        else:
            key = (ev.date, ev.order, ev.bases, ev.key)
        groups.setdefault(key, []).append(ev)
    out: list[list[_Event]] = []
    for _, same_day in groupby(groups.values(), key=lambda g: g[0].date):
        out += _ordered(list(same_day))
    return out


def _ordered(groups: list[list[_Event]]) -> list[list[_Event]]:
    """Steps of one In-force date in an order that never takes a document back.

    The clamp to the entry into force gives editions of several dates one In-force date,
    and a basis can join an early edition of one document with a late one of another. The
    editions of each document go in edition order (its initial first), the repeal of a code
    after its editions; groups that would need each other first (a cycle) become one step.
    Otherwise the order of the first events is kept.
    """
    n = len(groups)
    if n < 2:
        return groups
    tracks: dict[str, list[tuple[tuple[date, int], int]]] = {}
    for i, g in enumerate(groups):
        for ev in g:
            if ev.edition is not None:
                tracks.setdefault(ev.edition.zakon_id, []).append(((ev.ed_date, ev.order), i))
    documents: dict[str, set[str]] = {}  # slug → zakon_ids with an edition on this date
    for g in groups:
        for ev in g:
            if ev.edition is not None:
                documents.setdefault(ev.slug, set()).add(ev.edition.zakon_id)
    for i, g in enumerate(groups):  # a repeal comes after every edition of its code
        for ev in g:
            if ev.order == 2:
                for z in documents.get(ev.slug, ()):
                    tracks[z].append(((date.max, 2), i))
    after: list[set[int]] = [set() for _ in range(n)]
    for track in tracks.values():
        track.sort()
        for (_, a), (_, b) in pairwise(track):
            if a != b:
                after[a].add(b)

    def reach(i: int) -> set[int]:
        seen, todo = {i}, [i]
        while todo:
            for j in after[todo.pop()] - seen:
                seen.add(j)
                todo.append(j)
        return seen

    reachable = [reach(i) for i in range(n)]
    comp = [min(j for j in reachable[i] if i in reachable[j]) for i in range(n)]
    members: dict[int, list[int]] = {}
    for i in range(n):
        members.setdefault(comp[i], []).append(i)
    deps: dict[int, set[int]] = {c: set() for c in members}
    for i in range(n):
        for j in after[i]:
            if comp[i] != comp[j]:
                deps[comp[j]].add(comp[i])
    ready = [c for c in members if not deps[c]]
    heapify(ready)
    out: list[list[_Event]] = []
    while ready:
        c = heappop(ready)
        out.append(sorted(ev for i in members[c] for ev in groups[i]))
        for d in members:
            if c in deps[d]:
                deps[d].discard(c)
                if not deps[d]:
                    heappush(ready, d)
    return out


def _metas(bases: Iterable[str], index: LawIndex | None) -> dict[str, LawMeta | None]:
    if index is None:
        return {}
    return {b: index.law(b) for b in bases if base_kind(b) == "law"}


@dataclass
class _Group:
    """One step of ``main`` before authorship, messages and parents are decided."""

    date: date
    reason: Reason
    bases: tuple[str, ...]
    editions: list[Edition]
    slugs: list[str]
    changes: ChangeSet


def _groups(sources: list[CodeSource], load_tree: TreeLoader) -> Iterator[_Group]:
    """Steps of ``main`` in order with their change sets (renders are diffed lazily)."""
    by_slug = {s.code.slug: s for s in sources}
    trees: dict[str, DocTree] = {}  # zakon_id → latest tree
    files: dict[str, dict[str, bytes]] = {}  # slug → latest render

    for events in _grouped(_events(sources)):
        first = events[0]
        bases = tuple(sorted({b for ev in events for b in ev.bases}, key=law_sort_key))
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
                old = trees.get(ed.zakon_id)
                if old is not None and old.edition.date > ed.date:
                    raise BuildError(
                        f"{ed.zakon_id} ed{ed.stamp} comes after "
                        f"ed{old.edition.stamp} in the history of main"
                    )
                trees[ed.zakon_id] = load_tree(ed)
            for slug in slugs:
                src = by_slug[slug]
                current = [trees[z] for z in src.code.zakon_ids if z in trees]
                new = render(current, src.code, adopted=src.adopted)
                changes.update(diff(files.get(slug, {}), new))
                files[slug] = new
        yield _Group(first.date, reason, bases, editions, slugs, changes)


def _step(
    g: _Group,
    author: Ident,
    message: str,
    *,
    parents: tuple[int, ...],
    kind: StepKind = "commit",
    ref: str = MAIN,
    stage: int | None = None,
    branch: str | None = None,
    editions: list[Edition] | None = None,
    changes: ChangeSet | None = None,
) -> Step:
    """A ``Step`` of group ``g``; ``editions``/``changes`` default to the group's."""
    return Step(
        date=g.date,
        reason=g.reason,
        bases=g.bases,
        editions=g.editions if editions is None else editions,
        codes=g.slugs,
        changes=g.changes if changes is None else changes,
        author=author,
        message=message,
        ref=ref,
        parents=parents,
        kind=kind,
        stage=stage,
        branch=branch,
    )


def plan_linear(
    sources: Iterable[CodeSource],
    load_tree: TreeLoader,
    *,
    index: LawIndex | None = None,
) -> list[Step]:
    """Linear history of ``main``: one commit per step, in In-force order.

    ``index`` (optional) only enriches messages with law titles, bills, votes.
    """
    return plan_full(sources, load_tree, index=index, linear=True)


@dataclass
class _Branch:
    ref: str
    step: int  # index of the last branch commit
    stage: int
    merged: int  # position in ``main`` of its last merge
    synced: int | None  # position in ``main`` whose tree equals the tip's tree, if any


def _initiator(meta: LawMeta | None, when: date) -> Ident:
    if meta:
        for person in meta.initiators:
            if person.slug:
                return Ident(person.name, person_email(person), when)
            break
    return Ident(*RADA, when)


def _unique_ref(ref: str, taken: set[str]) -> str:
    """``ref``, or ``ref-2``, ``ref-3``… when another bill or law already has that name
    (``ref_safe`` folds case and ``/``; the open data can give two laws one bill)."""
    out, n = ref, 1
    while out in taken:
        n += 1
        out = f"{ref}-{n}"
    taken.add(out)
    return out


def plan_full(
    sources: Iterable[CodeSource],
    load_tree: TreeLoader,
    *,
    index: LawIndex | None = None,
    bill_texts: BillTextSource | None = None,
    bills_limit: int | None = None,
    linear: bool = False,
) -> list[Step]:
    """History of ``main`` where every step based on a law is a merge of a bill branch.

    The branch is named after a law of the step (one that already has a branch, else the
    first by number): ``bill/<conv>/<number>`` when its bill is in ``index``, else
    ``bill/law/<nreg>``; a name already taken by another law or bill gets ``-2``, ``-3``….

    The branch commit carries the step's change set on top of a ``main`` commit B where
    every path of the change set already has its current content, so the branch tip and the
    merge result agree on those paths and ``git diff bill^ bill`` equals the merge's
    first-parent diff: just this law, and ``git log``/``git blame`` of a file never skip
    the commits of ``main`` in between. B = the latest of: the ``main`` commit in force on
    the bill's registration date (without one: the merge parent), the initial commit of
    each touched code, and the last ``main`` commit that changed any path of the change
    set. The merge = ``main`` head + the same change set.

    A later step of the same law (``Stage:`` 2, 3…, a later In-force date) adds a commit
    to the same branch: on the previous tip if its tree still equals ``main`` at B, else on
    B, which contains the previous merge (the branch ref moves forward, its earlier
    commits stay its ancestors). Other steps stay linear commits; with ``linear`` every
    step is.

    With ``index``, every bill concerning a code that did not become law (``bills_for_code``,
    status ≠ adopted) gets a branch with one commit adding ``bills/<conv>/<n>.md`` (text from
    ``bill_texts`` or a «Текст недоступний» stub), never merged; ``bills_limit`` caps their
    number (earliest registered first). These steps come after all steps of ``main``.
    """
    sources = list(sources)
    planner = _Planner(sources)
    for g in _groups(sources, load_tree):
        laws = sorted((b for b in g.bases if base_kind(b) == "law"), key=law_sort_key)
        metas = _metas(g.bases, index)
        if linear or g.reason in ("initial", "repealed") or not laws or not planner.main:
            planner.commit(g, metas)
        else:
            planner.merge(g, laws, metas)
    if index is not None and planner.main and not linear:
        planner.rejected(sources, index, bill_texts, bills_limit)
    return planner.steps


class _Planner:
    """The steps planned so far by :func:`plan_full` and what it needs to place the next."""

    def __init__(self, sources: list[CodeSource]) -> None:
        self.by_slug = {s.code.slug: s for s in sources}
        self.steps: list[Step] = []
        self.main: list[tuple[date, int]] = []  # (In-force, step index) of main commits
        self.dates: list[date] = []  # In-force of main commits (for bisect)
        self.born: dict[str, int] = {}  # slug → position in ``main`` of its initial commit
        self.touched: dict[str, int] = {}  # path → position in ``main`` of its last change
        self.branches: dict[str, _Branch] = {}  # nreg → its branch
        self.refs: set[str] = set()

    def _add(self, step: Step) -> int:
        self.steps.append(step)
        return len(self.steps) - 1

    def _head(self) -> tuple[int, ...]:
        return (self.main[-1][1],) if self.main else ()

    def _to_main(self, g: _Group, i: int) -> int:
        self.main.append((g.date, i))
        self.dates.append(g.date)
        pos = len(self.main) - 1
        for path in (*g.changes.modified, *g.changes.deleted):
            self.touched[path] = pos
        return pos

    def _titles(self, slugs: Iterable[str]) -> list[str]:
        return [self.by_slug[s].code.title for s in slugs]

    def _message(
        self,
        g: _Group,
        metas: dict[str, LawMeta | None],
        *,
        stage: int | None = None,
        unknown_bill: bool = False,
    ) -> str:
        return step_message(
            reason=g.reason,
            in_force=g.date,
            bases=list(g.bases),
            titles=self._titles(g.slugs),
            editions=g.editions,
            metas=metas,
            stage=stage,
            unknown_bill=unknown_bill,
        )

    def commit(self, g: _Group, metas: dict[str, LawMeta | None]) -> None:
        """A linear commit of ``main``."""
        ident = Ident(*AUTHORS[g.reason], g.date)
        i = self._add(_step(g, ident, self._message(g, metas), parents=self._head()))
        if g.reason == "initial":
            for slug in g.slugs:
                self.born.setdefault(slug, len(self.main))
        self._to_main(g, i)

    def merge(self, g: _Group, laws: list[str], metas: dict[str, LawMeta | None]) -> None:
        """A commit on the branch of a law of ``g`` and its merge into ``main``."""
        head = self._head()
        nreg = next((n for n in laws if n in self.branches), laws[0])
        meta = metas.get(nreg)
        registered = meta.registered if meta else None
        adopted = (meta.adopted if meta else None) or g.date
        branch = self.branches.get(nreg)
        if branch:
            ref, stage, floor = branch.ref, branch.stage + 1, branch.merged
        else:
            name = bill_ref(meta.bill) if meta and meta.bill else law_ref(nreg)
            ref = _unique_ref(name, self.refs)
            stage = 1
            if registered is None:
                floor = len(self.main) - 1
            else:
                floor = bisect_right(self.dates, registered) - 1
            floor = max([floor, 0, *(self.born.get(s, 0) for s in g.slugs)])
        paths = (*g.changes.modified, *g.changes.deleted)
        pos = max([floor, *(self.touched.get(p, 0) for p in paths)])
        parent = branch.step if branch and branch.synced == pos else self.main[pos][1]
        bmsg = branch_message(
            nreg=nreg,
            meta=meta,
            bases=g.bases,
            metas=metas,
            titles=self._titles(g.slugs),
            stage=stage,
        )
        b = self._add(
            _step(
                g,
                _initiator(meta, registered or adopted),
                bmsg,
                editions=[],
                changes=ChangeSet(dict(g.changes.modified), set(g.changes.deleted)),
                ref=ref,
                parents=(parent,),
                kind="branch",
                stage=stage,
            )
        )
        message = self._message(g, metas, stage=stage, unknown_bill=True)
        rada = Ident(*RADA, adopted)
        i = self._add(
            _step(g, rada, message, parents=(*head, b), kind="merge", stage=stage, branch=ref)
        )
        merged = self._to_main(g, i)
        # the tip = main[pos] + changes; the merge = main[head] + changes
        synced = merged if pos == merged - 1 else None
        self.branches[nreg] = _Branch(ref, b, stage, merged, synced)

    def rejected(
        self,
        sources: list[CodeSource],
        index: LawIndex,
        bill_texts: BillTextSource | None,
        limit: int | None,
    ) -> None:
        """One branch commit per bill that never became law, appended after ``main``'s
        steps: ``bills/<conv>/<n>.md`` on top of the ``main`` commit in force on its
        registration day (never before the initial commit of a code it concerns); never
        merged. A branch name already taken gets ``-2``, ``-3``… (:func:`_unique_ref`)."""
        bills = rejected_bills(sources, index)
        for meta, slugs in bills if limit is None else bills[:limit]:
            assert meta.bill is not None
            ref = _unique_ref(bill_ref(meta.bill), self.refs)
            when = meta.registered
            pos = bisect_right(self.dates, when) - 1 if when else len(self.main) - 1
            pos = max([pos, 0, *(self.born.get(s, 0) for s in slugs)])
            text = bill_texts.fetch(meta.bill) if bill_texts is not None else None
            content = bill_markdown(meta, text).encode("utf-8")
            author = _initiator(meta, when or self.main[pos][0])
            self.steps.append(
                Step(
                    date=author.when,
                    reason="bill",
                    bases=(),
                    editions=[],
                    codes=slugs,
                    changes=ChangeSet(modified={bill_path(meta.bill): content}),
                    author=author,
                    message=rejected_bill_message(meta=meta, titles=self._titles(slugs)),
                    ref=ref,
                    parents=(self.main[pos][1],),
                    kind="branch",
                    stage=1,
                )
            )


def rejected_bills(
    sources: Iterable[CodeSource], index: LawIndex
) -> list[tuple[LawMeta, list[str]]]:
    """Bills that did not become law concerning any of the codes, with the slugs of those
    codes; ordered by registration date (unknown last), then convocation and number."""
    found: dict[BillRef, tuple[LawMeta, list[str]]] = {}
    for src in sources:
        bases = [b for eds in src.editions.values() for ed in eds for b in ed.bases]
        for meta in bills_for_code(index, src.code, bases):
            if meta.status == "adopted" or meta.bill is None:
                continue
            found.setdefault(meta.bill, (meta, []))[1].append(src.code.slug)
    return sorted(
        found.values(),
        key=lambda t: (t[0].registered or date.max, t[0].bill.convocation, t[0].bill.number),
    )


def write_steps(steps: Iterable[Step], stream: Stream) -> None:
    """Write ``steps`` as commits (``Step.parents`` = indices of earlier steps)."""
    marks: list[int] = []
    for step in steps:
        parents = [marks[p] for p in step.parents]
        marks.append(
            stream.commit(step.ref, parents, step.author, step.author, step.message, step.changes)
        )
