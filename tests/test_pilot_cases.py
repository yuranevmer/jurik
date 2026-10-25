"""Cases found by the parse-only pilot (Task 10) on real editions.

Fixtures are trimmed fragments of the cached OpenData editions:
ЦК 435-15 ed20260805 and Податковий 2755-17 ed20260917.
"""

from datetime import date
from pathlib import Path

import pytest

from jurik.config import Code
from jurik.model import Article, DocTree, Edition, Unit
from jurik.render import render
from jurik.verify import verify_paragraphs
from jurik.zakon.paragraphs import Para, extract_paragraphs, is_excluded_heading, match_heading
from jurik.zakon.tree import build_tree, strip_notes

FIX = Path(__file__).parent / "fixtures" / "zakon"
CIVIL = "435-15_ed20260805_pilot_fragment.html"
TAX = "2755-17_ed20260917_pilot_fragment.html"


def paras_of(name: str) -> list[Para]:
    return extract_paragraphs((FIX / name).read_text(encoding="utf-8"))


def tree_of(paras: list[Para], zakon_id: str) -> DocTree:
    return build_tree(paras, Edition(zakon_id, date(2026, 1, 1), [], "law", "https://x.invalid"))


def walk(items):
    for item in items:
        yield item
        if isinstance(item, Unit):
            yield from walk(item.children)


def article(tree: DocTree, number: str) -> Article:
    return next(a for a in walk(tree.units) if isinstance(a, Article) and a.number == number)


@pytest.fixture(scope="module")
def civil_paras() -> list[Para]:
    return paras_of(CIVIL)


@pytest.fixture(scope="module")
def civil(civil_paras) -> DocTree:
    return tree_of(civil_paras, "435-15")


@pytest.fixture(scope="module")
def civil_files(civil) -> dict[str, bytes]:
    return render(civil, Code(slug="civil", zakon_ids=("435-15",), title="ЦК"))


@pytest.fixture(scope="module")
def tax_paras() -> list[Para]:
    return paras_of(TAX)


@pytest.fixture(scope="module")
def tax(tax_paras) -> DocTree:
    return tree_of(tax_paras, "2755-17")


@pytest.fixture(scope="module")
def tax_files(tax) -> dict[str, bytes]:
    return render(tax, Code(slug="tax", zakon_ids=("2755-17",), title="ПК"))


# --- ЦК -----------------------------------------------------------------------------------


def test_group_subheadings_inside_paragraph(civil_paras, civil, civil_files):
    # «2. Повне товариство» was glued to the end of article 118
    heads = [(p.unit_kind, p.number, p.title) for p in civil_paras if p.unit_kind == "group"]
    assert heads == [("group", "1", "Загальні положення"), ("group", "2", "Повне товариство")]
    assert "Повне товариство" not in article(civil, "118").body
    base = (
        "codes/civil/01-zahalni-polozhennia/02-osoby/02-yurydychna-osoba/"
        "08-pidpryiemnytski-tovarystva/01-hospodarski-tovarystva/"
    )
    assert f"{base}01-zahalni-polozhennia/art-0118.md" in civil_files
    assert f"{base}02-povne-tovarystvo/art-0119.md" in civil_files
    assert (
        civil_files[f"{base}02-povne-tovarystvo/provisions.md"]
        == "# 2. Повне товариство\n".encode()
    )


def test_group_needs_paragraph_level_and_following_article():
    # a numbered item of an article (no § open) stays body text
    p = extract_paragraphs(
        '<html><head><title>Кодекс</title></head><body><div id="article">'
        "<p>Глава 1 ЗАГАЛЬНІ</p><p>Стаття 1. Назва</p><p>2. Повне товариство</p>"
        "<p>Стаття 2. Інша</p></div></body></html>"
    )
    assert [x.kind for x in p] == ["heading", "article_start", "body", "article_start"]


