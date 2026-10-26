import gzip
import json
from datetime import date

import pytest
from gitrepo import git, git_ok, tree_files

from jurik.build.fastimport import Stream, run
from jurik.build.history import (
    BuildError,
    CodeSource,
    law_sort_key,
    plan_full,
    plan_linear,
    reason_of,
    write_steps,
)
from jurik.build.message import parse_trailers
from jurik.build.sources import cached_tree_loader, sources_from_cache
from jurik.config import Code
from jurik.model import Article, BillRef, DocTree, Edition, LawMeta, Person, Unit
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
    }  # _meta.yaml has no edition date: it does not change with every edition
    assert ("In-force", "2011-01-01") in trailers(joint)
    assert [v for k, v in trailers(joint) if k == "Edition"] == [
        "2755-17/ed20110101",
        "2947-14/ed20110101",
    ]
    assert steps[3].author.name == "Апарат Верховної Ради України"
    assert steps[0].author.name == "Верховна Рада України"
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


CIVIL = Code("civil", ("435-15",), "Цивільний кодекс України")
ENTRY = date(2004, 1, 1)


def _editions_never_go_back(steps):
    last = {}
    for s in steps:
        if s.ref != "main":
            continue
        for e in s.editions:
            assert last.get(e.zakon_id, date.min) <= e.date, (e, s.message)
            last[e.zakon_id] = e.date


def test_same_in_force_date_initial_before_shared_basis_step():
    """A basis joins family's pre-entry edition (dated before civil's first edition) with a
    later civil edition; all four are in force from 2004-01-01. Civil's initial must come
    before the law step, or the law step adds the whole code and the initial is empty."""
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 12, 1)), ("1", "сім'я"))
    f2 = load.add(ed("2947-14", date(2003, 1, 1), "100-15"), ("1", "сім'я 2"))
    c1 = load.add(ed("435-15", date(2003, 1, 16)), ("1", "цивільний"), ("2", "два"))
    c2 = load.add(ed("435-15", ENTRY, "100-15"), ("1", "цивільний"), ("2", "нове"))
    sources = [
        CodeSource(FAMILY, {"2947-14": [f1, f2]}, in_force={"2947-14": ENTRY}),
        CodeSource(CIVIL, {"435-15": [c1, c2]}, in_force={"435-15": ENTRY}),
    ]
    for steps in (plan_linear(sources, load), plan_full(sources, load)):
        main = [s for s in steps if s.ref == "main"]
        assert [(s.reason, s.codes) for s in main] == [
            ("initial", ["family"]),
            ("initial", ["civil"]),
            ("law", ["family", "civil"]),
        ]
        assert all(s.date == ENTRY for s in main)
        assert all(s.changes for s in main if s.editions)  # no empty commit with Edition:
        assert "codes/civil/01-hlava-1/art-0002.md" in main[1].changes.modified
        law = main[2].changes.modified
        assert set(law) == {
            "codes/family/01-hlava-1/art-0001.md",
            "codes/civil/01-hlava-1/art-0002.md",
        }  # only the amendments, not the whole civil code
        _editions_never_go_back(steps)


def test_same_in_force_date_cycle_becomes_one_step():
    """Family a1 (basis P) < a2 (Q), civil b1 (Q) < b2 (P), all in force on one date:
    {a1, b2} needs b1 first and {a2, b1} needs a1 first, so both are one step."""
    load = Loader()
    f0 = load.add(ed("2947-14", date(2002, 1, 1)), ("1", "a0"))
    a1 = load.add(ed("2947-14", date(2002, 3, 1), "1-15"), ("1", "a1"))
    a2 = load.add(ed("2947-14", date(2002, 9, 1), "2-15"), ("1", "a2"))
    c0 = load.add(ed("435-15", date(2002, 1, 2)), ("1", "c0"))
    b1 = load.add(ed("435-15", date(2002, 2, 1), "2-15"), ("1", "b1"))
    b2 = load.add(ed("435-15", date(2002, 5, 1), "1-15"), ("1", "b2"))
    sources = [
        CodeSource(FAMILY, {"2947-14": [f0, a1, a2]}, in_force={"2947-14": ENTRY}),
        CodeSource(CIVIL, {"435-15": [c0, b1, b2]}, in_force={"435-15": ENTRY}),
    ]
    steps = plan_linear(sources, load)
    assert [s.reason for s in steps] == ["initial", "initial", "multiple"]
    assert steps[2].editions == [b1, a1, b2, a2]
    assert all(s.changes for s in steps)
    _editions_never_go_back(steps)


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
    write_steps(steps, stream)
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


