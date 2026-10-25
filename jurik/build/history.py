"""Plan the history of ``main``: one step per edition date and set of bases.

Every in-force edition of every code is an event. Events with the same date and the same
non-empty set of bases are one step (a law amending several codes is one commit). The first
edition of each document is an ``initial`` step, a repealed code gets a ``repealed`` step
that deletes its folder. A step renders the touched codes from the latest tree of each of
their documents (КУпАП = union of both parts) and diffs against the previous render.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

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
from jurik.zakon.card import CardParseError, base_kind, parse_card
from jurik.zakon.http import ZakonClient, read_gz
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
    ref: str = MAIN
    parents: tuple[int, ...] = ()  # indices of parent steps in the plan; [0] is first parent
    kind: StepKind = "commit"
    stage: int | None = None  # stage of the law (branch commits and their merges)
    branch: str | None = None  # merges: the merged branch


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


@dataclass
class _Group:
    """One step of ``main`` before authorship and messages are decided."""

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
        yield _Group(first.date, reason, bases, editions, slugs, changes)


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
    """Linear history of ``main``: one ``Step`` per (date, bases) group, in date order.

    ``index`` (optional) only enriches messages with law titles, bills, votes.
    """
    sources = list(sources)
    by_slug = {s.code.slug: s for s in sources}
    steps: list[Step] = []
    for g in _groups(sources, load_tree):
        name, email = AUTHORS[g.reason]
        ident = Ident(name, email, g.date)
        message = _main_message(g, by_slug, _metas(g.bases, index))
        parents = (len(steps) - 1,) if steps else ()
        steps.append(
            Step(
                g.date,
                g.reason,
                g.bases,
                g.editions,
                g.slugs,
                g.changes,
                ident,
                ident,
                message,
                parents=parents,
            )
        )
    return steps


@dataclass
class _Branch:
    step: int  # index of the last branch commit
    stage: int


def _initiator(meta: LawMeta | None, when: date) -> Ident:
    if meta:
        for person in meta.initiators:
            if person.slug:
                return Ident(person.name, person_email(person), when)
            break
    return Ident(*RADA, when)


def plan_full(
    sources: Iterable[CodeSource],
    load_tree: TreeLoader,
    *,
    index: LawIndex | None = None,
    bill_texts: BillTextSource | None = None,
    bills_limit: int | None = None,
) -> list[Step]:
    """History of ``main`` where every step based on a law is a merge of a bill branch.

    The first law basis (by nreg) of a step names the branch: ``bill/<conv>/<number>`` when
    its bill is in ``index``, else ``bill/law/<nreg>``. The branch commit starts from the
    last commit of ``main`` in force on the bill's registration date (never before the
    initial commit of a touched code; without a registration date from the merge parent)
    and carries only the step's change set, so ``git diff bill^ bill`` shows just this law.
    The merge = ``main`` head + the same change set. A later stage of the same law adds a
    commit to the same branch and merges it again. Other steps stay linear commits.

    With ``index``, every bill concerning a code that did not become law (``bills_for_code``,
    status ≠ adopted) gets a branch with one commit adding ``bills/<conv>/<n>.md`` (text from
    ``bill_texts`` or a «Текст недоступний» stub), never merged; ``bills_limit`` caps their
    number (earliest registered first). These steps come after all steps of ``main``.
    """
    sources = list(sources)
    by_slug = {s.code.slug: s for s in sources}
    steps: list[Step] = []
    main: list[tuple[date, int]] = []  # (In-force, step index) of main commits, in order
    born: dict[str, int] = {}  # slug → position in ``main`` of its initial commit
    branches: dict[str, _Branch] = {}

    def add(step: Step) -> int:
        steps.append(step)
        return len(steps) - 1

    for g in _groups(sources, load_tree):
        head = (main[-1][1],) if main else ()
        laws = [b for b in g.bases if base_kind(b) == "law"]
        metas = _metas(g.bases, index)
        if g.reason in ("initial", "repealed") or not laws:
            name, email = AUTHORS[g.reason]
            ident = Ident(name, email, g.date)
            message = _main_message(g, by_slug, metas)
            i = add(
                Step(
                    g.date,
                    g.reason,
                    g.bases,
                    g.editions,
                    g.slugs,
                    g.changes,
                    ident,
                    ident,
                    message,
                    parents=head,
                )
            )
            if g.reason == "initial":
                for slug in g.slugs:
                    born.setdefault(slug, len(main))
            main.append((g.date, i))
            continue

        nreg = laws[0]
        meta = metas.get(nreg)
        ref = bill_ref(meta.bill) if meta and meta.bill else law_ref(nreg)
        adopted = (meta.adopted if meta else None) or g.date
        registered = meta.registered if meta else None
        branch = branches.get(ref)
        stage = branch.stage + 1 if branch else 1
        if branch:
            base: tuple[int, ...] = (branch.step,)
        elif registered is None or not main:
            base = head
        else:
            pos = bisect_right([d for d, _ in main], registered) - 1
            pos = max([pos, *(born.get(s, 0) for s in g.slugs)])
            base = (main[pos][1],)
        author = _initiator(meta, registered or adopted)
        bmsg = branch_message(
            nreg=nreg,
            meta=meta,
            bases=g.bases,
            metas=metas,
            titles=[by_slug[s].code.title for s in g.slugs],
            stage=stage,
        )
        b = add(
            Step(
                g.date,
                g.reason,
                g.bases,
                [],
                g.slugs,
                g.changes,
                author,
                author,
                bmsg,
                ref=ref,
                parents=base,
                kind="branch",
                stage=stage,
            )
        )
        branches[ref] = _Branch(b, stage)
        rada = Ident(*RADA, adopted)
        message = _main_message(g, by_slug, metas, stage=stage, unknown_bill=True)
        i = add(
            Step(
                g.date,
                g.reason,
                g.bases,
                g.editions,
                g.slugs,
                g.changes,
                rada,
                rada,
                message,
                parents=(*head, b),
                kind="merge",
                stage=stage,
                branch=ref,
            )
        )
        main.append((g.date, i))
    if index is not None and main:
        _plan_rejected(steps, main, born, sources, index, set(branches), bill_texts, bills_limit)
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
    used_refs: set[str],
    bill_texts: BillTextSource | None,
    limit: int | None,
) -> None:
    """One branch commit per bill that never became law, appended after ``main``'s steps:
    ``bills/<conv>/<n>.md`` on top of the ``main`` commit in force on its registration day
    (never before the initial commit of a code it concerns); never merged."""
    titles = {s.code.slug: s.code.title for s in sources}
    dates = [d for d, _ in main]
    count = 0
    for meta, slugs in rejected_bills(sources, index):
        if limit is not None and count >= limit:
            break
        assert meta.bill is not None
        ref = bill_ref(meta.bill)
        if ref in used_refs:  # another number with the same ref name (case, «/»)
            continue
        used_refs.add(ref)
        count += 1
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
            step.ref, parents, step.author, step.committer, step.message, step.changes
        )
        marks.append(mark)
        if step.ref == MAIN:
            head = mark
    return head


write_linear = write_steps


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
