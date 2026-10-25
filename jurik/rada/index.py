"""Index of bills and laws built from cached Rada open data (see ``jurik.rada.client``).

Sources per convocation (field names in ``docs/spike-sources.md``):

- III–VIII: ``bills_main`` (bill card), ``bills_acts`` (bill → act number and adoption date),
  ``bills_initiators``, ``bills_passings`` (stages, used for the signing date);
- IX: ``billinfo-skl9.json`` (one JSON with all of the above);
- III–IX: ``plenary_agenda`` / ``plenary_event_question`` / ``plenary_result_event`` for the
  final («в цілому») vote.

Laws are keyed by nreg (zakon id, ``229-IX`` → ``229-20``), the form used by edition bases,
because the displayed suffix is ambiguous (``322-VIII`` is 1971, ``1-VIII`` is 2014).
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jurik.model import BillRef, LawMeta, LawStatus, Person
from jurik.rada.client import BILLINFO, CONVOCATIONS, RadaError, cache_name, read_cached
from jurik.translit import slug as make_slug

# displayed convocation suffix of an act number → nreg suffix; III laws appear as -XIV and -III
ROMAN_TO_NREG = {"XIV": 14, "III": 14, "IV": 15, "V": 16, "VI": 17, "VII": 18, "VIII": 19, "IX": 20}
ROMAN_CONVOCATION = {"III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
# Cyrillic look-alikes typed in act numbers («229-ІХ»)
_LATIN = str.maketrans({"І": "I", "Х": "X", "В": "B", "С": "C"})

PASS_THRESHOLD = 226  # used only when the dataset has no voting_result (III convocation)
FINAL_VOTE_RE = re.compile(r"в\s+цілому", re.IGNORECASE)
FIRST_READING_RE = re.compile(r"за\s+основу|першому\s+читанні", re.IGNORECASE)
# the event name is «Поіменне голосування про <subject> … (№N) - <stage>»; only the head
# is checked because bill titles themselves contain words like «скасування», «процедур»
_VOTE_HEAD = r"\s*((поіменне\s+)?голосування\s+)?(про|щодо)\s+"
SKIP_VOTE_RE = re.compile(_VOTE_HEAD + r"(скасуванн|поправк|пропозиці)", re.IGNORECASE)
BILL_VOTE_RE = re.compile(_VOTE_HEAD + r"(про[еє]кт|закон)", re.IGNORECASE)
EVENT_NUMBER_RE = re.compile(r"№\s*([0-9][^,;)\s]*)")
LAW_PREFIX_RE = re.compile(r"^\s*(про[еє]кт\s+)?закону?\s+(україни\s+)?", re.IGNORECASE)
NON_LAW_RE = re.compile(r"постанов|заяв|звернен", re.IGNORECASE)
PROPOSALS_RE = re.compile(r"пропозиції\s+президента", re.IGNORECASE)


@dataclass(frozen=True)
class Vote:
    when: date
    votes: tuple[int, int, int]  # for, against, abstained
    passed: bool
    final: bool  # «в цілому»; False = a bill vote without a stage (ratifications in IV)


@dataclass
class LawIndex:
    by_law: dict[str, LawMeta] = field(default_factory=dict)  # nreg → adopted law
    bills: dict[BillRef, LawMeta] = field(default_factory=dict)  # every law bill, all statuses
    convocations: list[int] = field(default_factory=list)

    def law(self, nreg: str) -> LawMeta | None:
        return self.by_law.get(nreg.strip().lower())


def act_to_nreg(act_number: str) -> str | None:
    """``229-IX`` / ``229-ІХ`` → ``229-20``; ``650-XIV`` → ``650-14``; None if not III–IX."""
    num = re.sub(r"\s+", "", act_number or "").translate(_LATIN)
    m = re.fullmatch(r"(\d+[^\W\d_]?)-([IVX]+)", num)
    if not m or m.group(2) not in ROMAN_TO_NREG:
        return None
    return f"{m.group(1).lower()}-{ROMAN_TO_NREG[m.group(2)]}"


def display_act(act_number: str) -> str:
    """Act number as displayed, with Cyrillic І/Х normalized: ``229-ІХ`` → ``229-IX``."""
    num = re.sub(r"\s+", "", act_number or "")
    head, _, suffix = num.rpartition("-")
    return f"{head}-{suffix.translate(_LATIN)}" if head else num


def norm_bill_number(number: str) -> str:
    return re.sub(r"\s+", "", number or "").lower()


def parse_day(value: str | None) -> date | None:
    """``2026-10-13``, ``2026-10-13T12:48:39.18``; None for empty and ``1900-00-00``."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return None if d.year < 1990 else d