def law(nreg, bill=None, registered=None, adopted=None, initiators=(), title="Про зміни"):
    return LawMeta(
        nreg, title, bill, list(initiators), registered, adopted, None, None, "adopted", ""
    )


PETRENKO = Person("Іван Петренко", "petrenko-ivan-ivanovych")
KOVAL = Person("Олена Коваль", "koval-olena-petrivna")
ART = "codes/family/01-hlava-1/art-{:04d}.md"


def full_history(tmp_path):
    """Family code: initial; law A (two stages); law B registered before A was merged;
    two laws in one edition; a law without metadata; a court decision."""
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0"), ("2", "b0"), ("3", "c0")),
        load.add(ed("2947-14", date(2003, 7, 1), "100-15"), ("1", "a1"), ("2", "b0"), ("3", "c0")),
        load.add(ed("2947-14", date(2003, 10, 1), "200-15"), ("1", "a1"), ("2", "b1"), ("3", "c0")),
        load.add(ed("2947-14", date(2004, 1, 1), "100-15"), ("1", "a1"), ("2", "b1"), ("3", "c1")),
        load.add(
            ed("2947-14", date(2005, 1, 1), "301-15", "300-15"),
            ("1", "a2"),
            ("2", "b2"),
            ("3", "c1"),
        ),
        load.add(ed("2947-14", date(2006, 1, 1), "999-15"), ("1", "a3"), ("2", "b2"), ("3", "c1")),
        load.add(
            ed("2947-14", date(2007, 1, 1), "v001p710-07"), ("1", "a3"), ("2", "b3"), ("3", "c1")
        ),
    ]
    index = LawIndex(
        by_law={
            "100-15": law(
                "100-15", BillRef(4, "1234-д"), date(2003, 3, 1), date(2003, 6, 1), [PETRENKO]
            ),
            "200-15": law(
                "200-15",
                BillRef(4, "2000"),
                date(2003, 2, 1),
                date(2003, 9, 1),
                [PETRENKO, KOVAL],
                title="Про шлюб",
            ),
            "300-15": law(
                "300-15", BillRef(4, "3000"), date(2004, 5, 1), date(2004, 12, 1), [KOVAL]
            ),
            "301-15": law("301-15", BillRef(4, "3001"), date(2004, 6, 1), date(2004, 12, 2)),
        }
    )
    steps = plan_full([CodeSource(FAMILY, {"2947-14": eds})], load, index=index)
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    return steps, repo, load, eds


def test_plan_full_steps(tmp_path):
    steps, _, _, _ = full_history(tmp_path)
    assert [(s.kind, s.ref, s.stage) for s in steps] == [
        ("commit", "main", None),
        ("branch", "bill/4/1234-d", 1),
        ("merge", "main", 1),
        ("branch", "bill/4/2000", 1),
        ("merge", "main", 1),
        ("branch", "bill/4/1234-d", 2),
        ("merge", "main", 2),
        ("branch", "bill/4/3000", 1),
        ("merge", "main", 1),
        ("branch", "bill/law/999-15", 1),
        ("merge", "main", 1),
        ("commit", "main", None),
    ]
    # branch from main in force at registration; stage 2 continues the branch
    assert steps[1].parents == (0,)
    assert steps[3].parents == (0,)  # registered 2003-02-01, before law A came into force
    assert steps[5].parents == (1,)
    assert steps[6].parents == (4, 5)
    assert steps[7].parents == (6,)  # registered 2004-05-01: head in force 2004-01-01
    assert steps[9].parents == (8,)  # no metadata: from the merge parent
    assert steps[11].parents == (10,)


