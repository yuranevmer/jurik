"""Cases found by the full build (Task 17) on real editions of the remaining codes.

Fixtures are trimmed fragments of cached OpenData editions (``*_build_fragment.html``).
"""

from datetime import date
from pathlib import Path

import pytest

from jurik.config import Code
from jurik.model import Article, DocTree, Edition, Unit
from jurik.render import render
from jurik.verify import verify_paragraphs
from jurik.zakon.paragraphs import Para, extract_paragraphs, match_heading, normalize_text
from jurik.zakon.tree import build_tree

FIX = Path(__file__).parent / "fixtures" / "zakon"
ELECTORAL = "396-20_ed20261022_build_fragment.html"
CUSTOMS = "4495-17_ed20260917_build_fragment.html"
LAND = "2768-14_ed20261016_build_fragment.html"
LABOUR = "322-08_ed19960723_build_fragment.html"
ADMIN_PROCEDURE = "2747-15_ed20170101_build_fragment.html"
CRIMINAL = "2341-14_ed20260918_build_fragment.html"
KUPAP_PARTS = "80731-10_ed20170507_build_fragment.html"
KUPAP_NOTE = "80731-10_ed20120119_build_fragment.html"


def paras_of(name: str) -> list[Para]:
    return extract_paragraphs((FIX / name).read_text(encoding="utf-8"))


def tree_of(paras: list[Para], zakon_id: str) -> DocTree:
    return build_tree(paras, Edition(zakon_id, date(2026, 1, 1), [], "law", "https://x.invalid"))


def walk(items):
    for item in items:
        yield item
        if isinstance(item, Unit):
            yield from walk(item.children)


def articles(tree: DocTree) -> list[Article]:
    return [a for a in walk(tree.units) if isinstance(a, Article)]


@pytest.fixture(scope="module")
def electoral_paras() -> list[Para]:
    return paras_of(ELECTORAL)


@pytest.fixture(scope="module")
def electoral(electoral_paras) -> DocTree:
    return tree_of(electoral_paras, "396-20")


def test_electoral_quoted_articles_in_final_provisions_are_text(electoral):
    # «статті 26-28 викласти в такій редакції: "Стаття 26 …" Стаття 27. …» in розділ XXXXII
    numbers = [a.number for a in articles(electoral)]
    assert numbers.count("27") == 1
    assert numbers.count("28") == 1
    final = next(u for u in walk(electoral.units) if isinstance(u, Unit) and u.number == "XXXXII")
    assert not any(isinstance(c, Article) for c in final.children)
    assert "Стаття 27. Складання попередніх списків виборців" in final.provisions
    assert "Стаття 28. Складання уточнених списків виборців" in final.provisions


def test_duplicate_article_outside_final_provisions_still_fails(electoral_paras):
    # the relaxation applies only inside final/transitional provisions
    paras = list(electoral_paras)
    dup = next(p for p in paras if p.kind == "article_start" and p.number == "27")
    pos = next(i for i, p in enumerate(paras) if p.kind == "heading" and "XXXXI." in p.text)
    paras.insert(pos + 2, dup)
    with pytest.raises(ValueError, match="duplicate article 27"):
        tree_of(paras, "396-20")


def test_electoral_additive_roman_section_numbers(electoral, electoral_paras):
    files = render(electoral, Code(slug="electoral", zakon_ids=("396-20",), title="ВК"))
    assert any(p.startswith("codes/electoral/01-") and "/41-" in p for p in files)
    assert any("/42-prykintsevi" in p for p in files)
    res = verify_paragraphs(electoral_paras, files)
    assert res.ok, res.missing[:5]


