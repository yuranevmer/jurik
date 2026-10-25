import time
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from jurik import cli
from jurik.config import Code, load_codes
from jurik.model import Edition
from jurik.render import render
from jurik.verify import normalize_words, verify_edition, verify_paragraphs
from jurik.zakon.http import ZakonClient, write_gz
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree

FIX = Path(__file__).parent / "fixtures" / "zakon"
FAMILY = FIX / "2947-14_ed20020110_print.html"


def render_html(html: str, zakon_id: str = "2947-14", slug: str = "family") -> dict[str, bytes]:
    tree = build_tree(extract_paragraphs(html), Edition(zakon_id, date(2002, 1, 10), [], "law", ""))
    return render(tree, Code(slug, (zakon_id,), "Кодекс"))


def page(*paras: str, title: str = "КОДЕКС") -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return (
        f"<html><head><title>{title}</title></head><body><div id=article>{body}</div></body></html>"
    )


@pytest.fixture(scope="module")
def family_html() -> str:
    return FAMILY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def family_files(family_html) -> dict[str, bytes]:
    return render_html(family_html)


def test_normalize_words():
    assert normalize_words("# Стаття 39-1. Пам'ять {Із змінами} — «ТЕСТ»,  п’ять!") == [
        "стаття",
        "39",
        "1",
        "память",
        "тест",
        "пять",
    ]
    assert normalize_words("| a | b |\n| --- | --- |") == ["a", "b"]
    assert normalize_words("{ціле примітка}") == []


@pytest.mark.parametrize(
    ("name", "zakon_id"),
    [
        ("2947-14_ed20020110_print.html", "2947-14"),
        ("2755-17_ed20240101_print_fragment.html", "2755-17"),
        ("435-15_print_fragment.html", "435-15"),
        ("254k_96-vr_print_fragment.html", "254к/96-ВР"),
    ],
)
def test_fixture_renders_are_complete(name, zakon_id):
    html = (FIX / name).read_text(encoding="utf-8")
    result = verify_edition(render_html(html, zakon_id, "x"), html)
    assert result.ok, [str(d) for d in result.diffs]
    assert result.words > 100


def test_identical_text_ok(family_files, family_html):
    result = verify_edition(family_files, family_html)
    assert result.ok
    assert result.diffs == []
    assert "ok" in result.summary()


def test_lost_paragraph_is_missing_with_file(family_files, family_html):
    path = next(p for p in family_files if p.endswith("/art-0010.md"))
    content = family_files[path].decode("utf-8")
    blocks = content.split("\n\n")
    lost = blocks[2]
    files = dict(family_files)
    files[path] = "\n\n".join(blocks[:2] + blocks[3:]).encode("utf-8")
    result = verify_edition(files, family_html)
    assert not result.ok
    assert result.missing and not result.extra
    assert result.diffs[0].kind == "missing"
    assert result.diffs[0].file == path
    assert result.diffs[0].text.strip() == lost.strip()
    assert result.diffs[0].context  # previous paragraph


def test_added_text_is_extra_with_file(family_files, family_html):
    path = next(p for p in family_files if p.endswith("/art-0020.md"))
    files = dict(family_files)
    files[path] = family_files[path] + "\nЗайвий абзац тексту.\n".encode()
    result = verify_edition(files, family_html)
    assert not result.missing
    assert set(result.extra) == {"зайвий", "абзац", "тексту"}
    assert result.diffs[0].kind == "extra" and result.diffs[0].file == path


def test_level_heading_is_content(family_files, family_html):
    # a level holding only articles still has its heading in provisions.md
    path = (
        "codes/family/01-zahalni-polozhennia/01-simia-rehuliuvannia-simeinykh-vidnosyn/"
        "provisions.md"
    )
    files = {p: c for p, c in family_files.items() if p != path}
    result = verify_edition(files, family_html)
    assert not result.ok
    assert result.diffs[0].kind == "missing"
    assert result.diffs[0].text.startswith("Глава 1")