def test_full_repo_branches_and_merges(tmp_path):
    steps, repo, load, eds = full_history(tmp_path)
    first_parent = git(repo, "log", "--first-parent", "--format=%P", "main").splitlines()
    assert [len(p.split()) for p in first_parent] == [1, 2, 2, 2, 2, 2, 0]
    # merge tree = new official render
    files = tree_files(repo)
    assert files[ART.format(1)] == "# Стаття 1. Назва 1\n\na3\n".encode()
    assert files[ART.format(2)] == "# Стаття 2. Назва 2\n\nb3\n".encode()
    # every commit of main = cumulative render (the merge tree is the new official text)
    expected: dict[str, bytes] = {}
    main_steps = [s for s in steps if s.ref == "main"]
    for back, step in zip(range(len(main_steps) - 1, -1, -1), main_steps, strict=True):
        expected = step.changes.apply(expected)
        assert tree_files(repo, f"main~{back}") == expected
    # branch of law B: base = initial commit even though law A merged in between
    assert (
        git(repo, "rev-parse", "bill/4/2000^").strip() == git(repo, "rev-parse", "main~6").strip()
    )
    changed = set(git(repo, "diff", "--name-only", "bill/4/2000^", "bill/4/2000").split())
    assert changed == set(steps[3].changes.modified)
    assert ART.format(1) not in changed and ART.format(2) in changed
    # the branch keeps the old text of art. 1, the merge has law A's text
    assert tree_files(repo, "bill/4/2000")[ART.format(1)].endswith(b"a0\n")
    assert tree_files(repo, "main~4")[ART.format(1)].endswith(b"a1\n")


def test_full_staged_law_merged_twice(tmp_path):
    _, repo, _, _ = full_history(tmp_path)
    merges = git(
        repo, "log", "--first-parent", "--format=%H %P|%(trailers:key=Stage,valueonly)", "main"
    )
    branch = git(repo, "rev-list", "bill/4/1234-d").split()
    assert len(branch) == 3  # two stages on top of the initial commit
    rows = [line.split("|") for line in merges.splitlines() if line]
    merged = [r for r in rows if len(r[0].split()) == 3 and r[0].split()[2] in branch]
    assert [r[1] for r in merged] == ["2", "1"]
    stat = git(repo, "diff", "--name-only", "bill/4/1234-d^", "bill/4/1234-d").split()
    assert ART.format(3) in stat and ART.format(1) not in stat


def test_full_multi_basis_one_merge_two_laws(tmp_path):
    steps, repo, _, _ = full_history(tmp_path)
    msg = git(repo, "log", "-1", "--format=%B", "main~2")
    trailers = parse_trailers(msg)
    assert [v for k, v in trailers if k == "Law"] == ["300-IV", "301-IV"]
    assert [v for k, v in trailers if k == "Bill"] == ["4/3000", "4/3001"]
    assert ("Reason", "multiple") in trailers
    assert (
        git(repo, "rev-parse", "main~2^2").strip() == git(repo, "rev-parse", "bill/4/3000").strip()
    )
    assert "bill/4/3001" not in git(repo, "for-each-ref", "--format=%(refname:short)")


def test_full_law_without_meta_and_court(tmp_path):
    _, repo, _, _ = full_history(tmp_path)
    msg = git(repo, "log", "-1", "--format=%B", "main~1")
    assert ("Bill", "unknown") in parse_trailers(msg)
    assert git(repo, "log", "-1", "--format=%an|%ad", "--date=short", "bill/law/999-15") == (
        "Верховна Рада України|2006-01-01\n"
    )
    assert ("Reason", "court") in parse_trailers(git(repo, "log", "-1", "--format=%B", "main"))
    assert git(repo, "log", "-1", "--format=%P", "main").count(" ") == 0


def test_full_authors_and_dates(tmp_path):
    _, repo, _, _ = full_history(tmp_path)
    fmt = "--format=%an|%ae|%ad|%cn|%ce|%cd"
    branch = git(repo, "log", "-1", fmt, "--date=iso", "bill/4/2000").strip()
    assert branch == (
        "Іван Петренко|petrenko-ivan-ivanovych@deputies.invalid|2003-02-01 12:00:00 +0200|"
        "Іван Петренко|petrenko-ivan-ivanovych@deputies.invalid|2003-02-01 12:00:00 +0200"
    )
    merge = git(repo, "log", "-1", fmt, "--date=iso", "main~4").strip()
    assert merge == (
        "Верховна Рада України|rada@laws.invalid|2003-09-01 12:00:00 +0300|"
        "Верховна Рада України|rada@laws.invalid|2003-09-01 12:00:00 +0300"
    )
    body = git(repo, "log", "-1", "--format=%B", "bill/4/2000")
    assert "Co-authored-by: Олена Коваль <koval-olena-petrivna@deputies.invalid>" in body
    trailers = parse_trailers(git(repo, "log", "-1", "--format=%B", "main~4"))
    assert ("In-force", "2003-10-01") in trailers
    assert ("Edition", "2947-14/ed20031001") in trailers
    assert ("Stage", "1") in trailers
    # multi-basis: the branch (and its author) belong to the first law by number
    assert git(repo, "log", "-1", "--format=%an", "bill/4/3000").strip() == "Олена Коваль"


