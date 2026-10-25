from datetime import date
from pathlib import Path

import pytest

from jurik.model import Article, DocTree, Edition, Unit
from jurik.zakon.paragraphs import Para, extract_paragraphs
from jurik.zakon.tree import TreeError, build_tree

FIX = Path(__file__).parent / "fixtures" / "zakon"


def edition(zakon_id: str = "2947-14") -> Edition:
    return Edition(zakon_id, date(2002, 1, 10), [], "law", "https://example.invalid")


def tree_of(name: str, zakon_id: str) -> DocTree:
    paras = extract_paragraphs((FIX / name).read_text(encoding="utf-8"))
    return build_tree(paras, edition(zakon_id))


def walk(items):
    for item in items:
        yield item
        if isinstance(item, Unit):
            yield from walk(item.children)


def units(tree: DocTree, kind: str) -> list[Unit]:
    return [u for u in walk(tree.units) if isinstance(u, Unit) and u.kind == kind]


def articles(items) -> list[Article]:
    return [a for a in walk(items) if isinstance(a, Article)]


def find(tree: DocTree, *path: tuple[str, str]) -> Unit:
    items = tree.units
    unit = None
    for kind, number in path:
        unit = next(
            u for u in items if isinstance(u, Unit) and (u.kind, u.number) == (kind, number)
        )
        items = unit.children
    assert unit is not None
    return unit


@pytest.fixture(scope="module")
def family() -> DocTree:
    return tree_of("2947-14_ed20020110_print.html", "2947-14")


@pytest.fixture(scope="module")
def civil() -> DocTree:
    return tree_of("435-15_print_fragment.html", "435-15")


@pytest.fixture(scope="module")
def constitution() -> DocTree:
    return tree_of("254k_96-vr_print_fragment.html", "254к/96-ВР")


@pytest.fixture(scope="module")
def tax() -> DocTree:
    return tree_of("2755-17_ed20240101_print_fragment.html", "2755-17")


# --- Сімейний кодекс, first edition ---------------------------------------------------


def test_family_levels_and_articles(family):
    assert family.zakon_id == "2947-14"
    assert [u.number for u in family.units] == ["I", "II", "III", "IV", "V", "VI", "VII"]
    assert all(isinstance(u, Unit) and u.kind == "section" for u in family.units)
    assert [c.number for c in units(family, "chapter")] == [str(n) for n in range(1, 23)]
    assert [a.number for a in articles(family.units)] == [str(n) for n in range(1, 293)]
    assert not any(a.excluded for a in articles(family.units))
    assert all(a.title and a.body for a in articles(family.units))
    # розділ VI has articles directly, without chapters
    assert len(find(family, ("section", "VI")).children) == 18


def test_family_article_body(family):
    art = articles(family.units)[0]
    assert art.title == "Завдання Сімейного кодексу України"
    paras = art.body.split("\n\n")
    assert paras[0].startswith("1. Сімейний кодекс України визначає")
    assert "зміцнення сім'ї як соціального інституту" in art.body
    assert "{" not in art.body


def test_family_final_provisions_and_closing(family):
    final = find(family, ("section", "VII"))
    assert final.title == "ПРИКІНЦЕВІ ПОЛОЖЕННЯ"
    assert final.children == []
    assert final.provisions.startswith("1. Цей Кодекс набирає чинності з 1 січня 2003 року.")
    assert "Президент" not in final.provisions
    assert family.closing == "Президент України Л.КУЧМА\n\nм. Київ, 10 січня 2002 року N 2947-III"
    assert family.preamble == ""


# --- Цивільний кодекс: Книга / Розділ / Глава / § ----------------------------------------


def test_civil_book_section_chapter_paragraph(civil):
    chapter1 = find(civil, ("book", "1"), ("section", "I"), ("chapter", "1"))
    assert [a.number for a in chapter1.children] == ["1", "2", "3"]
    par = find(civil, ("book", "1"), ("section", "I"), ("chapter", "8"), ("paragraph", "1"))
    assert par.title == "Господарські товариства"
    assert [a.number for a in articles(par.children)][:3] == ["113", "114", "115"]
    # unnumbered sub-heading inside § before the first article
    assert par.provisions == "1. Загальні положення"


def test_civil_excluded_article_from_title_note(civil):
    art = next(a for a in articles(civil.units) if a.number == "196")
    assert art.excluded
    assert art.title == "Виключена"
    assert art.body == "Виключена на підставі Закону № 3585-IX від 22.02.2024."
    assert "Президент України" in civil.closing
    assert "Президент" not in next(a for a in articles(civil.units) if a.number == "197").body


# --- Конституція --------------------------------------------------------------------------


