from datetime import date
from pathlib import Path

import pytest
import yaml

from jurik.config import Code
from jurik.model import Article, DocTree, Edition, Unit
from jurik.render import RenderError, article_file, render, unit_dir
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree

FIX = Path(__file__).parent / "fixtures" / "zakon"


def edition(zakon_id: str, d: date = date(2024, 1, 1)) -> Edition:
    return Edition(zakon_id, d, [], "law", "https://example.invalid")


def code(slug: str, *ids: str, title: str = "Тестовий кодекс") -> Code:
    return Code(slug=slug, zakon_ids=ids, title=title)


def fixture_tree(name: str, zakon_id: str) -> DocTree:
    paras = extract_paragraphs((FIX / name).read_text(encoding="utf-8"))
    return build_tree(paras, edition(zakon_id))


def text(files: dict[str, bytes], path: str) -> str:
    return files[path].decode("utf-8")


@pytest.mark.parametrize(
    ("number", "name"),
    [
        ("1", "art-0001.md"),
        ("39-1", "art-0039-1.md"),
        ("1000", "art-1000.md"),
        ("1308", "art-1308.md"),
    ],
)
def test_article_file_names(number, name):
    assert article_file(number) == name


def test_article_paths_in_render():
    tree = DocTree(
        "x",
        edition("x"),
        "",
        [
            Unit(
                "section",
                "I",
                "Загальні положення",
                children=[
                    Article(n, f"Назва {n}", "Текст.") for n in ("1", "39-1", "1000", "1308")
                ],
            )
        ],
    )
    files = render(tree, code("tax", "x"))
    base = "codes/tax/01-zahalni-polozhennia"
    for name in ("art-0001.md", "art-0039-1.md", "art-1000.md", "art-1308.md"):
        assert f"{base}/{name}" in files


def test_unit_dirs():
    assert unit_dir(Unit("chapter", "1-1", "Спрощена система")).startswith("01-1-")
    assert unit_dir(Unit("section", "XIV", "Спеціальні податкові режими")) == (
        "14-spetsialni-podatkovi-rezhymy"
    )
    assert unit_dir(Unit("section", "XVIII-1", "Збір")) == "18-1-zbir"
    assert unit_dir(Unit("chapter", "3", "")) == "03"


def test_unit_dir_slug_with_apostrophe_and_long_title():
    u = Unit("chapter", "2", "ЗДІЙСНЕННЯ СІМЕЙНИХ ПРАВ ТА ВИКОНАННЯ СІМЕЙНИХ ОБОВ'ЯЗКІВ. ЗАХИСТ")
    assert unit_dir(u) == "02-zdiisnennia-simeinykh-prav-ta-vykonannia"
    assert unit_dir(Unit("chapter", "1", "СІМ'Я")) == "01-simia"


def test_article_content():
    tree = DocTree(
        "x",
        edition("x"),
        "",
        [
            Article("14", "Визначення понять", "14.1. Перший абзац.\n\n14.1.1. Другий абзац."),
            Article(
                "5",
                "Виключена",
                "Виключена на підставі Закону № 2856-VI від 23.12.2010.",
                excluded=True,
            ),
            Article("7", "", "Україна є суверенна держава."),
        ],
    )
    files = render(tree, code("c", "x"))
    assert text(files, "codes/c/art-0014.md") == (
        "# Стаття 14. Визначення понять\n\n14.1. Перший абзац.\n\n14.1.1. Другий абзац.\n"
    )
    assert text(files, "codes/c/art-0005.md") == (
        "# Стаття 5. Виключена\n\nВиключена на підставі Закону № 2856-VI від 23.12.2010.\n"
    )
    assert text(files, "codes/c/art-0007.md") == "# Стаття 7.\n\nУкраїна є суверенна держава.\n"


def test_empty_body_article():
    files = render(
        DocTree("x", edition("x"), "", [Article("5", "Виключена", "", True)]), code("c", "x")
    )
    assert text(files, "codes/c/art-0005.md") == "# Стаття 5. Виключена\n"


