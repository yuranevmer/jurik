"""Parse a document card of the «Законодавство України» OpenData API into a list of editions.

The card is the JSON from ``https://data.rada.gov.ua/laws/card/<nreg>.json``. The fields used:

- ``nreg``: system number of the document (zakon id), ``nazva``: title;
- ``orgnum`` / ``orgdat``: displayed number and adoption date (``YYYYMMDD``);
- ``status``: numeric state from the ``stan`` dictionary;
- ``eds[]``: editions, each ``{datred: YYYYMMDD, pidstava: "nreg,nreg", comment?}``.
  Conditional editions without a calendar date have ``datred`` 30000101, 30000102, ...
  and the condition in ``comment``;
- ``hist[]`` (or the ``history`` string ``YYYYMMDD:podid:pidstava|…``): events of the
  document; ``podid`` 1 = entry into force. The first edition of most codes is dated on
  adoption (Податковий: ed20101202, in force 2011-01-01), so ``CardInfo.in_force`` keeps
  the entry-into-force date.

Bases (підстави) stay in nreg form (``4834-17``, ``254к/96-вр``, ``v020p710-10``): the
displayed suffix (``-VIII``) is ambiguous, nreg is the stable key. ``display_number`` gives a
best-effort human form for messages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from jurik.convocation import NREG_SUFFIX_ROMAN, roman
from jurik.errors import JurikError
from jurik.model import Edition, EditionKind, edition_url, kyiv_today

# «Довідник станів документів» (/ogd/zak/laws/data/csv/stan.txt)
STATUSES = {
    0: "Не визначено",
    1: "Втратив чинність",
    2: "Набирає чинності",
    3: "Дію зупинено",
    4: "Дію відновлено",
    5: "Чинний",
    6: "Не набрав чинності",
    7: "Не застосовується на території України",
    8: "Частково набрав чинності",
    9: "Втратив чинність крім окремих положень",
}

# conditional editions ("через 6 місяців з дня припинення воєнного стану") are dated 3000-01-0N
CONDITIONAL_YEAR = 3000

ENTRY_INTO_FORCE = 1  # podid of «Набрання чинності» in hist/history

COURT_RE = re.compile(r"^v\w+p710-\d\d$")
# 2856-17, 2147а-19, 256/96-вр, 5465-10
LAW_RE = re.compile(r"^\d+[а-яa-z]?(-\d\d|/\d\d-вр)$")
NREG_RE = re.compile(r"^(\d+[а-яa-z]?)-(\d\d)$")


class CardParseError(JurikError):
    """The card is not a valid document card or has no edition history."""


@dataclass
class CardInfo:
    zakon_id: str
    title: str
    number: str  # displayed number of the document, e.g. "2947-III"
    adopted: date | None
    status: str
    editions: list[Edition]
    in_force: date | None = None  # entry into force of the document (hist podid 1)

    def current(self) -> list[Edition]:
        """Editions already in force (not future)."""
        return [e for e in self.editions if not e.future]


def parse_date(value: Any) -> date:
    s = str(value)
    if not re.fullmatch(r"\d{8}", s):
        raise CardParseError(f"bad date {value!r}")
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError as e:
        raise CardParseError(f"bad date {value!r}: {e}") from e


def split_bases(pidstava: str | None) -> list[str]:
    """``"2853-15, 2901-15"`` → ``["2853-15", "2901-15"]``, lowercased, deduplicated."""
    out: list[str] = []
    for part in (pidstava or "").split(","):
        nreg = part.strip().lower()
        if nreg and nreg not in out:
            out.append(nreg)
    return out


def base_kind(nreg: str) -> EditionKind:
    if COURT_RE.match(nreg):
        return "court"
    if LAW_RE.match(nreg):
        return "law"
    return "other"


def edition_kind(bases: list[str]) -> EditionKind:
    """``law``/``court`` if every basis is of that kind; ``other`` if empty or mixed."""
    kinds = {base_kind(b) for b in bases}
    if len(kinds) == 1:
        return kinds.pop()
    return "other"


def display_number(nreg: str) -> str:
    """Best-effort displayed law number: ``4834-17`` → ``4834-VI``, ``256/96-вр`` → ``256/96-ВР``.

    Ambiguous by nature (III convocation laws are both ``-III`` and ``-XIV``); use only in
    human-facing text, never as a key.
    """
    if nreg.endswith("-вр"):
        return nreg[:-3] + "-ВР"
    m = NREG_RE.match(nreg)
    if not m:
        return nreg
    num, conv = m.group(1), int(m.group(2))
    if conv in NREG_SUFFIX_ROMAN:
        return f"{num}-{NREG_SUFFIX_ROMAN[conv]}"
    if conv <= 12:
        return f"{num}-{roman(conv)}"
    return nreg


def load_card(text: str) -> dict:
    try:
        data = json.loads(text, strict=False)
    except ValueError as e:
        raise CardParseError(f"card is not JSON: {e}") from e
    if not isinstance(data, dict):
        raise CardParseError("card JSON is not an object")
    return data


def entry_into_force(data: dict) -> date | None:
    """Earliest entry-into-force event (``podid`` 1) of the card's history, if any."""
    events: list[tuple[Any, Any]] = []
    hist = data.get("hist")
    if isinstance(hist, list):
        events = [(h.get("podid"), h.get("poddat")) for h in hist if isinstance(h, dict)]
    elif isinstance(data.get("history"), str):
        for item in data["history"].split("|"):
            day, _, rest = item.partition(":")
            events.append((rest.partition(":")[0], day))
    dates = []
    for podid, day in events:
        if str(podid) == str(ENTRY_INTO_FORCE) and day:
            try:
                dates.append(parse_date(day))
            except CardParseError:
                continue
    return min(dates, default=None)