def test_paragraph_heading_without_dot(civil_paras, civil):
    assert match_heading("§ 2 Правові наслідки недодержання вимог закону") == (
        "paragraph",
        "2",
        "Правові наслідки недодержання вимог закону",
    )
    assert match_heading('Розділ V "Акти громадянського стану" зберігає чинність') is None
    assert "§ 2" not in article(civil, "214").body
    par2 = next(p for p in civil_paras if p.anchor == "n1219")
    assert (par2.kind, par2.unit_kind, par2.number) == ("heading", "paragraph", "2")


def test_excluded_articles_with_garbled_notes(civil):
    for number in ("196", "990", "991"):  # "{Cтаттю 990 …}" has a Latin C
        art = article(civil, number)
        assert art.excluded and art.title == "Виключена", number
        assert art.body.startswith("Виключена на підставі Закону №"), number


def test_excluded_level_heading_is_a_note(civil_paras, civil):
    text = "§ 2. {Параграф 2 глави 74 виключено на підставі Закону № 1591-IX від 30.06.2021}"
    assert is_excluded_heading(text)
    assert not is_excluded_heading("§ 3. Розрахунки за акредитивом")
    chapter74 = next(u for u in walk(civil.units) if isinstance(u, Unit) and u.number == "74")
    assert [u.number for u in chapter74.children if isinstance(u, Unit)] == ["1", "3"]


def test_final_provisions_are_top_level(civil, civil_files):
    last = civil.units[-1]
    assert isinstance(last, Unit)
    assert (last.kind, last.number, last.title) == (
        "final",
        "",
        "ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ",
    )
    assert last.provisions.startswith("1. Цей Кодекс набирає чинності з 1 січня 2004 року.")
    book6 = civil.units[-2]
    assert (book6.kind, book6.number, book6.provisions) == ("book", "6", "")
    prov = civil_files["codes/civil/prykintsevi-ta-perekhidni-polozhennia/provisions.md"].decode()
    assert prov.startswith("# ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ\n\n1. Цей Кодекс")


def test_civil_fragment_complete(civil_paras, civil_files):
    assert verify_paragraphs(civil_paras, civil_files).ok


# --- Податковий --------------------------------------------------------------------------


def test_excluded_and_lapsed_articles(tax, tax_files):
    a115 = article(tax, "115")  # "{С таттю 115 виключено …}": split word in the source
    assert (a115.excluded, a115.title) == (True, "Виключена")
    assert a115.body == "Виключена на підставі Закону № 466-IX від 16.01.2020."
    a209 = article(tax, "209")
    assert (a209.excluded, a209.title) == (True, "Втратила чинність")
    assert a209.body.startswith("Втратила чинність з 1 січня 2017 року - див. підпункт 4")
    path = "codes/tax/05-podatok-na-dodanu-vartist/art-0209.md"
    assert tax_files[path].decode().startswith("# Стаття 209. Втратила чинність\n\n")


def test_inline_notes_not_in_text(tax, tax_files):
    body = article(tax, "174").body
    assert "{" not in body and "Абзац перший пункту 174.5" not in body
    # the item label stays: its remaining paragraphs belong to 174.5
    assert "\n\n174.5.\n\nУ разі переходу права" in body
    for path, content in tax_files.items():
        assert b"{" not in content, path


def test_excluded_sections_leave_no_level(tax):
    sections = [u.number for u in tax.units if isinstance(u, Unit)]
    assert sections == ["II", "IV", "V", "XVIII-1", "XIX"]


def test_tax_fragment_complete(tax_paras, tax_files):
    assert verify_paragraphs(tax_paras, tax_files).ok


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("174.5. {Абзац перший виключено}", "174.5."),
        ("текст {примітка} далі", "текст далі"),
        ("| a {x} | b |\n| --- | --- |", "| a | b |\n| --- | --- |"),
        ("{ціле примітка}", ""),
        ("без приміток", "без приміток"),
    ],
)
def test_strip_notes(raw, clean):
    assert strip_notes(raw) == clean