def test_meta_yaml_round_trip():
    tree = DocTree("2947-14", edition("2947-14", date(2002, 1, 10)), "", [])
    files = render(
        tree, code("family", "2947-14", title="Сімейний кодекс України"), adopted=date(2002, 1, 10)
    )
    meta = yaml.safe_load(files["codes/family/_meta.yaml"])
    assert meta == {
        "title": "Сімейний кодекс України",
        "zakon_id": "2947-14",
        "adopted": date(2002, 1, 10),
        "source_url": "https://zakon.rada.gov.ua/laws/show/2947-14",
        "status": "in_force",
    }  # no edition date: it would change on every edition (see jurik.build.history)


@pytest.fixture(scope="module")
def family_files() -> dict[str, bytes]:
    tree = fixture_tree("2947-14_ed20020110_print.html", "2947-14")
    return render(tree, code("family", "2947-14"))


def test_family_layout(family_files):
    arts = [p for p in family_files if p.rsplit("/", 1)[-1].startswith("art-")]
    assert len(arts) == 292
    assert (
        "codes/family/01-zahalni-polozhennia/01-simia-rehuliuvannia-simeinykh-vidnosyn/"
        "art-0001.md" in family_files
    )
    # розділ VI has no chapters: articles directly in the section folder
    assert any(p.startswith("codes/family/06-") and p.count("/") == 3 for p in arts)
    prov = text(family_files, "codes/family/07-prykintsevi-polozhennia/provisions.md")
    assert prov.startswith("# Розділ VII. ПРИКІНЦЕВІ ПОЛОЖЕННЯ\n\n1. Цей Кодекс")
    assert prov.endswith("\n") and not prov.endswith("\n\n")
    closing = text(family_files, "codes/family/closing.md")
    assert closing.startswith("Президент України")
    assert "codes/family/preamble.md" not in family_files


def test_all_files_end_with_single_newline(family_files):
    for path, content in family_files.items():
        assert content.endswith(b"\n"), path
        if path.endswith(".md"):
            assert not content.endswith(b"\n\n"), path


def test_constitution_preamble_and_section_provisions():
    files = render(
        fixture_tree("254k_96-vr_print_fragment.html", "254к/96-ВР"),
        code("constitution", "254к/96-ВР"),
    )
    assert text(files, "codes/constitution/preamble.md").startswith("Верховна Рада України")
    art1 = text(files, "codes/constitution/01-zahalni-zasady/art-0001.md")
    assert art1.startswith("# Стаття 1.\n\n")
    prov = text(files, "codes/constitution/15-perekhidni-polozhennia/provisions.md")
    assert prov.startswith("# Розділ XV. ПЕРЕХІДНІ ПОЛОЖЕННЯ\n\n1. Закони")
    assert "Конституція України прийнята" in text(files, "codes/constitution/closing.md")
    meta = yaml.safe_load(files["codes/constitution/_meta.yaml"])
    assert meta["source_url"] == "https://zakon.rada.gov.ua/laws/show/254к/96-ВР"


def test_tax_subsection_provisions():
    files = render(
        fixture_tree("2755-17_ed20240101_print_fragment.html", "2755-17"), code("tax", "2755-17")
    )
    assert "codes/tax/01-zahalni-polozhennia/art-0039-1.md" in files
    prov = [p for p in files if p.startswith("codes/tax/20-") and p.endswith("provisions.md")]
    assert prov == [
        "codes/tax/20-perekhidni-polozhennia/01-osoblyvosti-spravliannia-podatku-na/provisions.md",
        "codes/tax/20-perekhidni-polozhennia/provisions.md",
    ]
    assert text(files, prov[0]).startswith("# Підрозділ 1. Особливості справляння")
    # розділ XX has only subsections: provisions.md holds just its heading
    assert text(files, prov[1]) == "# Розділ XX. ПЕРЕХІДНІ ПОЛОЖЕННЯ\n"
    assert any(
        p.startswith("codes/tax/14-spetsialni-podatkovi-rezhymy/01-sproshchena-") for p in files
    )