def test_customs_cyrillic_letters_in_section_numerals():
    # «Розділ XVІ МИТНА СТАТИСТИКА» (Cyrillic І, no dot) was glued to the previous article
    paras = paras_of(CUSTOMS)
    tree = tree_of(paras, "4495-17")
    sections = [u for u in walk(tree.units) if isinstance(u, Unit) and u.kind == "section"]
    assert [s.number for s in sections] == ["XVI", "XVII", "XVIII", "XIX"]
    assert sections[0].title == "МИТНА СТАТИСТИКА"
    assert all("Розділ" not in a.body for a in articles(tree))
    files = render(tree, Code(slug="customs", zakon_ids=("4495-17",), title="МК"))
    assert "codes/customs/19-provadzhennia-u-spravakh-pro-porushennia/provisions.md" in files
    res = verify_paragraphs(paras, files)
    assert res.ok, res.missing[:5]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Розділ XVІ МИТНА СТАТИСТИКА", "XVI"),
        ("Розділ XІX ПРОВАДЖЕННЯ", "XIX"),
        ("Розділ ІХ. ТИТУЛ", "IX"),
    ],
)
def test_match_heading_cyrillic_roman(text, number):
    assert match_heading(text) == ("section", number, text.split(maxsplit=2)[2].lstrip(". "))


def test_land_dotless_chapter_headings():
    # «Глава 1 Основні положення» (no dot, mixed case) was glued to the previous article
    paras = paras_of(LAND)
    tree = tree_of(paras, "2768-14")
    chapters = [u for u in walk(tree.units) if isinstance(u, Unit) and u.kind == "chapter"]
    assert [(c.number, c.title) for c in chapters][:1] == [("1", "Основні положення")]
    assert [c.number for c in chapters] == ["1", "2"]
    assert all("Глава" not in a.body for a in articles(tree))
    res = verify_paragraphs(paras, render(tree, Code("land", ("2768-14",), "ЗК")))
    assert res.ok, res.missing[:5]


@pytest.mark.parametrize(
    ("text", "heading"),
    [
        ("Глава 5 Землі сільськогосподарського призначення", True),
        ("Глава 16 - 1 Право користування чужою земельною ділянкою", True),
        ("Глава 5 Кодексу застосовується до цих відносин.", False),
        ("Глава 5 цього Кодексу", False),
    ],
)
def test_match_heading_dotless_chapter(text, heading):
    assert (match_heading(text) is not None) == heading


def test_labour_enacting_act_articles_go_to_preamble():
    # КЗпП 1996–1998: «Закон УРСР Про затвердження Кодексу … Стаття 1. Затвердити Кодекс …»
    # precedes the code, whose own «Стаття 1» used to be a duplicate
    paras = paras_of(LABOUR)
    tree = tree_of(paras, "322-08")
    assert "Стаття 1. Затвердити Кодекс законів про працю" in tree.preamble
    assert "Стаття 2. Доручити Президії" in tree.preamble
    assert all(isinstance(u, Unit) for u in tree.units)
    assert [a.number for a in articles(tree)][:3] == ["1", "2", "2-1"]
    assert articles(tree)[0].title == "Завдання Кодексу законів про працю України"
    files = render(tree, Code("labour", ("322-08",), "КЗпП"))
    assert any(p.startswith("codes/labour/03-a-zabezpechennia-zainiatosti") for p in files)
    assert "# Глава III-А. ЗАБЕЗПЕЧЕННЯ" in "".join(f.decode() for f in files.values())
    res = verify_paragraphs(paras, files)
    assert res.ok, res.missing[:5]


@pytest.mark.parametrize(
    ("text", "number", "title"),
    [
        ("ГЛАВА III-Б СПРОЩЕНИЙ РЕЖИМ РЕГУЛЮВАННЯ ТРУДОВИХ ВІДНОСИН", "III-Б", "СПРОЩЕНИЙ"),
        ("Глава XVI-А ПРАЦЯ", "XVI-А", "ПРАЦЯ"),
        ("Глава IV РОБОЧИЙ ЧАС", "IV", "РОБОЧИЙ ЧАС"),
        ("Глава 3-1. Назва", "3-1", "Назва"),
    ],
)
def test_match_heading_roman_chapter(text, number, title):
    kind, num, ttl = match_heading(text)
    assert (kind, num) == ("chapter", number)
    assert ttl.startswith(title)


