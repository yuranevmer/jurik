from pathlib import Path

import pytest

from jurik.zakon.paragraphs import (
    Para,
    ParagraphWarning,
    extract_paragraphs,
    match_heading,
    normalize_text,
)

FIX = Path(__file__).parent / "fixtures" / "zakon"


def load(name: str) -> list[Para]:
    return extract_paragraphs((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def family() -> list[Para]:
    return load("2947-14_ed20020110_print.html")


@pytest.fixture(scope="module")
def tax() -> list[Para]:
    return load("2755-17_ed20240101_print_fragment.html")


@pytest.fixture(scope="module")
def civil() -> list[Para]:
    return load("435-15_print_fragment.html")


@pytest.fixture(scope="module")
def constitution() -> list[Para]:
    return load("254k_96-vr_print_fragment.html")


def articles(paras: list[Para]) -> list[str]:
    return [p.number for p in paras if p.kind == "article_start"]


def headings(paras: list[Para]) -> list[tuple[str | None, str, str]]:
    return [(p.unit_kind, p.number, p.title) for p in paras if p.kind == "heading"]


def after(paras: list[Para], pred) -> list[Para]:
    idx = next(i for i, p in enumerate(paras) if pred(p))
    return paras[idx + 1 :]


# --- Сімейний кодекс, first edition (full document) ---------------------------------


def test_family_first_edition_article_count(family):
    # first edition 10.01.2002: articles 1..292, no gaps, no duplicates (Розділ VII
    # «Прикінцеві положення» consists of numbered points without articles)
    assert articles(family) == [str(n) for n in range(1, 293)]


def test_family_headings_with_title_in_next_paragraph(family):
    hs = headings(family)
    assert hs[0] == ("section", "I", "ЗАГАЛЬНІ ПОЛОЖЕННЯ")
    assert hs[1] == ("chapter", "1", "СІМ'Я. РЕГУЛЮВАННЯ СІМЕЙНИХ ВІДНОСИН")
    assert [h for h in hs if h[0] == "section"][-1] == ("section", "VII", "ПРИКІНЦЕВІ ПОЛОЖЕННЯ")
    assert sum(1 for h in hs if h[0] == "chapter") == 22
    # the heading title paragraph is consumed, not repeated as body
    assert not any(p.kind == "body" and p.text == "ЗАГАЛЬНІ ПОЛОЖЕННЯ" for p in family)


def test_family_header_and_article_titles(family):
    assert [p.text for p in family if p.kind == "header"][0] == "Сімейний кодекс України"
    first = next(p for p in family if p.kind == "article_start")
    assert first.title == "Завдання Сімейного кодексу України"
    assert family[-1].text.startswith("м. Київ, 10 січня 2002 року")


def test_family_body_sentence_with_rozdil_is_not_heading(family):
    p = next(p for p in family if p.text.startswith('Розділ V "Акти громадянського стану"'))
    assert p.kind == "body"


def test_no_empty_body(family, tax, civil, constitution):
    for paras in (family, tax, civil, constitution):
        assert all(p.text.strip() for p in paras)


# --- Податковий кодекс ----------------------------------------------------------------


def test_tax_article_with_index(tax):
    p = next(p for p in tax if p.kind == "article_start" and p.number == "39-1")
    assert p.text.startswith("Стаття 39-1. Особливості застосування валютних курсів")
    assert p.title.startswith("Особливості застосування валютних курсів")
    # superscript index inside point numbers is glued as well: "39 - 1 .1." -> "39-1.1."
    nxt = tax[tax.index(p) + 1]
    assert nxt.kind == "body" and nxt.text.startswith("39-1.1. Для цілей")


def test_tax_headings(tax):
    hs = headings(tax)
    assert hs[0] == ("section", "I", "ЗАГАЛЬНІ ПОЛОЖЕННЯ")
    assert ("chapter", "1", "СПРОЩЕНА СИСТЕМА ОПОДАТКУВАННЯ, ОБЛІКУ ТА ЗВІТНОСТІ") in hs
    assert ("chapter", "1", "виключена.") in hs
    assert ("subsection", "1", "Особливості справляння податку на доходи фізичних осіб") in hs


def test_tax_header_before_first_heading(tax):
    first = next(i for i, p in enumerate(tax) if p.kind == "heading")
    assert {p.kind for p in tax[:first]} <= {"header", "note"}
    assert [p.text for p in tax if p.kind == "header"][1] == "ПОДАТКОВИЙ КОДЕКС УКРАЇНИ"


def test_tax_multi_paragraph_note(tax):
    i = next(i for i, p in enumerate(tax) if p.text == "{У тексті Кодексу:")
    assert tax[i].kind == "note"
    j = i + 1
    while not tax[j].text.endswith("}"):
        assert tax[j].kind == "note", tax[j].text
        j += 1
    assert tax[j].kind == "note"


def test_tax_rozdil_xx_points_are_body(tax):
    rest = after(tax, lambda p: p.kind == "heading" and p.number == "XX")
    assert rest[0].kind == "heading" and rest[0].unit_kind == "subsection"
    points = [p for p in rest[1:] if p.kind != "note"]
    assert points[0].text.startswith("1. Податок на доходи фізичних осіб")
    assert all(p.kind == "body" for p in points)


def test_tax_tables_markdown(tax):
    tables = [p for p in tax if p.kind == "body" and p.text.startswith("| Групи |")]
    assert len(tables) == 2
    lines = tables[0].text.splitlines()
    assert lines[0] == "| Групи | Мінімально допустимі строки корисного використання, років |"
    assert lines[1] == "| --- | --- |"
    assert "| група 3 - будівлі | 20 |" in lines


def test_notes(tax, constitution):
    note = next(p for p in constitution if "Розділ VII" in p.text)
    assert note.kind == "note"
    assert note.text.startswith('{Розділ VII "ПРОКУРАТУРА" виключено')
    # an unclosed note that is not a list does not swallow the following text
    assert sum(p.kind == "note" for p in tax) < sum(p.kind == "body" for p in tax)


# --- Цивільний кодекс -----------------------------------------------------------------


def test_civil_book_and_paragraph_headings(civil):
    hs = headings(civil)
    assert hs[:3] == [
        ("book", "1", "ЗАГАЛЬНІ ПОЛОЖЕННЯ"),
        ("section", "I", "ОСНОВНІ ПОЛОЖЕННЯ"),
        ("chapter", "1", "ЦИВІЛЬНЕ ЗАКОНОДАВСТВО УКРАЇНИ"),
    ]
    assert ("chapter", "8", "ПІДПРИЄМНИЦЬКІ ТОВАРИСТВА") in hs
    assert ("paragraph", "1", "Господарські товариства") in hs


def test_civil_excluded_article_and_pending_note(civil):
    p = next(p for p in civil if p.kind == "article_start" and p.number == "196")
    assert p.title.startswith("{Статтю 196 виключено")
    pending = next(p for p in civil if p.text.startswith("/- {Зміни"))
    assert pending.kind == "note"


# --- Конституція ----------------------------------------------------------------------


def test_constitution_preamble_is_body(constitution):
    first_heading = next(i for i, p in enumerate(constitution) if p.kind == "heading")
    pre = [p for p in constitution[:first_heading] if p.kind == "body"]
    assert pre[0].text.startswith("Верховна Рада України від імені Українського народу")
    assert pre[-1].text == "приймає цю Конституцію - Основний Закон України."


def test_constitution_untitled_articles(constitution):
    i = next(i for i, p in enumerate(constitution) if p.kind == "article_start")
    art = constitution[i]
    assert (art.number, art.title, art.text) == ("1", "", "Стаття 1.")
    assert constitution[i + 1].kind == "body"
    assert constitution[i + 1].text.startswith("Україна є суверенна і незалежна")
    assert "129-1" in articles(constitution)


def test_constitution_rozdil_xv_points_are_body(constitution):
    rest = after(constitution, lambda p: p.kind == "heading" and p.number == "XV")
    assert all(p.kind in ("body", "note") for p in rest)
    texts = [p.text for p in rest if p.kind == "body"]
    assert texts[0].startswith("1. Закони та інші нормативні акти")
    assert any(t.startswith("16-1. З дня набрання чинності") for t in texts)


def test_titled_articles_override(constitution):
    html = (FIX / "254k_96-vr_print_fragment.html").read_text(encoding="utf-8")
    paras = extract_paragraphs(html, titled_articles=True)
    art = next(p for p in paras if p.kind == "article_start")
    assert art.title.startswith("Україна є суверенна")


# --- units ----------------------------------------------------------------------------


def test_normalize_text():
    assert normalize_text("Стаття\xa039 - 1 .  Назва\n") == "Стаття 39-1. Назва"
    assert normalize_text("статті 1 - 3 цього Кодексу") == "статті 1 - 3 цього Кодексу"
    assert normalize_text("обов’язок і обов'язок") == "обов’язок і обов'язок"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Книга перша", ("book", "1", "")),
        ("КНИГА П'ЯТА ЗОБОВ'ЯЗАЛЬНЕ ПРАВО", ("book", "5", "ЗОБОВ'ЯЗАЛЬНЕ ПРАВО")),
        ("ЗАГАЛЬНА ЧАСТИНА", ("part", "1", "Загальна частина")),
        ("Особлива частина", ("part", "2", "Особлива частина")),
        ("§ 1. Господарські товариства", ("paragraph", "1", "Господарські товариства")),
        ("§ 1", ("paragraph", "1", "")),
        ("Глава 1-1 ЗАГАЛЬНІ", ("chapter", "1-1", "ЗАГАЛЬНІ")),
        ("ГЛАВА 30 - 1 . ТЕСТ", ("chapter", "30-1", "ТЕСТ")),
        ("РОЗДІЛ XVIII - 1 . ПОСАДОВІ ОСОБИ", ("section", "XVIII-1", "ПОСАДОВІ ОСОБИ")),
        ("Підрозділ 9-1. Особливості", ("subsection", "9-1", "Особливості")),
        ("Розділ XV", ("section", "XV", "")),
    ],
)
def test_match_heading(text, expected):
    assert match_heading(normalize_text(text)) == expected


@pytest.mark.parametrize(
    "text",
    [
        'Розділ V "Акти громадянського стану" зберігає свою чинність',
        "Загальна сума, на яку може бути зменшено податок",
        "Глава 3 цього Кодексу застосовується",
        "§ 2 глави 8 застосовується",
        "Книга перша цього Кодексу",
        "Розділи I-III",
    ],
)
def test_body_not_heading(text):
    assert match_heading(text) is None


# --- errors ---------------------------------------------------------------------------


def test_html_without_article_div_warns():
    with pytest.warns(ParagraphWarning):
        assert extract_paragraphs("<html><body><p>Стаття 1. Щось</p></body></html>") == []


def test_empty_article_div_warns():
    with pytest.warns(ParagraphWarning):
        assert extract_paragraphs('<html><body><div id="article"> </div></body></html>') == []


def test_non_rectangular_table():
    html = (
        '<html><body><div id="article"><p>Стаття 1. Т</p><div><table>'
        "<tr><td>a</td><td>b</td></tr><tr><td colspan=2>c|d</td></tr>"
        "</table></div></div></body></html>"
    )
    paras = extract_paragraphs(html)
    assert paras[-1] == Para("a | b\nc\\|d", "body")
