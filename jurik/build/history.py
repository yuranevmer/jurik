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
from typing import Any, Literal, Protocol

from jurik.build.changeset import ChangeSet, diff
from jurik.build.fastimport import Ident, Stream
from jurik.build.message import (
    branch_message,
    person_email,
    rejected_bill_message,
    status_label,
    step_message,
)
from jurik.build.refs import bill_ref, law_ref, ref_safe
from jurik.config import Code
from jurik.model import BillRef, DocTree, Edition, LawMeta
from jurik.rada.index import LawIndex
from jurik.rada.match import bills_for_code
from jurik.render import render
from jurik.zakon.card import CardParseError, base_kind
from jurik.zakon.fetch import cached_card
from jurik.zakon.http import ZakonClient, ZakonError, read_gz
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree

Reason = Literal["initial", "law", "multiple", "court", "unknown", "other", "repealed", "bill"]

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
StepKind = Literal["commit", "branch", "merge"]
UNAVAILABLE = "Текст недоступний."
ROMAN = {3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX"}


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


def law_sort_key(nreg: str) -> tuple[Any, ...]:
    """Order of law numbers: by convocation suffix, then numerically (``999-20`` before
    ``1001-20``); ``…/96-вр`` acts before convocation III (``-14``), non-laws last."""
    m = _NREG.match(nreg)
    if not m:
        return (99, 0, nreg)
    conv = int(m.group(3)) if m.group(3) else 13
    return (conv, int(m.group(1)), m.group(2), nreg)


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
    for i, g in enumerate(groups):  # a repeal comes after every edition of its code
        for ev in g:
            if ev.order == 2:
                for z in {
                    e.edition.zakon_id
                    for h in groups
                    for e in h
                    if e.slug == ev.slug and e.edition is not None
                }:
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
                        f"{ed.zakon_id} ed{ed.date:%Y%m%d} comes after "
                        f"ed{old.edition.date:%Y%m%d} in the history of main"
                    )
                trees[ed.zakon_id] = load_tree(ed)
            for slug in slugs:
                src = by_slug[slug]
                current = [trees[z] for z in src.code.zakon_ids if z in trees]
                new = render(current, src.code, adopted=src.adopted)
                changes.update(diff(files.get(slug, {}), new))
                files[slug] = new
        yield _Group(first.date, reason, bases, editions, slugs, changes)


def _step(g: _Group, author: Ident, message: str, **kw: Any) -> Step:
    """A ``Step`` of group ``g``; ``kw`` overrides or adds fields."""
    fields: dict[str, Any] = {
        "date": g.date,
        "reason": g.reason,
        "bases": g.bases,
        "editions": g.editions,
        "codes": g.slugs,
        "changes": g.changes,
    }
    fields.update(kw)
    return Step(author=author, message=message, **fields)


