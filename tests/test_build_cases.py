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
from jurik.zakon.paragraphs import Para, extract_paragraphs
from jurik.zakon.tree import build_tree

FIX = Path(__file__).parent / "fixtures" / "zakon"
ELECTORAL = "396-20_ed20261022_build_fragment.html"


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