def test_branch_never_starts_before_code_exists(tmp_path):
    load = Loader()
    c1 = load.add(ed("254к/96-ВР", date(1996, 6, 28)), ("1", "x"))
    f1 = load.add(ed("2947-14", date(2004, 1, 1)), ("1", "a"))
    f2 = load.add(ed("2947-14", date(2005, 1, 1), "100-15"), ("1", "b"))
    constitution = Code("constitution", ("254к/96-ВР",), "Конституція України")
    index = LawIndex(by_law={"100-15": law("100-15", BillRef(4, "1"), date(2000, 1, 1))})
    steps = plan_full(
        [CodeSource(constitution, {"254к/96-ВР": [c1]}), CodeSource(FAMILY, {"2947-14": [f1, f2]})],
        load,
        index=index,
    )
    assert [s.kind for s in steps] == ["commit", "commit", "branch", "merge"]
    assert steps[2].parents == (1,)  # family initial, not the constitution-only commit


def rejected(number, registered, status="rejected", title="Про внесення змін до Сімейного кодексу"):
    return LawMeta(
        "",
        title,
        BillRef(4, number),
        [KOVAL, PETRENKO],
        registered,
        None,
        None,
        None,
        status,
        "",
        card_url=f"https://rada/card/{number}",
        phase_date=date(2006, 3, 1) if status != "pending" else None,
    )


class FakeTexts:
    def __init__(self, texts):
        self.texts = texts
        self.calls = []

    def fetch(self, bill):
        self.calls.append(bill)
        return self.texts.get(bill.number)


def rejected_history(tmp_path, *, bills=True, bills_limit=None, texts=None):
    family = Code("family", ("2947-14",), "Сімейний кодекс України", (r"сімейн\w* кодекс",))
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0")),
        load.add(ed("2947-14", date(2003, 7, 1), "100-15"), ("1", "a1")),
        load.add(ed("2947-14", date(2005, 1, 1)), ("1", "a2")),
    ]
    by_law = {"100-15": law("100-15", BillRef(4, "1234"), date(2003, 3, 1), date(2003, 6, 1))}
    index = LawIndex(by_law=by_law)
    if bills:
        index.bills = {
            BillRef(4, "5555-д"): rejected("5555-д", date(2004, 2, 1)),
            BillRef(4, "1000"): rejected("1000", date(2001, 5, 1), status="pending"),
            BillRef(4, "7777"): rejected("7777", date(2004, 3, 1), title="Про інше"),
            BillRef(4, "1234"): by_law["100-15"],
        }
    steps = plan_full(
        [CodeSource(family, {"2947-14": eds})],
        load,
        index=index,
        bill_texts=texts,
        bills_limit=bills_limit,
    )
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / ("laws-bills" if bills else "laws")
    run(stream, repo)
    return steps, repo