def _main_message(
    g: _Group,
    by_slug: dict[str, CodeSource],
    metas: dict[str, LawMeta | None],
    *,
    stage: int | None = None,
    unknown_bill: bool = False,
) -> str:
    return step_message(
        reason=g.reason,
        in_force=g.date,
        bases=list(g.bases),
        titles=[by_slug[s].code.title for s in g.slugs],
        editions=g.editions,
        metas=metas,
        stage=stage,
        unknown_bill=unknown_bill,
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
    by_slug = {s.code.slug: s for s in sources}
    steps: list[Step] = []
    main: list[tuple[date, int]] = []  # (In-force, step index) of main commits, in order
    dates: list[date] = []  # In-force of main commits (for bisect)
    born: dict[str, int] = {}  # slug → position in ``main`` of its initial commit
    touched: dict[str, int] = {}  # path → position in ``main`` of the last commit changing it
    branches: dict[str, _Branch] = {}  # nreg → its branch
    refs: set[str] = set()

    def add(step: Step) -> int:
        steps.append(step)
        return len(steps) - 1

    def to_main(g: _Group, i: int) -> int:
        main.append((g.date, i))
        dates.append(g.date)
        pos = len(main) - 1
        for path in (*g.changes.modified, *g.changes.deleted):
            touched[path] = pos
        return pos

    for g in _groups(sources, load_tree):
        head = (main[-1][1],) if main else ()
        laws = sorted((b for b in g.bases if base_kind(b) == "law"), key=law_sort_key)
        metas = _metas(g.bases, index)
        if linear or g.reason in ("initial", "repealed") or not laws or not main:
            ident = Ident(*AUTHORS[g.reason], g.date)
            i = add(_step(g, ident, _main_message(g, by_slug, metas), parents=head))
            if g.reason == "initial":
                for slug in g.slugs:
                    born.setdefault(slug, len(main))
            to_main(g, i)
            continue

        nreg = next((n for n in laws if n in branches), laws[0])
        meta = metas.get(nreg)
        registered = meta.registered if meta else None
        adopted = (meta.adopted if meta else None) or g.date
        branch = branches.get(nreg)
        if branch:
            ref, stage, floor = branch.ref, branch.stage + 1, branch.merged
        else:
            ref = _unique_ref(bill_ref(meta.bill) if meta and meta.bill else law_ref(nreg), refs)
            stage = 1
            floor = len(main) - 1 if registered is None else bisect_right(dates, registered) - 1
            floor = max([floor, 0, *(born.get(s, 0) for s in g.slugs)])
        paths = (*g.changes.modified, *g.changes.deleted)
        pos = max([floor, *(touched.get(p, 0) for p in paths)])
        parent = branch.step if branch and branch.synced == pos else main[pos][1]
        bmsg = branch_message(
            nreg=nreg,
            meta=meta,
            bases=g.bases,
            metas=metas,
            titles=[by_slug[s].code.title for s in g.slugs],
            stage=stage,
        )
        b = add(
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
        message = _main_message(g, by_slug, metas, stage=stage, unknown_bill=True)
        rada = Ident(*RADA, adopted)
        i = add(_step(g, rada, message, parents=(*head, b), kind="merge", stage=stage, branch=ref))
        merged = to_main(g, i)
        # the tip = main[pos] + changes; the merge = main[head] + changes
        synced = merged if pos == merged - 1 else None
        branches[nreg] = _Branch(ref, b, stage, merged, synced)
    if index is not None and main and not linear:
        _plan_rejected(steps, main, born, sources, index, refs, bill_texts, bills_limit)
    return steps


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


def bill_path(bill: BillRef) -> str:
    """``bills/<conv>/<n>.md`` with the number spelled as in the branch name (ASCII, like
    every other path in the repository): ``1234-д`` → ``bills/4/1234-d.md``."""
    return f"bills/{bill.convocation}/{ref_safe(bill.number)}.md"


def bill_markdown(meta: LawMeta, text: str | None) -> str:
    """``bills/<conv>/<n>.md``: heading, metadata list, then the text or a stub with a link."""
    assert meta.bill is not None
    conv = ROMAN.get(meta.bill.convocation, str(meta.bill.convocation))
    lines = [f"# Законопроект № {meta.bill.number} ({conv} скликання)", ""]
    if meta.law_title:
        lines += [" ".join(meta.law_title.split()), ""]
    if meta.registered:
        lines.append(f"- Зареєстровано: {meta.registered.isoformat()}")
    if meta.initiators:
        lines.append(f"- Ініціатори: {', '.join(p.name for p in meta.initiators)}")
    status = status_label(meta.status)
    if meta.phase_date:
        status += f" ({meta.phase_date.isoformat()})"
    lines.append(f"- Статус: {status}")
    if meta.card_url:
        lines.append(f"- Картка: {meta.card_url}")
    lines += ["", "---", ""]
    if text and text.strip():
        lines.append(text.strip())
    else:
        stub = UNAVAILABLE
        if meta.card_url:
            stub += f" Див. картку законопроекту: {meta.card_url}"
        lines.append(stub)
    return "\n".join(lines) + "\n"


def _plan_rejected(
    steps: list[Step],
    main: list[tuple[date, int]],
    born: dict[str, int],
    sources: list[CodeSource],
    index: LawIndex,
    refs: set[str],
    bill_texts: BillTextSource | None,
    limit: int | None,
) -> None:
    """One branch commit per bill that never became law, appended after ``main``'s steps:
    ``bills/<conv>/<n>.md`` on top of the ``main`` commit in force on its registration day
    (never before the initial commit of a code it concerns); never merged. A branch name
    already taken gets ``-2``, ``-3``… (:func:`_unique_ref`)."""
    titles = {s.code.slug: s.code.title for s in sources}
    dates = [d for d, _ in main]
    bills = rejected_bills(sources, index)
    for meta, slugs in bills if limit is None else bills[:limit]:
        assert meta.bill is not None
        ref = _unique_ref(bill_ref(meta.bill), refs)
        when = meta.registered
        pos = bisect_right(dates, when) - 1 if when else len(main) - 1
        pos = max([pos, 0, *(born.get(s, 0) for s in slugs)])
        base = main[pos][1]
        text = bill_texts.fetch(meta.bill) if bill_texts is not None else None
        content = bill_markdown(meta, text).encode("utf-8")
        author = _initiator(meta, when or main[pos][0])
        code_titles = [titles[s] for s in slugs]
        steps.append(
            Step(
                author.when,
                "bill",
                (),
                [],
                slugs,
                ChangeSet(modified={bill_path(meta.bill): content}),
                author,
                rejected_bill_message(meta=meta, titles=code_titles),
                ref=ref,
                parents=(base,),
                kind="branch",
                stage=1,
            )
        )


def write_steps(steps: Iterable[Step], stream: Stream) -> int | None:
    """Write ``steps`` as commits (``Step.parents`` = indices of earlier steps); returns the
    mark of the last commit of ``main``."""
    marks: list[int] = []
    head: int | None = None
    for step in steps:
        parents = [marks[p] for p in step.parents]
        mark = stream.commit(
            step.ref, parents, step.author, step.author, step.message, step.changes
        )
        marks.append(mark)
        if step.ref == MAIN:
            head = mark
    return head


def sources_from_cache(
    codes: Iterable[Code], client: ZakonClient, *, today: date | None = None
) -> list[CodeSource]:
    """In-force editions of every document of ``codes`` from cached cards; every edition
    must be cached too (the history must be complete), else ``BuildError``."""
    sources: list[CodeSource] = []
    for code in codes:
        src = CodeSource(code, {})
        for zakon_id in code.zakon_ids:
            try:
                card = cached_card(client, zakon_id, today=today)
            except (ZakonError, CardParseError) as e:
                raise BuildError(f"{code.slug}: {e}") from e
            current = card.current()
            missing = [
                f"ed{e.date:%Y%m%d}"
                for e in current
                if not client.edition_path(zakon_id, f"{e.date:%Y%m%d}").is_file()
            ]
            if missing:
                raise BuildError(
                    f"{code.slug} {zakon_id}: {len(missing)} edition(s) not cached "
                    f"({', '.join(missing[:5])}), run `jurik fetch --code {code.slug}`"
                )
            src.editions[zakon_id] = current
            src.adopted = src.adopted or card.adopted
            if card.in_force and current and card.in_force > current[0].date:
                src.in_force[zakon_id] = card.in_force
        sources.append(src)
    return sources


def cached_tree_loader(client: ZakonClient) -> TreeLoader:
    """Parse a cached edition into a ``DocTree``."""

    def load(ed: Edition) -> DocTree:
        path = client.edition_path(ed.zakon_id, f"{ed.date:%Y%m%d}")
        try:
            return build_tree(extract_paragraphs(read_gz(path)), ed)
        except (OSError, EOFError, ValueError) as e:
            raise BuildError(f"{ed.zakon_id} ed{ed.date:%Y%m%d}: {e}") from e

    return load