def law_title(bill_title: str) -> str:
    """``Проєкт Закону про внесення змін …`` → ``Про внесення змін …``."""
    t = re.sub(r"\s+", " ", bill_title or "").strip()
    stripped = LAW_PREFIX_RE.sub("", t, count=1)
    if stripped != t and stripped:
        t = stripped
    # «"Про внесення змін …"» (a quoted title, sometimes without the closing quote)
    if t.startswith('"') and (t.endswith('"') or t.count('"') % 2):
        t = t[1:-1] if t.endswith('"') and t.count('"') % 2 == 0 else t[1:]
    return t[:1].upper() + t[1:]


def is_law_bill(kind: str) -> bool:
    """Bill type (CSV ``type``) or title prefix (IX ``name``) of a law/code draft."""
    head = (kind or "").strip()[:40]
    return bool(head) and not NON_LAW_RE.search(head)


def person(surname: str, firstname: str = "", patronymic: str = "") -> Person | None:
    """``Person("Данило Гетманцев", "hetmantsev-danylo-oleksandrovych")``."""
    surname, firstname, patronymic = (x.strip() for x in (surname, firstname, patronymic))
    if not surname:
        return None
    name = f"{firstname} {surname}".strip()
    return Person(name=name, slug=make_slug(f"{surname} {firstname} {patronymic}", max_len=80))


def person_from_fio(fio: str) -> Person | None:
    """``Кучма Леонід Данилович`` (surname first, as in bills_initiators)."""
    parts = (fio or "").split()
    if not parts:
        return None
    return person(parts[0], parts[1] if len(parts) > 1 else "", " ".join(parts[2:]))


def status_of(phase: str, has_act: bool) -> LawStatus:
    p = (phase or "").lower()
    if has_act or ("закон" in p and "підписано" in p):
        return "adopted"
    if "відхилено" in p or "не прийнято" in p:
        return "rejected"
    if "відкликано" in p:
        return "withdrawn"
    if "знято з розгляду" in p:
        return "removed"
    return "pending"


def _dedupe(people: Iterable[Person | None]) -> list[Person]:
    seen: set[str] = set()
    out = []
    for p in people:
        if p and p.slug and p.slug not in seen:
            seen.add(p.slug)
            out.append(p)
    return out


# --- reading cached files -----------------------------------------------------------------


def _file(cache: Path, n: int, name: str) -> Path:
    path = Path(cache) / "rada" / f"skl{n}" / cache_name(name)
    if not path.is_file():
        raise RadaError(f"{path}: not cached, run `jurik rada --convocation {n}` first")
    return path


def read_csv(path: Path, required: Iterable[str]) -> list[dict[str, str]]:
    raw = read_cached(path)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    reader = csv.DictReader(io.StringIO(text))
    missing = set(required) - set(reader.fieldnames or [])
    if missing:
        raise RadaError(f"{path}: missing columns {sorted(missing)}")
    try:
        return list(reader)
    except csv.Error as e:
        raise RadaError(f"{path}: bad CSV: {e}") from e


def read_billinfo(path: Path) -> list[dict]:
    raw = read_cached(path)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = [m for m in zf.namelist() if m.endswith(".json")]
            if not members:
                raise RadaError(f"{path}: no JSON file in archive")
            text = zf.read(members[0]).decode("utf-8-sig")
    except zipfile.BadZipFile as e:
        raise RadaError(f"{path}: not a zip archive ({e})") from e
    try:
        data = json.loads(text, strict=False)  # strings contain raw control characters
    except ValueError as e:
        raise RadaError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(data, list):
        raise RadaError(f"{path}: expected a JSON list of bills")
    return data