def test_rejected_bill_branch_without_merge(tmp_path):
    texts = FakeTexts({"5555-д": "Текст законопроекту.\n\nДругий абзац."})
    steps, repo = rejected_history(tmp_path, texts=texts)
    branches = [s for s in steps if s.reason == "bill"]
    # earliest registered first; adopted bills and bills not naming the code are skipped
    assert [s.ref for s in branches] == ["bill/4/1000", "bill/4/5555-d"]
    assert texts.calls == [BillRef(4, "1000"), BillRef(4, "5555-д")]
    assert all(s.kind == "branch" for s in branches)
    assert not any(s.branch in {b.ref for b in branches} for s in steps if s.kind == "merge")
    # base = main commit in force on registration day; never before the code exists
    assert branches[0].parents == (0,)  # registered 2001, family initial is 2002
    assert steps[branches[1].parents[0]].date == date(2003, 7, 1)
    refs = git(repo, "for-each-ref", "--format=%(refname:short)").split()
    assert "bill/4/5555-d" in refs and "bill/4/1000" in refs
    for ref in ("bill/4/5555-d", "bill/4/1000"):
        assert not git_ok(repo, "merge-base", "--is-ancestor", ref, "main")  # never merged
    changed = git(repo, "diff", "--name-only", "bill/4/5555-d^", "bill/4/5555-d").split()
    assert changed == ["bills/4/5555-d.md"]
    content = tree_files(repo, "bill/4/5555-d")["bills/4/5555-d.md"].decode()
    assert content.startswith("# Законопроект № 5555-д (IV скликання)\n")
    assert "- Статус: відхилено (2006-03-01)" in content
    assert content.endswith("---\n\nТекст законопроекту.\n\nДругий абзац.\n")
    stub = tree_files(repo, "bill/4/1000")["bills/4/1000.md"].decode()
    assert stub.endswith("Текст недоступний. Див. картку законопроекту: https://rada/card/1000\n")
    msg = git(repo, "log", "-1", "--format=%an|%ad|%B", "--date=short", "bill/4/5555-d")
    assert msg.startswith("Олена Коваль|2004-02-01|Законопроект № 5555-д: Про внесення змін")
    trailers = parse_trailers(msg)
    assert ("Status", "rejected") in trailers
    assert ("Status-date", "2006-03-01") in trailers
    assert ("Bill", "4/5555-д") in trailers
    assert not any(k == "In-force" for k, _ in trailers)


def test_rejected_bills_leave_main_unchanged(tmp_path):
    with_bills, repo1 = rejected_history(tmp_path, bills=True)
    without, repo2 = rejected_history(tmp_path, bills=False)
    assert [s.message for s in with_bills if s.ref == "main"] == [
        s.message for s in without if s.ref == "main"
    ]
    assert git(repo1, "rev-parse", "main") == git(repo2, "rev-parse", "main")
    assert "bills/" not in git(repo1, "ls-tree", "-r", "--name-only", "main")


def test_bills_limit(tmp_path):
    steps, _ = rejected_history(tmp_path, bills_limit=1)
    assert [s.ref for s in steps if s.reason == "bill"] == ["bill/4/1000"]
    steps, _ = rejected_history(tmp_path, bills_limit=0)
    assert not [s for s in steps if s.reason == "bill"]


# --- branch bases, stages and names ------------------------------------------------------


def _merge_and_branch_diffs(repo, merge_rev):
    branch = git(repo, "diff", "--raw", "--no-renames", f"{merge_rev}^2^", f"{merge_rev}^2")
    merge = git(repo, "diff", "--raw", "--no-renames", f"{merge_rev}^1", merge_rev)
    return sorted(branch.splitlines()), sorted(merge.splitlines())


def test_intermediate_law_on_same_file_moves_branch_base(tmp_path):
    """Law B registered before law A, both change art. 1: B's branch starts after A's
    merge, so its diff is only B's change and `git log -- art-0001.md` lists both laws."""
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0"), ("2", "b0")),
        load.add(ed("2947-14", date(2003, 7, 1), "100-15"), ("1", "a1 (A)"), ("2", "b0")),
        load.add(ed("2947-14", date(2004, 1, 1), "200-15"), ("1", "a1 (A) + (B)"), ("2", "b0")),
    ]
    index = LawIndex(
        by_law={
            "100-15": law("100-15", BillRef(4, "1"), date(2003, 3, 1), date(2003, 6, 1)),
            "200-15": law("200-15", BillRef(4, "2"), date(2002, 6, 1), date(2003, 12, 1)),
        }
    )
    steps = plan_full([CodeSource(FAMILY, {"2947-14": eds})], load, index=index)
    b_branch = next(s for s in steps if s.ref == "bill/4/2")
    assert steps[b_branch.parents[0]].kind == "merge"  # A's merge, not the initial commit
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    branch, merge = _merge_and_branch_diffs(repo, "main")
    assert branch == merge
    subjects = git(repo, "log", "--format=%s", "main", "--", ART.format(1)).splitlines()
    assert len([s for s in subjects if s.startswith("Закон")]) == 2  # both merges listed
    blame = git(repo, "blame", "--line-porcelain", "main", "--", ART.format(1))
    assert "Законопроект № 2" in blame