def test_constitution_preamble_sections_provisions(constitution):
    assert constitution.preamble.startswith("Верховна Рада України від імені Українського народу")
    assert constitution.preamble.endswith("приймає цю Конституцію - Основний Закон України.")
    assert "{" not in constitution.preamble
    assert units(constitution, "chapter") == []
    assert all(isinstance(u, Unit) and u.kind == "section" for u in constitution.units)
    art1 = articles(constitution.units)[0]
    assert (art1.number, art1.title) == ("1", "")
    assert art1.body.startswith("Україна є суверенна і незалежна")
    xv = find(constitution, ("section", "XV"))
    assert xv.children == []
    assert xv.provisions.startswith("1. Закони та інші нормативні акти")
    assert "\n\n16-1. З дня набрання чинності" in xv.provisions
    assert "{" not in xv.provisions
    assert constitution.closing.startswith("***\n\nКонституція України прийнята")


# --- Податковий кодекс --------------------------------------------------------------------


def test_tax_section_xx_subsections(tax):
    xx = find(tax, ("section", "XX"))
    assert xx.provisions == ""
    sub = find(tax, ("section", "XX"), ("subsection", "1"))
    assert sub.title == "Особливості справляння податку на доходи фізичних осіб"
    assert sub.children == []
    assert sub.provisions.startswith("1. Податок на доходи фізичних осіб")
    assert "\n\n14. Тимчасово" in sub.provisions


def test_tax_duplicate_excluded_chapter_dropped(tax):
    xiv = find(tax, ("section", "XIV"))
    assert len(xiv.children) == 1
    chapter = xiv.children[0]
    assert isinstance(chapter, Unit)
    assert chapter.title.startswith("СПРОЩЕНА СИСТЕМА ОПОДАТКУВАННЯ")
    assert [a.number for a in chapter.children] == ["291"]
    assert "39-1" in [a.number for a in articles(tax.units)]


# --- synthetic cases ------------------------------------------------------------------------


def head(kind: str, number: str, title: str = "T") -> Para:
    return Para(f"{kind} {number}", "heading", unit_kind=kind, number=number, title=title)  # type: ignore[arg-type]


def art(number: str, title: str = "Назва", anchor: str = "") -> Para:
    return Para(f"Стаття {number}. {title}", "article_start", anchor, number=number, title=title)


def body(text: str) -> Para:
    return Para(text, "body")


def note(text: str) -> Para:
    return Para(text, "note")


def test_heading_closes_levels_not_above_own():
    paras = [
        head("section", "I"),
        head("chapter", "1"),
        art("1"),
        body("a"),
        head("chapter", "2"),
        art("2"),
        head("section", "II"),
        body("p"),
        art("3"),
        body("c"),
    ]
    tree = build_tree(paras, edition())
    s1, s2 = tree.units
    assert [c.number for c in s1.children] == ["1", "2"]
    assert s2.provisions == "p"
    assert [a.number for a in s2.children] == ["3"]
    assert s2.children[0].body == "c"


def test_header_and_notes_dropped():
    paras = [
        Para("Кодекс", "header"),
        body("pre"),
        note("{Із змінами}"),
        art("1"),
        body("a"),
        note("{Статтю 1 доповнено}"),
        body("b"),
    ]
    tree = build_tree(paras, edition())
    assert tree.preamble == "pre"
    assert tree.units[0].body == "a\n\nb"


def test_excluded_article_by_title():
    tree = build_tree([art("5", "Виключена."), art("6")], edition())
    a5 = tree.units[0]
    assert a5.excluded and a5.title == "Виключена" and a5.body == ""


def test_excluded_article_with_note_only_body():
    paras = [
        art("5", "Виключена"),
        note("{Статтю 5 виключено на підставі Закону № 2856-VI від 23.12.2010}"),
        Para("Стаття 6.", "article_start", number="6"),
        note("{Статтю 6 виключено на підставі Закону № 71-VIII від 28.12.2014 }"),
    ]
    a5, a6 = build_tree(paras, edition()).units
    assert a5.excluded and a5.body == "Виключена на підставі Закону № 2856-VI від 23.12.2010."
    assert a6.excluded and a6.title == "Виключена"
    assert a6.body == "Виключена на підставі Закону № 71-VIII від 28.12.2014."


def test_note_about_other_article_does_not_exclude():
    paras = [Para("Стаття 6.", "article_start", number="6"), note("{Статтю 7 виключено}")]
    a6 = build_tree(paras, edition()).units[0]
    assert not a6.excluded


def test_duplicate_article_number_raises():
    paras = [head("section", "I"), art("1"), art("2"), art("1", anchor="n42")]
    with pytest.raises(TreeError, match=r"duplicate article 1 at paragraph 3 \(#n42\)"):
        build_tree(paras, edition())


def test_duplicate_non_empty_unit_raises():
    paras = [head("section", "I"), art("1"), head("section", "I"), art("2")]
    with pytest.raises(TreeError, match="duplicate section I"):
        build_tree(paras, edition())
