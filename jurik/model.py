"""Core data model shared by fetching, parsing and history building."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
# a document (law, code) on zakon.rada.gov.ua by its nreg / zakon id
ZAKON_SHOW_URL = "https://zakon.rada.gov.ua/laws/show/{}"


def law_url(nreg: str) -> str:
    return ZAKON_SHOW_URL.format(nreg)


def edition_stamp(day: date) -> str:
    """``YYYYMMDD``: how the sources and the cache name an edition date."""
    return f"{day:%Y%m%d}"


def edition_url(zakon_id: str, day: date) -> str:
    """``https://zakon.rada.gov.ua/laws/show/<zakon_id>/edYYYYMMDD``."""
    return f"{law_url(zakon_id)}/ed{edition_stamp(day)}"


def kyiv_today() -> date:
    """Today in Kyiv: the sources date editions by Ukrainian calendar days."""
    return datetime.now(KYIV).date()


EditionKind = Literal["law", "court", "other"]
# "final": unnumbered top-level «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ» (ЦК), number "";
# "group": unlabelled numbered sub-heading inside a § («2. Повне товариство» in ЦК § 1)
UnitKind = Literal[
    "final", "book", "part", "section", "subsection", "chapter", "paragraph", "group"
]
LawStatus = Literal["adopted", "rejected", "withdrawn", "removed", "pending"]
# ``status`` of a rendered code's _meta.yaml (a repealed code's folder is deleted instead)
CodeStatus = Literal["in_force"]


@dataclass
class Edition:
    """One edition (redaktsiia) of a zakon.rada.gov.ua document."""

    zakon_id: str
    date: date  # edition date (= entry into force)
    bases: list[str]  # law numbers, e.g. ["2856-VI"], ["71-VIII", "79-VIII"], []
    kind: EditionKind
    url: str
    future: bool = False
    comment: str = ""  # condition of a conditional edition ("через 6 місяців з дня ...")

    @property
    def stamp(self) -> str:
        """``YYYYMMDD`` of the edition date (cache file names, ``Edition:`` trailers)."""
        return edition_stamp(self.date)


@dataclass
class Article:
    number: str  # "14", "39-1"
    title: str
    body: str  # markdown
    excluded: bool = False
    # 2, 3, … for a repeated number the source really has (two laws both added «Стаття 183-7»
    # to КАС in 2016): rendered as art-0183-7_2.md, the heading keeps the source number
    variant: int = 0


@dataclass
class Unit:
    """Structural level of a code: book, part, section, subsection, chapter, paragraph."""

    kind: UnitKind
    number: str  # "I", "1", "1-1"
    title: str
    provisions: str = ""  # level text outside articles
    children: list[Unit | Article] = field(default_factory=list)


@dataclass
class DocTree:
    zakon_id: str
    edition: Edition
    preamble: str
    units: list[Unit | Article] = field(default_factory=list)
    # closing block after the last provision: signature («Президент України Л.КУЧМА»,
    # «м. Київ, … N 2947-III») or the Constitution's «***» adoption note; "" if none
    closing: str = ""


@dataclass(frozen=True)
class BillRef:
    convocation: int
    number: str  # "7101", "1234-д"


@dataclass(frozen=True)
class Person:
    name: str
    slug: str


@dataclass
class LawMeta:
    """Bill/law metadata from data.rada.gov.ua open data."""

    law_number: str  # nreg of the adopted act ("2856-17"), "" if the bill is not a law
    law_title: str
    bill: BillRef | None
    initiators: list[Person]
    registered: date | None
    adopted: date | None
    votes: tuple[int, int, int] | None  # for, against, abstained
    signed: date | None
    status: LawStatus
    display_number: str = ""  # act number as displayed ("2856-VI"), "" if not a law
    card_url: str = ""  # bill card on rada.gov.ua
    phase_date: date | None = None  # date of the current phase (outcome of a closed bill)