def test_branch_keeps_registration_base_for_untouched_paths(tmp_path):
    steps, repo, _, _ = full_history(tmp_path)
    for back in range(1, 6):
        if len(git(repo, "log", "-1", "--format=%P", f"main~{back}").split()) == 2:
            branch, merge = _merge_and_branch_diffs(repo, f"main~{back}")
            assert branch == merge


def test_later_stage_rebased_when_tip_is_stale(tmp_path):
    """Stage 2 of law A changes a file that law B changed after A's stage 1: the stage 2
    commit starts from main (which contains stage 1), not from A's stale tip."""
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0"), ("2", "b0")),
        load.add(ed("2947-14", date(2003, 7, 1), "100-15"), ("1", "a1"), ("2", "b0")),
        load.add(ed("2947-14", date(2003, 9, 1), "200-15"), ("1", "a1"), ("2", "b1")),
        load.add(ed("2947-14", date(2004, 1, 1), "100-15"), ("1", "a1"), ("2", "b2")),
    ]
    index = LawIndex(
        by_law={
            "100-15": law("100-15", BillRef(4, "1"), date(2003, 3, 1), date(2003, 6, 1)),
            "200-15": law("200-15", BillRef(4, "2"), date(2003, 4, 1), date(2003, 8, 1)),
        }
    )
    steps = plan_full([CodeSource(FAMILY, {"2947-14": eds})], load, index=index)
    stage2 = [s for s in steps if s.ref == "bill/4/1"][1]
    assert stage2.stage == 2
    assert steps[stage2.parents[0]].ref == "main"
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    branch, merge = _merge_and_branch_diffs(repo, "main")
    assert branch == merge
    tips = git(repo, "rev-list", "bill/4/1").split()
    assert (
        len([t for t in tips if "Законопроект № 1" in git(repo, "log", "-1", "--format=%s", t)])
        == 2
    )


def test_same_date_overlapping_bases_are_one_step():
    """Code A's edition has bases [X], code B's [X, Y] on the same date: one merge."""
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a"))
    f2 = load.add(ed("2947-14", date(2010, 1, 1), "999-17"), ("1", "b"))
    t1 = load.add(ed("2755-17", date(2002, 1, 10)), ("1", "c"))
    t2 = load.add(ed("2755-17", date(2010, 1, 1), "999-17", "1001-17"), ("1", "d"))
    steps = plan_full(
        [CodeSource(FAMILY, {"2947-14": [f1, f2]}), CodeSource(TAX, {"2755-17": [t1, t2]})],
        load,
    )
    merges = [s for s in steps if s.kind == "merge"]
    assert len(merges) == 1
    assert merges[0].bases == ("999-17", "1001-17")  # numeric order, not string order
    assert merges[0].branch == "bill/law/999-17"
    assert set(merges[0].codes) == {"family", "tax"}
    assert merges[0].stage == 1


def test_law_sort_key_numeric():
    assert sorted(["1001-20", "999-20", "5-19", "254к/96-вр"], key=law_sort_key) == [
        "254к/96-вр",
        "5-19",
        "999-20",
        "1001-20",
    ]


def test_two_laws_of_one_bill_get_two_branches():
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0")),
        load.add(ed("2947-14", date(2003, 1, 1), "100-15"), ("1", "a1")),
        load.add(ed("2947-14", date(2004, 1, 1), "200-15"), ("1", "a2")),
    ]
    same_bill = BillRef(4, "1234")
    index = LawIndex(
        by_law={
            "100-15": law("100-15", same_bill, date(2002, 6, 1), date(2002, 12, 1)),
            "200-15": law("200-15", same_bill, date(2002, 6, 1), date(2003, 12, 1)),
        }
    )
    steps = plan_full([CodeSource(FAMILY, {"2947-14": eds})], load, index=index)
    assert [(s.branch, s.stage) for s in steps if s.kind == "merge"] == [
        ("bill/4/1234", 1),
        ("bill/4/1234-2", 1),
    ]


