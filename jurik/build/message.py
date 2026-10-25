"""Commit messages: subject line, optional body, trailers (``In-force:`` is mandatory)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from jurik.model import Edition, LawMeta, Person
from jurik.zakon.card import base_kind, display_number

SOURCE_URL = "https://zakon.rada.gov.ua/laws/show/{}"
MAX_SUBJECT_LAWS = 3

Trailer = tuple[str, str]


class MessageError(ValueError):
    """A commit message without the mandatory ``In-force:`` trailer."""


def format_message(
    subject: str, trailers: Sequence[Trailer], body: str = "", *, require_in_force: bool = True
) -> str:
    """``subject``, blank line, ``body`` (if any), blank line, ``Key: value`` trailers.

    Commits of ``main`` must carry ``In-force:``; bill branch commits are exempt.
    """
    if require_in_force and not any(key == "In-force" and value for key, value in trailers):
        raise MessageError(f"message {subject!r} has no In-force trailer")
    parts = [subject.strip()]
    if body.strip():
        parts.append(body.strip())
    parts.append("\n".join(f"{key}: {value}" for key, value in trailers))
    return "\n\n".join(parts) + "\n"


def parse_trailers(message: str) -> list[Trailer]:
    """Trailers of the last paragraph of ``message`` (``Key: value`` lines)."""
    last = message.strip().split("\n\n")[-1]
    out: list[Trailer] = []
    for line in last.splitlines():
        key, sep, value = line.partition(": ")
        if not sep or not key or " " in key:
            return []
        out.append((key, value.strip()))
    return out


def law_display(nreg: str, meta: LawMeta | None) -> str:
    return (meta.display_number if meta and meta.display_number else "") or display_number(nreg)


# the index titles a law adopted with the President's proposals after that document
_PROPOSALS_RE = re.compile(
    r"^Пропозиції Президента України до Закону(?: України)?\s*[«\"“]?(?P<title>.*?)[»\"”]?$",
    re.S,
)


def law_title(meta: LawMeta | None) -> str:
    """Title of the law; «Пропозиції Президента України до Закону "X"» → ``X``."""
    if meta is None:
        return ""
    title = " ".join(meta.law_title.split())
    if (m := _PROPOSALS_RE.match(title)) and (inner := m.group("title").strip()):
        return inner[:1].upper() + inner[1:]
    return title


def adopted_value(meta: LawMeta) -> str:
    """``2010-12-23, за 268 / проти 2 / утрималися 1``; without votes just the date."""
    assert meta.adopted is not None
    value = meta.adopted.isoformat()
    if meta.votes:
        yes, no, abstained = meta.votes
        value += f", за {yes} / проти {no} / утрималися {abstained}"
    return value


def law_trailers(
    bases: Sequence[str], metas: dict[str, LawMeta | None], *, unknown_bill: bool = False
) -> list[Trailer]:
    """``Law:``/``Bill:`` per law basis, ``Basis:`` per other basis; ``Adopted:`` and
    ``Signed:`` only when there is exactly one law. ``unknown_bill`` writes ``Bill: unknown``
    for laws whose bill is not in the open data."""
    out: list[Trailer] = []
    laws = [b for b in bases if base_kind(b) == "law"]
    for nreg in bases:
        if base_kind(nreg) != "law":
            out.append(("Basis", nreg))
            continue
        meta = metas.get(nreg)
        out.append(("Law", law_display(nreg, meta)))
        if meta and meta.bill:
            out.append(("Bill", f"{meta.bill.convocation}/{meta.bill.number}"))
        elif unknown_bill:
            out.append(("Bill", "unknown"))
    if len(laws) == 1 and (meta := metas.get(laws[0])):
        if meta.adopted:
            out.append(("Adopted", adopted_value(meta)))
        if meta.signed:
            out.append(("Signed", meta.signed.isoformat()))
    return out


def edition_trailers(editions: Sequence[Edition]) -> list[Trailer]:
    return [("Edition", f"{e.zakon_id}/ed{e.date:%Y%m%d}") for e in editions]


def source_trailers(bases: Sequence[str], editions: Sequence[Edition]) -> list[Trailer]:
    if bases:
        return [("Source", SOURCE_URL.format(b)) for b in bases]
    return [("Source", e.url) for e in editions]


def subject_for(
    reason: str,
    bases: Sequence[str],
    titles: Sequence[str],
    metas: dict[str, LawMeta | None],
) -> str:
    """One-line summary of a step; ``titles`` = titles of the codes it touches."""
    codes = ", ".join(titles)
    if reason == "initial":
        return f"{codes}: перша редакція"
    if reason == "repealed":
        return f"{codes}: втрата чинності"
    if reason == "court":
        return f"Рішення Конституційного Суду України ({codes})"
    if reason == "unknown":
        return f"{codes}: редакція без підстави"
    laws = [b for b in bases if base_kind(b) == "law"]
    if reason == "law" and len(laws) == 1:
        meta = metas.get(laws[0])
        label = f"Закон № {law_display(laws[0], meta)}"
        title = law_title(meta)
        return f"{label}: {title}" if title else f"{label} ({codes})"
    if laws:
        shown = [f"№ {law_display(b, metas.get(b))}" for b in laws[:MAX_SUBJECT_LAWS]]
        more = len(laws) - len(shown)
        tail = f" та ще {more}" if more else ""
        word = "Закони" if len(laws) > 1 else "Закон"
        return f"{word} {', '.join(shown)}{tail} ({codes})"
    return f"{codes}: редакція, підстава {', '.join(bases)}"


def body_for(bases: Sequence[str], metas: dict[str, LawMeta | None]) -> str:
    """For several laws: one line per law with its title (when known)."""
    laws = [b for b in bases if base_kind(b) == "law"]
    if len(laws) < 2:
        return ""
    lines = []
    for nreg in laws:
        meta = metas.get(nreg)
        line = f"- Закон № {law_display(nreg, meta)}"
        if title := law_title(meta):
            line += f": {title}"
        lines.append(line)
    return "\n".join(lines)


def step_message(
    *,
    reason: str,
    in_force: date,
    bases: Sequence[str],
    titles: Sequence[str],
    editions: Sequence[Edition],
    metas: dict[str, LawMeta | None],
    stage: int | None = None,
    unknown_bill: bool = False,
) -> str:
    """Message of a commit of ``main``; ``stage`` (merge of a bill branch) adds ``Stage:``."""
    trailers = law_trailers(bases, metas, unknown_bill=unknown_bill)
    trailers.append(("In-force", in_force.isoformat()))
    if stage is not None:
        trailers.append(("Stage", str(stage)))
    trailers.append(("Reason", reason))
    trailers += edition_trailers(editions)
    trailers += source_trailers(bases, editions)
    return format_message(
        subject_for(reason, bases, titles, metas), trailers, body_for(bases, metas)
    )


def person_email(person: Person) -> str:
    return f"{person.slug}@deputies.invalid"


def branch_message(
    *,
    nreg: str,
    meta: LawMeta | None,
    bases: Sequence[str],
    metas: dict[str, LawMeta | None],
    titles: Sequence[str],
    stage: int,
) -> str:
    """Message of a bill branch commit (the change set of ``nreg``'s bill, one stage).

    No ``In-force:`` (the branch is not ``main``); other laws of a multi-basis step are
    listed in the body; initiators after the first are ``Co-authored-by:``.
    """
    if meta and meta.bill:
        subject = f"Законопроект № {meta.bill.number}"
    else:
        subject = f"Закон № {law_display(nreg, meta)}"
    title = law_title(meta)
    subject += f": {title}" if title else f" ({', '.join(titles)})"
    trailers = law_trailers(bases, metas, unknown_bill=True)
    trailers = [t for t in trailers if t[0] not in ("Adopted", "Signed")]
    if meta and meta.registered:
        trailers.append(("Registered", meta.registered.isoformat()))
    trailers.append(("Stage", str(stage)))
    trailers += [
        ("Co-authored-by", f"{p.name} <{person_email(p)}>")
        for p in (meta.initiators[1:] if meta else [])
        if p.slug
    ]
    return format_message(subject, trailers, body_for(bases, metas), require_in_force=False)


STATUS_LABELS = {
    "rejected": "відхилено",
    "withdrawn": "відкликано",
    "removed": "знято з розгляду",
    "pending": "на розгляді",
    "adopted": "прийнято",
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def rejected_bill_message(*, meta: LawMeta, titles: Sequence[str]) -> str:
    """Message of the only commit of a bill that never became law (a branch without merge).

    ``Status:`` = ``LawMeta.status``, ``Status-date:`` = date of its current phase (the
    outcome for closed bills), when known.
    """
    assert meta.bill is not None
    subject = f"Законопроект № {meta.bill.number}"
    title = law_title(meta)
    subject += f": {title}" if title else f" ({', '.join(titles)})"
    label = status_label(meta.status)
    body = f"Статус: {label}" + (f", {meta.phase_date.isoformat()}" if meta.phase_date else "")
    body += f".\nСтосується: {', '.join(titles)}." if titles else "."
    trailers: list[Trailer] = [("Bill", f"{meta.bill.convocation}/{meta.bill.number}")]
    if meta.registered:
        trailers.append(("Registered", meta.registered.isoformat()))
    trailers.append(("Status", meta.status))
    if meta.phase_date:
        trailers.append(("Status-date", meta.phase_date.isoformat()))
    if meta.card_url:
        trailers.append(("Source", meta.card_url))
    trailers += [
        ("Co-authored-by", f"{p.name} <{person_email(p)}>") for p in meta.initiators[1:] if p.slug
    ]
    return format_message(subject, trailers, body, require_in_force=False)
