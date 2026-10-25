import gzip
import json
from datetime import date

import pytest
from gitrepo import git, tree_files

from jurik.build.fastimport import Stream, run
from jurik.build.history import (
    BuildError,
    CodeSource,
    cached_tree_loader,
    plan_linear,
    reason_of,
    sources_from_cache,
    write_linear,
)
from jurik.build.message import parse_trailers
from jurik.config import Code
from jurik.model import Article, DocTree, Edition, LawMeta, Unit
from jurik.rada.index import LawIndex
from jurik.zakon.card import edition_kind
from jurik.zakon.http import ZakonClient, write_gz

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України")
TAX = Code("tax", ("2755-17",), "Податковий кодекс України")
KUPAP = Code("admin-offences", ("80731-10", "80732-10"), "КУпАП")


def ed(zakon_id, d, *bases):
    return Edition(zakon_id, d, list(bases), edition_kind(list(bases)), f"https://x/{zakon_id}")


def tree(edition, *articles, chapter="1"):
    arts = [Article(n, f"Назва {n}", body) for n, body in articles]
    unit = Unit("chapter", chapter, f"Глава {chapter}", children=arts)
    return DocTree(edition.zakon_id, edition, "", [unit])


class Loader:
    def __init__(self):
        self.trees = {}

    def add(self, edition, *articles, chapter="1"):
        self.trees[(edition.zakon_id, edition.date)] = tree(edition, *articles, chapter=chapter)
        return edition

    def __call__(self, edition):
        return self.trees[(edition.zakon_id, edition.date)]


def trailers(step):
    return parse_trailers(step.message)


def test_reason_of():
    assert reason_of([]) == "unknown"
    assert reason_of(["2856-17"]) == "law"
    assert reason_of(["71-19", "79-19"]) == "multiple"
    assert reason_of(["v020p710-10"]) == "court"
    assert reason_of(["v020p710-10", "2856-17"]) == "multiple"
    assert reason_of(["123-2011-п"]) == "other"


def test_linear_steps_and_shared_bases_merge_across_codes():
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 1, 10)), ("1", "текст"), ("2", "два"))
    f2 = load.add(ed("2947-14", date(2011, 1, 1), "2856-17"), ("1", "текст"), ("2", "нове"))
    t1 = load.add(ed("2755-17", date(2010, 12, 2)), ("1", "податок"))
    t2 = load.add(ed("2755-17", date(2011, 1, 1), "2856-17"), ("1", "податок 2"))
    t3 = load.add(ed("2755-17", date(2012, 1, 1)), ("1", "податок 3"))
    sources = [
        CodeSource(FAMILY, {"2947-14": [f1, f2]}),
        CodeSource(TAX, {"2755-17": [t1, t2, t3]}),
    ]
    steps = plan_linear(sources, load)
    assert [(s.date, s.reason, s.codes) for s in steps] == [
        (date(2002, 1, 10), "initial", ["family"]),
        (date(2010, 12, 2), "initial", ["tax"]),
        (date(2011, 1, 1), "law", ["tax", "family"]),  # same-day order: by zakon_id
        (date(2012, 1, 1), "unknown", ["tax"]),
    ]
    joint = steps[2]
    assert joint.editions == [t2, f2]
    assert set(joint.changes.modified) == {
        "codes/family/01-hlava-1/art-0002.md",
        "codes/tax/01-hlava-1/art-0001.md",
        "codes/family/_meta.yaml",
        "codes/tax/_meta.yaml",
    }
    assert ("In-force", "2011-01-01") in trailers(joint)
    assert [v for k, v in trailers(joint) if k == "Edition"] == [
        "2755-17/ed20110101",
        "2947-14/ed20110101",
    ]
    assert steps[3].author.name == "Апарат Верховної Ради України"
    assert steps[0].author.name == steps[0].committer.name == "Верховна Рада України"
    assert all(s.author.when == s.date for s in steps)


def test_empty_bases_same_day_not_merged_across_codes():
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a"))
    f2 = load.add(ed("2947-14", date(2014, 2, 22)), ("1", "b"))
    t1 = load.add(ed("2755-17", date(2010, 12, 2)), ("1", "c"))
    t2 = load.add(ed("2755-17", date(2014, 2, 22)), ("1", "d"))
    steps = plan_linear(
        [CodeSource(FAMILY, {"2947-14": [f1, f2]}), CodeSource(TAX, {"2755-17": [t1, t2]})],
        load,
    )
    assert [s.codes for s in steps if s.date == date(2014, 2, 22)] == [["tax"], ["family"]]


def test_kupap_folder_is_union_of_latest_parts():
    load = Loader()
    a1 = load.add(ed("80731-10", date(1984, 12, 7)), ("1", "a1"), chapter="1")
    b1 = load.add(ed("80732-10", date(1984, 12, 7)), ("200", "b1"), chapter="20")
    a2 = load.add(ed("80731-10", date(1990, 1, 1), "100-12"), ("1", "a2"), chapter="1")
    b2 = load.add(ed("80732-10", date(1995, 1, 1), "200-12"), ("200", "b2"), chapter="20")
    src = CodeSource(KUPAP, {"80731-10": [a1, a2], "80732-10": [b1, b2]})
    steps = plan_linear([src], load)
    assert [s.reason for s in steps] == ["initial", "law", "law"]
    assert steps[0].editions == [a1, b1]  # one initial step for both parts
    art1 = "codes/admin-offences/01-hlava-1/art-0001.md"
    art200 = "codes/admin-offences/20-hlava-20/art-0200.md"
    assert {art1, art200} <= set(steps[0].changes.modified)
    # part 2 amended: part 1 text stays (from its latest edition), only part 2 file changes
    assert art200 in steps[2].changes.modified
    assert art1 not in steps[2].changes.modified
    assert not steps[2].changes.deleted


