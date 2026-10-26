import pytest

from jurik.config import load_codes
from jurik.model import BillRef
from jurik.rada.index import build_index
from jurik.rada.match import (
    bills_for_code,
    coverage_report,
    laws_for_bases,
    match_codes,
    omnibus_unmatched,
)

CODES = load_codes()
BY_SLUG = {c.slug: c for c in CODES}

# real bill titles (data.rada.gov.ua, III–IX) → expected code slugs
TITLES = [
    ("Про внесення змін до Податкового кодексу України щодо оподаткування", ["tax"]),
    ("Про внесення змін до Цивільного та Сімейного кодексів України", ["family", "civil"]),
    (
        "Про внесення змін до Цивільного процесуального та Господарського процесуального "
        "кодексів України",
        ["civil-procedure", "commercial-procedure"],
    ),
    ("про внесення змін до Сімейного кодексу України (щодо узгодження норм)", ["family"]),
    (
        "Проєкт Закону про внесення змін до Конституції України (щодо децентралізації влади)",
        ["constitution"],
    ),
    (
        "Про внесення змін до Кодексу України про адміністративні правопорушення щодо "
        "посилення відповідальності",
        ["admin-offences"],
    ),
    ("Про внесення змін до статті 198 Цивільного кодексу України", ["civil"]),
    ("Про внесення змін до Цивільного процесуального кодексу України", ["civil-procedure"]),
    (
        "Про внесення змін до Кримінального та Кримінального процесуального кодексів України",
        ["criminal", "criminal-procedure"],
    ),
    ("Про внесення змін до Кримінально-виконавчого кодексу України", ["penal"]),
    ("Про внесення змін до Кодексу адміністративного судочинства України", ["admin-procedure"]),
    (
        "Про внесення змін до Господарського процесуального кодексу України",
        ["commercial-procedure"],
    ),
    (
        "Про внесення змін до Господарського кодексу України (щодо порядку відчуження)",
        ["commercial"],
    ),
    ("Про внесення змін до Митного кодексу України", ["customs"]),
    ("Про внесення змін до Бюджетного кодексу України щодо розподілу", ["budget"]),
    ("Про внесення змін до Кодексу законів про працю України", ["labour"]),
    ("Про внесення змін до Земельного кодексу України (щодо земель)", ["land"]),
    ("Про внесення змін до статті 9 Житлового кодексу Української РСР", ["housing"]),
    ("Про внесення змін до Водного кодексу України", ["water"]),
    ("Про внесення змін до Лісового кодексу України", ["forest"]),
    ("Про внесення змін до Кодексу України про надра", ["subsoil"]),
    ("Про внесення змін до Повітряного кодексу України", ["air"]),
    ("Про внесення змін до Кодексу торговельного мореплавства України", ["merchant-shipping"]),
    ("Про внесення змін до Виборчого кодексу України", ["electoral"]),
    ("Про внесення змін до Кодексу України з процедур банкрутства", ["bankruptcy"]),
    ("Про внесення змін до Кодексу цивільного захисту України", ["civil-protection"]),
    ("Податковий кодекс України", ["tax"]),
    (
        "Про внесення змін до Податкового  кодексу\nУкраїни та Бюджетного кодексу України",
        ["tax", "budget"],
    ),
    # omnibus bills that do not name a code are a known limitation
    ("Про внесення змін до деяких законодавчих актів України з питань освіти", []),
    ("Про внесення змін до деяких законодавчих актів України щодо соціального захисту", []),
    ("Про Конституційний Суд України", []),
    ("Про ратифікацію Конвенції про уникнення подвійного оподаткування", []),
]


@pytest.mark.parametrize(("title", "slugs"), TITLES)
def test_match_real_titles(title, slugs):
    assert match_codes(title, CODES) == slugs


def test_every_code_has_patterns():
    assert all(c.title_patterns for c in CODES)


@pytest.fixture
def index(rada_cache):
    return build_index(rada_cache, [5, 9])


def test_adopted_laws_come_from_bases_not_titles(index):
    family = BY_SLUG["family"]
    # the adopted Сімейний law is only included when it is an edition basis
    assert all(m.status != "adopted" for m in bills_for_code(index, family))
    with_bases = bills_for_code(index, family, ["524-16", "v020p710-10", "9999-20"])
    assert [m.law_number for m in with_bases if m.status == "adopted"] == ["524-16"]
    # a basis whose title names another code still counts (link is the edition basis)
    tax = bills_for_code(index, BY_SLUG["tax"], ["549-16"])
    assert [m.law_number for m in tax if m.status == "adopted"] == ["549-16"]


def test_not_adopted_by_title(index):
    civil = bills_for_code(index, BY_SLUG["civil"])
    assert [m.bill for m in civil] == [BillRef(5, "2221")]
    tax = bills_for_code(index, BY_SLUG["tax"])
    assert [m.bill for m in tax] == [BillRef(9, "15112-д")]


def test_omnibus_share(index):
    assert omnibus_unmatched(index, CODES) == (1, 1)


def test_laws_for_bases_dedupes(index):
    assert [m.law_number for m in laws_for_bases(index, ["524-16", "524-16", "1-20"])] == ["524-16"]


def test_coverage_report(index):
    lines = coverage_report(index, CODES, {"family": ["524-16", "2947-14"]})
    text = "\n".join(lines)
    assert "skl5: law bills 4, laws 2, with votes 1/2 (50%), with initiators 2/2 (100%)" in text
    assert "skl9: law bills 2, laws 1, with votes 1/1 (100%)" in text
    assert "total: law bills 6, laws 3, with votes 2/3 (67%)" in text
    assert "family: edition law bases found 1/2 (50%)" in text
    assert "tax:" not in text  # no cached card
    assert "naming no code (not matched): 1/1 (100%)" in text