def parse_card(text: str, zakon_id: str, *, today: date | None = None) -> CardInfo:
    """Parse an OpenData JSON card into ``CardInfo`` with editions sorted by date.

    Editions dated after ``today`` (Kyiv) and conditional editions are marked
    ``future=True``. Two entries with one ``datred`` are one edition with the union of
    their bases.
    """
    today = today or kyiv_today()
    data = load_card(text)
    nreg = str(data.get("nreg") or "")
    if nreg.lower() != zakon_id.lower():
        raise CardParseError(f"card is for {nreg!r}, expected {zakon_id!r}")
    eds = data.get("eds")
    if not isinstance(eds, list) or not eds:
        raise CardParseError(f"{zakon_id}: card has no edition history (eds)")

    by_date: dict[date, Edition] = {}
    for raw in eds:
        if not isinstance(raw, dict) or "datred" not in raw:
            raise CardParseError(f"{zakon_id}: malformed edition entry {raw!r}")
        d = parse_date(raw["datred"])
        bases = split_bases(raw.get("pidstava"))
        if d in by_date:  # duplicate entry for one date: keep every basis
            bases = by_date[d].bases + [b for b in bases if b not in by_date[d].bases]
        conditional = d.year >= CONDITIONAL_YEAR
        by_date[d] = Edition(
            zakon_id=zakon_id,
            date=d,
            bases=bases,
            kind=edition_kind(bases),
            url=edition_url(zakon_id, d),
            future=conditional or d > today,
            comment=str(raw.get("comment") or "").strip(),
        )

    adopted = data.get("orgdat")
    status = data.get("status")
    return CardInfo(
        zakon_id=zakon_id,
        title=str(data.get("nazva") or "").strip(),
        number=str(data.get("orgnum") or display_number(nreg)),
        adopted=parse_date(adopted) if adopted else None,
        status=STATUSES.get(status, str(status)) if isinstance(status, int) else str(status or ""),
        editions=[by_date[d] for d in sorted(by_date)],
        in_force=entry_into_force(data),
    )