def test_civil_paragraph_provisions_before_articles():
    files = render(fixture_tree("435-15_print_fragment.html", "435-15"), code("civil", "435-15"))
    prov = [p for p in files if p.endswith("provisions.md")]
    assert not [p for p in prov if "\n\n" in text(files, p)]
    group = next(p for p in prov if "/01-hospodarski-tovarystva/01-zahalni-polozhennia/" in p)
    assert text(files, group) == "# 1. Загальні положення\n"
    book = text(files, "codes/civil/01-zahalni-polozhennia/provisions.md")
    assert book.startswith("# Книга перша.")


def test_every_level_has_heading_file(family_files):
    dirs = {p.rsplit("/", 1)[0] for p in family_files if "/art-" in p}
    levels = {d for p in dirs for d in _ancestors(p) if d != "codes/family"}
    for d in levels:
        assert f"{d}/provisions.md" in family_files, d
    chapter = text(
        family_files,
        "codes/family/01-zahalni-polozhennia/01-simia-rehuliuvannia-simeinykh-vidnosyn/provisions.md",
    )
    assert chapter.startswith("# Глава 1. ") and chapter.count("\n") == 1


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(2, len(parts) + 1)]


def test_render_is_deterministic():
    a = render(fixture_tree("2947-14_ed20020110_print.html", "2947-14"), code("family", "2947-14"))
    b = render(fixture_tree("2947-14_ed20020110_print.html", "2947-14"), code("family", "2947-14"))
    assert a == b
    assert list(a) == list(b) == sorted(a)


def test_two_documents_render_into_one_folder():
    part1 = DocTree(
        "80731-10",
        edition("80731-10", date(2024, 1, 1)),
        "",
        [Unit("section", "I", "Загальні положення", children=[Article("1", "Завдання", "Т.")])],
        closing="Голова Президії",
    )
    part2 = DocTree(
        "80732-10",
        edition("80732-10", date(2024, 3, 5)),
        "",
        [
            Unit(
                "section", "I", "Загальні положення", children=[Article("2", "Законодавство", "Т.")]
            ),
            Unit("section", "IV", "Провадження", children=[Article("245", "Завдання", "Т.")]),
        ],
        closing="Голова Президії",
    )
    c = code("admin-offences", "80731-10", "80732-10")
    files = render([part1, part2], c)
    base = "codes/admin-offences"
    assert f"{base}/01-zahalni-polozhennia/art-0001.md" in files
    assert f"{base}/01-zahalni-polozhennia/art-0002.md" in files
    assert f"{base}/04-provadzhennia/art-0245.md" in files
    assert text(files, f"{base}/closing.md") == "Голова Президії\n"
    # section I is in both documents: one heading file, no conflict
    assert text(files, f"{base}/01-zahalni-polozhennia/provisions.md") == (
        "# Розділ I. Загальні положення\n"
    )
    meta = yaml.safe_load(files[f"{base}/_meta.yaml"])
    assert meta["zakon_id"] == ["80731-10", "80732-10"]
    assert meta["source_url"] == [
        "https://zakon.rada.gov.ua/laws/show/80731-10",
        "https://zakon.rada.gov.ua/laws/show/80732-10",
    ]
    assert "edition_date" not in meta


def test_same_file_from_two_documents_is_an_error():
    a = DocTree("a", edition("a"), "", [Article("1", "X", "Т.")])
    b = DocTree("b", edition("b"), "", [Article("1", "Y", "Т.")])
    with pytest.raises(RenderError, match="art-0001.md"):
        render([a, b], code("c", "a", "b"))


def test_bad_numbers_raise_render_error():
    with pytest.raises(RenderError):
        article_file("I")
    with pytest.raises(ValueError):
        unit_dir(Unit("section", "Q", "x"))