def test_repealed_code_deletes_folder_and_drops_later_editions():
    code = Code(
        "commercial",
        ("436-15",),
        "Господарський кодекс України",
        repealed=date(2025, 8, 28),
        repealed_by="4196-20",
    )
    load = Loader()
    e1 = load.add(ed("436-15", date(2004, 1, 1)), ("1", "x"))
    e2 = load.add(ed("436-15", date(2030, 1, 1), "1-20"), ("1", "y"))
    steps = plan_linear([CodeSource(code, {"436-15": [e1, e2]})], load)
    assert [s.reason for s in steps] == ["initial", "repealed"]
    rep = steps[1]
    assert rep.date == date(2025, 8, 28)
    assert not rep.changes.modified
    assert rep.changes.deleted == set(steps[0].changes.modified)
    assert ("Reason", "repealed") in trailers(rep)
    assert ("In-force", "2025-08-28") in trailers(rep)


def test_index_enriches_message():
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a"))
    f2 = load.add(ed("2947-14", date(2003, 1, 1), "407-15"), ("1", "b"))
    meta = LawMeta(
        "407-15",
        "Про внесення змін до Сімейного кодексу України",
        None,
        [],
        None,
        date(2002, 12, 20),
        None,
        None,
        "adopted",
        "407-IV",
    )
    index = LawIndex(by_law={"407-15": meta})
    steps = plan_linear([CodeSource(FAMILY, {"2947-14": [f1, f2]})], load, index=index)
    assert steps[1].message.startswith("Закон № 407-IV: Про внесення змін до Сімейного")
    assert ("Adopted", "2002-12-20") in trailers(steps[1])


def test_write_linear_repo_matches_last_render(tmp_path):
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a"), ("2", "b")),
        load.add(ed("2947-14", date(2005, 6, 25), "2620-15"), ("1", "a"), ("2", "b2")),
        load.add(ed("2947-14", date(2006, 1, 1), "2853-15", "2901-15"), ("2", "b3")),
    ]
    steps = plan_linear([CodeSource(FAMILY, {"2947-14": eds})], load)
    stream = Stream()
    write_linear(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    log = git(
        repo,
        "log",
        "--format=%ad|%(trailers:key=In-force,valueonly,separator=%x2C)|%(trailers:key=Reason,valueonly,separator=%x2C)",
        "--date=short",
    )
    assert [line for line in log.splitlines() if line] == [
        "2006-01-01|2006-01-01|multiple",
        "2005-06-25|2005-06-25|law",
        "2002-01-10|2002-01-10|initial",
    ]
    files = tree_files(repo)
    assert "codes/family/01-hlava-1/art-0001.md" not in files
    assert files["codes/family/01-hlava-1/art-0002.md"] == "# Стаття 2. Назва 2\n\nb3\n".encode()
    stat = git(repo, "show", "--stat", "--format=", "HEAD~1")
    assert "art-0002.md" in stat and "art-0001.md" not in stat


def _cache_card(cache, zakon_id, dates):
    card = {
        "nreg": zakon_id,
        "nazva": "X",
        "orgdat": dates[0],
        "eds": [{"datred": d, "pidstava": ""} for d in dates],
    }
    client = ZakonClient(cache)
    write_gz(client.card_path(zakon_id), json.dumps(card))
    return client


def test_sources_from_cache_requires_every_edition(tmp_path, fixtures_dir):
    client = _cache_card(tmp_path, "2947-14", [20020110, 20030101])
    html = (fixtures_dir / "zakon" / "2947-14_ed20020110_print.html").read_text("utf-8")
    write_gz(client.edition_path("2947-14", "20020110"), html)
    with pytest.raises(BuildError, match="1 edition.*ed20030101"):
        sources_from_cache([FAMILY], client)
    write_gz(client.edition_path("2947-14", "20030101"), html)
    [src] = sources_from_cache([FAMILY], client)
    assert [e.date for e in src.editions["2947-14"]] == [date(2002, 1, 10), date(2003, 1, 1)]
    assert src.adopted == date(2002, 1, 10)
    tree = cached_tree_loader(client)(src.editions["2947-14"][0])
    assert tree.units
    client.close()


def test_sources_from_cache_missing_card(tmp_path):
    with ZakonClient(tmp_path) as client, pytest.raises(BuildError, match="not cached"):
        sources_from_cache([FAMILY], client)


def test_gz_helper_roundtrip(tmp_path):
    # guard: write_gz output is what the build reads
    path = tmp_path / "x.gz"
    write_gz(path, "абв")
    assert gzip.decompress(path.read_bytes()).decode() == "абв"
