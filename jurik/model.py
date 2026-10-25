"""Core data model shared by fetching, parsing and history building."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

EditionKind = Literal["law", "court", "other"]
UnitKind = Literal["book", "part", "section", "subsection", "chapter", "paragraph"]
LawStatus = Literal["adopted", "rejected", "withdrawn", "removed", "pending"]


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


@dataclass
class Article:
    number: str  # "14", "39-1"
    title: str
    body: str  # markdown
    excluded: bool = False


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

    law_number: str  # "2856-VI"
    law_title: str
    bill: BillRef | None
    initiators: list[Person]
    registered: date | None
    adopted: date | None
    votes: tuple[int, int, int] | None  # for, against, abstained
    signed: date | None
    status: LawStatus