def test_full_mode_law_touching_two_codes_and_repeal(tmp_path):
    commercial = Code("commercial", ("436-15",), "Господарський кодекс", repealed=date(2020, 1, 1))
    load = Loader()
    f1 = load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a"))
    f2 = load.add(ed("2947-14", date(2010, 1, 1), "100-17"), ("1", "b"))
    c1 = load.add(ed("436-15", date(2004, 1, 1)), ("1", "x"))
    c2 = load.add(ed("436-15", date(2010, 1, 1), "100-17"), ("1", "y"))
    steps = plan_full(
        [
            CodeSource(FAMILY, {"2947-14": [f1, f2]}),
            CodeSource(commercial, {"436-15": [c1, c2]}),
        ],
        load,
    )
    branch = next(s for s in steps if s.kind == "branch")
    assert {p.split("/")[1] for p in branch.changes.modified} == {"family", "commercial"}
    assert branch.changes is not steps[steps.index(branch) + 1].changes  # copied
    assert [s.reason for s in steps if s.ref == "main"][-1] == "repealed"
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    assert "codes/commercial" not in git(repo, "ls-tree", "-r", "--name-only", "main")
    assert "codes/commercial" in git(repo, "ls-tree", "-r", "--name-only", "main~1")


def test_rejected_ref_collision_gets_suffix_and_no_registration_uses_head(tmp_path):
    family = Code("family", ("2947-14",), "Сімейний кодекс України", (r"сімейн\w* кодекс",))
    load = Loader()
    eds = [
        load.add(ed("2947-14", date(2002, 1, 10)), ("1", "a0")),
        load.add(ed("2947-14", date(2003, 7, 1), "100-15"), ("1", "a1")),
    ]
    adopted = law("100-15", BillRef(4, "1234"), date(2003, 3, 1), date(2003, 6, 1))
    index = LawIndex(by_law={"100-15": adopted})
    index.bills = {
        BillRef(4, "1234"): adopted,
        BillRef(4, "1234-Д"): rejected("1234-Д", None),  # same ref as …/1234-д below
        BillRef(4, "1234-д"): rejected("1234-д", None),
        BillRef(4, "1234-x"): rejected("1234-x", date(2002, 5, 1)),
    }
    steps = plan_full([CodeSource(family, {"2947-14": eds})], load, index=index)
    bills = [s for s in steps if s.reason == "bill"]
    assert [s.ref for s in bills] == ["bill/4/1234-x", "bill/4/1234-d", "bill/4/1234-d-2"]
    head = max(i for i, s in enumerate(steps) if s.ref == "main")
    assert bills[1].parents == (head,) and bills[2].parents == (head,)
    assert bills[0].parents == (0,)


def test_sources_from_cache_bad_card_and_in_force(tmp_path):
    client = _cache_card(tmp_path, "2947-14", [20020110])
    write_gz(client.card_path("2947-14"), "not json")
    with pytest.raises(BuildError, match="family: card is not JSON"):
        sources_from_cache([FAMILY], client)
    card = {
        "nreg": "2947-14",
        "orgdat": 20020110,
        "eds": [{"datred": 20020110}],
        "hist": [{"podid": 4, "poddat": 20020110}, {"podid": 1, "poddat": 20040101}],
    }
    write_gz(client.card_path("2947-14"), json.dumps(card))
    write_gz(client.edition_path("2947-14", "20020110"), "<html></html>")
    [src] = sources_from_cache([FAMILY], client, today=date(2026, 1, 1))
    assert src.in_force == {"2947-14": date(2004, 1, 1)}
    client.close()


def test_cached_tree_loader_missing_file_is_build_error(tmp_path):
    with ZakonClient(tmp_path) as client:
        load = cached_tree_loader(client)
        with pytest.raises(BuildError, match="ed20020110"):
            load(ed("2947-14", date(2002, 1, 10)))


def test_cached_tree_loader_truncated_gz_is_build_error(tmp_path):
    with ZakonClient(tmp_path) as client:
        path = client.edition_path("2947-14", "20020110")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress("<html>текст</html>".encode() * 50)[:-12])
        with pytest.raises(BuildError, match="ed20020110"):
            cached_tree_loader(client)(ed("2947-14", date(2002, 1, 10)))