# --- votes ---------------------------------------------------------------------------------


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def load_votes(cache: Path, n: int) -> dict[str, list[Vote]]:
    """Bill votes of convocation ``n`` (final and stage-less) by normalized bill number."""
    agenda = read_csv(
        _file(cache, n, f"plenary_agenda-skl{n}.csv"), ("id_question", "number_question")
    )
    numbers = {
        r["id_question"]: norm_bill_number(r["number_question"])
        for r in agenda
        if r["number_question"].strip() not in ("", "0")
    }
    results = {
        r["id_event"]: r
        for r in read_csv(
            _file(cache, n, f"plenary_result_event-skl{n}.csv"),
            ("id_event", "for", "against", "abstain", "voting_result"),
        )
    }
    events = read_csv(
        _file(cache, n, f"plenary_event_question-skl{n}.csv"),
        ("id_question", "date_event", "name_event", "id_event"),
    )
    votes: dict[str, list[Vote]] = {}
    for ev in events:
        name = ev["name_event"]
        res = results.get(ev["id_event"])
        if not res or SKIP_VOTE_RE.match(name):
            continue
        final = bool(FINAL_VOTE_RE.search(name))
        if not final and (FIRST_READING_RE.search(name) or not BILL_VOTE_RE.match(name)):
            continue
        # the bill number is the last «№N» (an earlier one can be a cited law's number)
        found = EVENT_NUMBER_RE.findall(name)
        number = norm_bill_number(found[-1]) if found else numbers.get(ev["id_question"])
        when = parse_day(ev["date_event"])
        if not number or not when:
            continue
        counts = (_int(res["for"]), _int(res["against"]), _int(res["abstain"]))
        flag = res["voting_result"].strip()
        passed = flag == "1" if flag else counts[0] >= PASS_THRESHOLD
        votes.setdefault(number, []).append(Vote(when, counts, passed, final))
    for lst in votes.values():
        lst.sort(key=lambda v: v.when)
    return votes


def pick_vote(
    candidates: list[Vote], adopted: date | None
) -> tuple[int, int, int] | None:
    """The vote that adopted a bill.

    Priority: passed «в цілому» on the adoption day, a passed bill vote without a stage on
    that day (ratifications are voted once, without «в цілому»), the last passed «в цілому»
    before it. Bills that did not become law get their last «в цілому» vote, if any.
    """
    finals = [v for v in candidates if v.final]
    if adopted is None:
        return finals[-1].votes if finals else None
    passed = [v for v in candidates if v.passed and v.when <= adopted]
    for pool in (
        [v for v in passed if v.final and v.when == adopted],
        [v for v in passed if v.when == adopted],
        [v for v in passed if v.final],
    ):
        if pool:
            return pool[-1].votes
    return None


# --- bills ---------------------------------------------------------------------------------


@dataclass
class _Bill:
    ref: BillRef
    title: str
    kind: str
    phase: str
    registered: date | None
    initiators: list[Person]
    act_number: str = ""
    adopted: date | None = None
    signed: date | None = None


def _csv_bills(cache: Path, n: int) -> Iterator[_Bill]:
    main = read_csv(
        _file(cache, n, f"bills_main-skl{n}.csv"),
        ("bill_id", "type", "title", "number", "registrationDate", "currentPhase_title"),
    )
    acts: dict[str, dict[str, str]] = {}
    for r in read_csv(
        _file(cache, n, f"bills_acts-skl{n}.csv"), ("bill_id", "act_number", "act_date")
    ):
        acts.setdefault(r["bill_id"], r)
    initiators: dict[str, list[Person | None]] = {}
    for r in read_csv(
        _file(cache, n, f"bills_initiators-skl{n}.csv"), ("bill_id", "person", "organization")
    ):
        p = person_from_fio(r["person"])
        if p is None and r["organization"].strip():
            org = r["organization"].strip()
            p = Person(name=org, slug=make_slug(org, max_len=80))
        initiators.setdefault(r["bill_id"], []).append(p)
    signed: dict[str, date] = {}
    for r in read_csv(
        _file(cache, n, f"bills_passings-skl{n}.csv"),
        ("bill_id", "passing_date", "passing_title"),
    ):
        if "з підписом від президента" in r["passing_title"].lower():
            d = parse_day(r["passing_date"])
            if d:
                signed[r["bill_id"]] = max(d, signed.get(r["bill_id"], d))
    for r in main:
        act = acts.get(r["bill_id"], {})
        yield _Bill(
            ref=BillRef(n, r["number"].strip()),
            title=r["title"],
            kind=r["type"],
            phase=r["currentPhase_title"],
            registered=parse_day(r["registrationDate"]),
            initiators=_dedupe(initiators.get(r["bill_id"], [])),
            act_number=act.get("act_number", "").strip(),
            adopted=parse_day(act.get("act_date")),
            signed=signed.get(r["bill_id"]),
        )


