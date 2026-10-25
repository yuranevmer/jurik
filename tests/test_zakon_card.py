import json
import re
from datetime import date

import pytest

from jurik.zakon.card import (
    CardParseError,
    base_kind,
    display_number,
    edition_kind,
    parse_card,
    split_bases,
)

TODAY = date(2026, 10, 25)


def load(fixtures_dir, name, zakon_id):
    text = (fixtures_dir / "zakon" / name).read_text(encoding="utf-8")
    return parse_card(text, zakon_id, today=TODAY)


@pytest.fixture
def tax(fixtures_dir):
    return load(fixtures_dir, "card-tax.json", "2755-17")


@pytest.fixture
def family(fixtures_dir):
    return load(fixtures_dir, "card-family.json", "2947-14")


@pytest.fixture
def constitution(fixtures_dir):
    return load(fixtures_dir, "card-constitution.json", "254к/96-ВР")


@pytest.fixture
def labour(fixtures_dir):
    return load(fixtures_dir, "card-labour.json", "322-08")


def by_date(card, ymd: str):
    d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    return next(e for e in card.editions if e.date == d)


def test_edition_counts(tax, family, constitution, labour):
    assert len(tax.editions) == 242
    assert len(family.editions) == 63
    assert len(family.current()) == 62
    assert len(constitution.editions) == 10
    assert len(labour.editions) == 147


def test_card_metadata(family, constitution):
    assert family.title == "Сімейний кодекс України"
    assert family.number == "2947-III"
    assert family.adopted == date(2002, 1, 10)
    assert family.status == "Чинний"
    assert constitution.title == "Конституція України"
    assert constitution.adopted == date(1996, 6, 28)
    assert constitution.zakon_id == "254к/96-ВР"


def test_sorted_by_date(tax, family, labour):
    for card in (tax, family, labour):
        dates = [e.date for e in card.editions]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)


def test_first_edition_without_basis(tax, family, constitution):
    for card in (tax, family, constitution):
        first = card.editions[0]
        assert first.bases == []
        assert first.kind == "other"
        assert first.date == card.adopted


def test_first_edition_with_basis(labour):
    first = labour.editions[0]
    assert first.date == date(1996, 7, 23)
    assert first.bases == ["256/96-вр"]
    assert first.kind == "law"


def test_single_basis(family):
    ed = by_date(family, "20030101")
    assert ed.bases == ["407-15"]
    assert ed.kind == "law"
    assert ed.url == "https://zakon.rada.gov.ua/laws/show/2947-14/ed20030101"
    assert not ed.future


def test_multiple_bases(tax, family):
    ed = by_date(tax, "20120101")
    assert ed.bases == [
        "3609-17", "4014-17", "4057-17", "4268-17", "3320-17", "4235-17", "4238-17",
    ]  # fmt: skip
    assert ed.kind == "law"
    assert by_date(family, "20060101").bases == ["2853-15", "2901-15"]


def test_court_basis(constitution):
    ed = by_date(constitution, "20100930")
    assert ed.bases == ["v020p710-10"]
    assert ed.kind == "court"


def test_stages_of_one_law(tax):
    stages = [e for e in tax.editions if e.bases == ["4834-17"]]
    assert [e.date for e in stages] == [date(2012, 7, 1), date(2012, 7, 21), date(2012, 12, 16)]
    # the same law is also one of several bases of another edition
    assert "4834-17" in by_date(tax, "20130101").bases


def test_future_editions(tax, family, labour):
    # dated, but later than today
    nov = by_date(tax, "20261101")
    assert nov.future
    assert not by_date(tax, "20260101").future
    # conditional, no calendar date
    cond = family.editions[-1]
    assert cond.future
    assert cond.date == date(3000, 1, 1)
    assert cond.bases == ["2147а-19"]
    assert cond.kind == "law"
    assert "ДСА України" in cond.comment
    assert [e.date.year for e in labour.editions if e.future] == [2028, 3000, 3000]
    assert all(not e.future for e in family.editions[:-1])


def test_future_depends_on_today(fixtures_dir):
    text = (fixtures_dir / "zakon" / "card-tax.json").read_text(encoding="utf-8")
    later = parse_card(text, "2755-17", today=date(2026, 11, 1))
    assert not by_date(later, "20261101").future


def test_matches_zakon_html_card(fixtures_dir, family):
    """JSON editions = links in the zakon HTML card + the current edition (no link there)."""
    html = (fixtures_dir / "zakon" / "card-family.html").read_text(encoding="utf-8")
    linked = set(re.findall(r"/laws/show/2947-14/ed(\d{8})", html))
    ours = {e.date.strftime("%Y%m%d") for e in family.editions}
    assert linked < ours
    assert ours - linked == {"20260805"}


def test_split_bases():
    assert split_bases("2853-15, 2901-15") == ["2853-15", "2901-15"]
    assert split_bases("256/96-ВР") == ["256/96-вр"]
    assert split_bases("") == []
    assert split_bases(None) == []
    assert split_bases("71-19,71-19") == ["71-19"]


def test_kinds():
    assert base_kind("v020p710-10") == "court"
    assert base_kind("v003p710-09") == "court"
    assert base_kind("2147а-19") == "law"
    assert base_kind("213/95-вр") == "law"
    assert base_kind("z1234-05") == "other"
    assert edition_kind([]) == "other"
    assert edition_kind(["71-19", "v020p710-10"]) == "other"


def test_display_number():
    assert display_number("4834-17") == "4834-VI"
    assert display_number("229-20") == "229-IX"
    assert display_number("322-08") == "322-VIII"
    assert display_number("5465-10") == "5465-X"
    assert display_number("2147а-19") == "2147а-VIII"
    assert display_number("256/96-вр") == "256/96-ВР"
    assert display_number("v020p710-10") == "v020p710-10"


def test_card_without_history():
    card = {"nreg": "2947-14", "nazva": "Сімейний кодекс України", "orgdat": 20020110}
    with pytest.raises(CardParseError, match="no edition history"):
        parse_card(json.dumps(card), "2947-14", today=TODAY)
    with pytest.raises(CardParseError, match="no edition history"):
        parse_card(json.dumps({**card, "eds": []}), "2947-14", today=TODAY)


def test_card_errors():
    with pytest.raises(CardParseError, match="not JSON"):
        parse_card("<html>Документ не знайдено</html>", "2947-14")
    with pytest.raises(CardParseError, match="expected"):
        parse_card(json.dumps({"nreg": "2755-17", "eds": [{"datred": 20101202}]}), "2947-14")
    with pytest.raises(CardParseError, match="bad date"):
        parse_card(json.dumps({"nreg": "2947-14", "eds": [{"datred": 2002}]}), "2947-14")
