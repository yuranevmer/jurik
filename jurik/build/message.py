"""Commit messages: subject line, optional body, trailers (``In-force:`` is mandatory)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from jurik.model import Edition, LawMeta
from jurik.zakon.card import base_kind, display_number

SOURCE_URL = "https://zakon.rada.gov.ua/laws/show/{}"
MAX_SUBJECT_LAWS = 3

Trailer = tuple[str, str]


class MessageError(ValueError):
    """A commit message without the mandatory ``In-force:`` trailer."""


def format_message(subject: str, trailers: Sequence[Trailer], body: str = "") -> str:
    """``subject``, blank line, ``body`` (if any), blank line, ``Key: value`` trailers."""
    if not any(key == "In-force" and value for key, value in trailers):
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


def adopted_value(meta: LawMeta) -> str:
    """``2010-12-23, за 268 / проти 2 / утрималися 1``; without votes just the date."""
    assert meta.adopted is not None
    value = meta.adopted.isoformat()
    if meta.votes:
        yes, no, abstained = meta.votes
        value += f", за {yes} / проти {no} / утрималися {abstained}"
    return value


def law_trailers(bases: Sequence[str], metas: dict[str, LawMeta | None]) -> list[Trailer]:
    """``Law:``/``Bill:`` per law basis, ``Basis:`` per other basis; ``Adopted:`` and
    ``Signed:`` only when there is exactly one law."""
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
        return f"{label}: {meta.law_title}" if meta and meta.law_title else f"{label} ({codes})"
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
        if meta and meta.law_title:
            line += f": {meta.law_title}"
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
) -> str:
    trailers = law_trailers(bases, metas)
    trailers.append(("In-force", in_force.isoformat()))
    trailers.append(("Reason", reason))
    trailers += edition_trailers(editions)
    trailers += source_trailers(bases, editions)
    return format_message(
        subject_for(reason, bases, titles, metas), trailers, body_for(bases, metas)
    )