def _billinfo_person(entry: dict) -> Person | None:
    for key in ("mp", "inner", "outter"):
        part = entry.get(key)
        if not part:
            continue
        p = part.get("person") or {}
        found = person(p.get("surname") or "", p.get("firstname") or "", p.get("patronymic") or "")
        if found:
            return found
        org = (part.get("organization") or part.get("post") or "").strip()
        if org:
            return Person(name=org, slug=make_slug(org, max_len=80))
    return None


def _billinfo_bills(cache: Path) -> Iterator[_Bill]:
    path = _file(cache, 9, BILLINFO)
    for i, b in enumerate(read_billinfo(path)):
        try:
            conv = ROMAN_CONVOCATION.get(
                (b.get("registrationConvocation") or "IX").split()[0].translate(_LATIN), 9
            )
            signed = [
                parse_day(p.get("date"))
                for p in b.get("passings") or []
                if "з підписом від президента" in (p.get("title") or "").lower()
            ]
            signed = [d for d in signed if d]
            yield _Bill(
                ref=BillRef(conv, str(b["registrationNumber"]).strip()),
                title=b.get("name") or "",
                kind=b.get("name") or "",
                phase=(b.get("currentPhase") or {}).get("status") or "",
                registered=parse_day(b.get("registrationDate")),
                initiators=_dedupe(_billinfo_person(e) for e in b.get("initiators") or []),
                act_number=(b.get("actNumber") or "").strip(),
                adopted=parse_day(b.get("actDate")),
                signed=max(signed) if signed else None,
            )
        except (KeyError, TypeError, AttributeError) as e:
            raise RadaError(f"{path}: bill #{i} is malformed ({e!r})") from e


def build_index(cache: Path, convocations: Iterable[int] = CONVOCATIONS) -> LawIndex:
    """Index law bills of the given convocations; all files must already be cached."""
    convs = sorted(set(convocations))
    for n in convs:
        if n not in CONVOCATIONS:
            raise RadaError(f"convocation {n} is outside III–IX (3..9)")
    votes = {n: load_votes(cache, n) for n in convs}
    index = LawIndex(convocations=convs)
    for n in convs:
        bills = _billinfo_bills(cache) if n == 9 else _csv_bills(cache, n)
        # «Пропозиції Президента до Закону» shares the number and act of the original draft;
        # index drafts first so that bill and law records come from the draft
        for bill in sorted(bills, key=lambda b: bool(PROPOSALS_RE.search(b.kind[:40]))):
            if not is_law_bill(bill.kind):
                continue
            nreg = act_to_nreg(bill.act_number) if bill.act_number else None
            number = norm_bill_number(bill.ref.number)
            candidates = votes[n].get(number, [])
            if nreg and bill.adopted and not pick_vote(candidates, bill.adopted):
                # adopted in another convocation's session (rare): look there on the same day
                candidates = [
                    v
                    for m, by_number in votes.items()
                    if m != n
                    for v in by_number.get(number, [])
                    if v.when == bill.adopted
                ]
            meta = LawMeta(
                law_number=nreg or "",
                law_title=law_title(bill.title),
                bill=bill.ref,
                initiators=bill.initiators,
                registered=bill.registered,
                adopted=bill.adopted if nreg else None,
                votes=pick_vote(candidates, bill.adopted if nreg else None),
                signed=bill.signed if nreg else None,
                status=status_of(bill.phase, bool(nreg)),
                display_number=display_act(bill.act_number) if nreg else "",
            )
            index.bills.setdefault(bill.ref, meta)
            if nreg:
                index.by_law.setdefault(nreg, meta)
    return index