def test_admin_procedure_repeated_article_number_kept_as_variant():
    # КАС 2016–2017: two laws both added «Стаття 183-7»; both articles are kept
    paras = paras_of(ADMIN_PROCEDURE)
    tree = tree_of(paras, "2747-15")
    dup = [a for a in articles(tree) if a.number == "183-7"]
    assert [a.variant for a in dup] == [0, 2]
    assert dup[0].title.startswith("Особливості провадження у справах щодо гарантованого")
    assert dup[1].title.startswith("Особливості провадження у справах за адміністративними")
    files = render(tree, Code("admin-procedure", ("2747-15",), "КАС"))
    names = sorted(p.rsplit("/", 1)[1] for p in files if "art-0183-7" in p)
    assert names == ["art-0183-7.md", "art-0183-7_2.md"]
    second = next(v for p, v in files.items() if p.endswith("art-0183-7_2.md")).decode()
    assert second.startswith("# Стаття 183-7. Особливості провадження у справах за адміні")
    res = verify_paragraphs(paras, files)
    assert res.ok, res.missing[:5]


def test_repeated_article_number_after_other_article_still_fails():
    a = Para("Стаття 1. А", "article_start", "", number="1", title="А")
    b = Para("Стаття 2. Б", "article_start", "", number="2", title="Б")
    with pytest.raises(ValueError, match="duplicate article 1"):
        tree_of([a, b, a], "x")


def test_criminal_two_level_article_index():
    # КК «Стаття 96 - 10-1 .» was glued to the end of article 96-10
    paras = paras_of(CRIMINAL)
    tree = tree_of(paras, "2341-14")
    numbers = [a.number for a in articles(tree)]
    assert numbers == ["96-10", "96-10-1", "96-11"]
    assert articles(tree)[1].title.startswith("Додаткові (нефінансові) заходи")
    files = render(tree, Code("criminal", ("2341-14",), "КК"))
    assert any(p.endswith("/art-0096-10-1.md") for p in files)
    res = verify_paragraphs(paras, files)
    assert res.ok, res.missing[:5]


def test_normalize_two_level_superscript_index():
    assert normalize_text("Стаття 96 - 10-1 . Назва") == "Стаття 96-10-1. Назва"
    assert normalize_text("Стаття 39 - 1 . Назва") == "Стаття 39-1. Назва"


def test_kupap_note_split_across_paragraphs_is_stripped():
    # «… доходів громадян. { Кодекс доповнено статтею 186-3 згідно із Законом» +
    # «N 2992-12 від 04.02.93, … від 13.07.2000 }»: 41 words were missing
    paras = paras_of(KUPAP_NOTE)
    joined = [p for p in paras if "Кодекс доповнено статтею 186-3" in p.text]
    assert len(joined) == 1 and joined[0].text.endswith("від 13.07.2000 }")
    tree = tree_of(paras, "80731-10")
    art = next(a for a in articles(tree) if a.number == "186-3")
    assert "{" not in art.body and "2992-12" not in art.body
    assert art.body.endswith("неоподатковуваних мінімумів доходів громадян.")
    res = verify_paragraphs(paras, render(tree, Code("admin-offences", ("80731-10",), "КУпАП")))
    assert res.ok, res.missing[:5]


def test_kupap_general_and_special_part_inside_section():
    # «II. ОСОБЛИВА ЧАСТИНА» was glued to the end of article 40-1
    paras = paras_of(KUPAP_PARTS)
    tree = tree_of(paras, "80731-10")
    section = next(u for u in tree.units if isinstance(u, Unit) and u.number == "II")
    parts = [u for u in section.children if isinstance(u, Unit)]
    assert [(u.kind, u.number, u.title) for u in parts] == [
        ("subsection", "I", "ЗАГАЛЬНА ЧАСТИНА"),
        ("subsection", "II", "ОСОБЛИВА ЧАСТИНА"),
    ]
    assert all("ЧАСТИНА" not in a.body for a in articles(tree))
    files = render(tree, Code("admin-offences", ("80731-10",), "КУпАП"))
    special = next(p for p in files if p.endswith("/02-osoblyva-chastyna/provisions.md"))
    assert files[special].decode().startswith("# II. ОСОБЛИВА ЧАСТИНА")
    res = verify_paragraphs(paras, files)
    assert res.ok, res.missing[:5]