def test_note_in_html_is_not_missing():
    html = page(
        "Розділ I ЗАГАЛЬНІ ПОЛОЖЕННЯ",
        "Стаття 1. Мета",
        "Текст статті {Із змінами, внесеними згідно із Законом № 1-IX}",
        "{Статтю 1 доповнено частиною другою згідно із Законом № 2-IX}",
        "Друга частина.",
    )
    files = render_html(html)
    # the note paragraph is not rendered; the inline note is ignored on both sides
    assert "№ 2-IX" not in b"".join(files.values()).decode()
    assert verify_edition(files, html).ok


def test_excluded_article_stub_is_not_extra():
    html = page(
        "Розділ I ЗАГАЛЬНІ ПОЛОЖЕННЯ",
        "Стаття 1. Мета",
        "Текст.",
        "Стаття 2. {Статтю 2 виключено на підставі Закону № 3585-IX від 22.02.2024 }",
    )
    files = render_html(html)
    stub = next(c for p, c in files.items() if p.endswith("art-0002.md")).decode()
    assert "Виключена на підставі Закону № 3585-IX" in stub
    assert verify_edition(files, html).ok


def test_header_is_not_counted():
    html = page("КОДЕКС", "(Відомості Верховної Ради, 2002, № 1)", "Стаття 1. Мета", "Текст.")
    assert verify_edition(render_html(html), html).ok


def test_timing_100k_words(family_html):
    # ~24k words in the fixture + 5 marked copies of every body paragraph -> > 100k words
    paras = extract_paragraphs(family_html)
    n = 5
    big_paras = [
        *paras,
        *(
            type(p)(f"{p.text} копія{i}", "body")
            for i in range(n)
            for p in paras
            if p.kind == "body"
        ),
    ]
    tree = build_tree(big_paras, Edition("2947-14", date(2002, 1, 10), [], "law", ""))
    files = render(tree, Code("family", ("2947-14",), "Кодекс"))
    start = time.perf_counter()
    result = verify_paragraphs(big_paras, files)
    assert result.ok
    assert result.words > 100_000
    # a mismatch takes the slow path (paragraph matching)
    path = next(iter(files))
    broken = {p: c for p, c in files.items() if p != path}
    assert not verify_paragraphs(big_paras, broken).ok
    assert time.perf_counter() - start < 2


# --- jurik parse ---------------------------------------------------------------


@pytest.fixture
def parse_env(tmp_path, monkeypatch, write_registry):
    registry = write_registry('- slug: family\n  zakon_id: "2947-14"\n  title: Сімейний кодекс\n')
    monkeypatch.setattr(cli, "load_codes", lambda: load_codes(registry))

    def no_network(*a, **k):
        raise AssertionError("parse must not download")

    def make_client(cache, refetch=False):
        client = ZakonClient(cache)
        client.get = no_network
        return client

    monkeypatch.setattr(cli, "make_zakon_client", make_client)
    monkeypatch.chdir(tmp_path)
    client = ZakonClient(tmp_path / "cache")
    write_gz(client.card_path("2947-14"), (FIX / "card-family.json").read_text(encoding="utf-8"))
    write_gz(client.edition_path("2947-14", "20020110"), FAMILY.read_text(encoding="utf-8"))
    return tmp_path, client


def test_cli_parse_edition_ok(parse_env):
    tmp_path, _ = parse_env
    result = CliRunner().invoke(cli.main, ["parse", "--code", "family", "--edition", "20020110"])
    assert result.exit_code == 0, result.output
    assert "family 2947-14 ed20020110: ok" in result.output
    report = (tmp_path / "report" / "parse-failures.md").read_text(encoding="utf-8")
    assert "No failures in 1 edition(s)" in report


def test_cli_parse_failures_written_to_report(parse_env):
    tmp_path, client = parse_env
    html = FAMILY.read_text(encoding="utf-8")
    # an article paragraph that the tree cannot place twice -> TreeError for one edition
    write_gz(client.edition_path("2947-14", "20020110"), html.replace("Стаття 3.", "Стаття 1.", 1))
    result = CliRunner().invoke(cli.main, ["parse", "--code", "family", "--all"])
    assert result.exit_code == 1
    report = (tmp_path / "report" / "parse-failures.md").read_text(encoding="utf-8")
    assert "## family 2947-14 ed20020110" in report
    assert "TreeError" in report
    # editions not in the cache are failures, never downloaded
    assert "not cached" in report
