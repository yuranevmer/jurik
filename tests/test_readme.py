from datetime import date

from gitrepo import git, tree_files

from jurik.build.fastimport import Stream, run
from jurik.build.history import CodeSource, plan_full, plan_linear, write_steps
from jurik.build.readme import README_PATH, add_readme, readme_markdown
from jurik.config import Code
from jurik.model import Article, DocTree, Edition, Unit
from jurik.zakon.card import edition_kind

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України")
KUPAP = Code("admin-offences", ("80731-10", "80732-10"), "КУпАП")
COMMERCIAL = Code(
    "commercial", ("436-15",), "Господарський кодекс України", repealed=date(2025, 8, 28)
)


def ed(d, *bases):
    return Edition("2947-14", d, list(bases), edition_kind(list(bases)), "https://x/2947-14")


def load(edition):
    art = Article("1", "Назва", f"текст {edition.date}")
    return DocTree("2947-14", edition, "", [Unit("chapter", "1", "Глава", children=[art])])


def test_readme_content():
    text = readme_markdown([FAMILY, KUPAP, COMMERCIAL])
    assert text.startswith("# Конституція та кодекси України з історією змін\n")
    # codes table: folder, title, zakon links, repeal date
    assert "| `codes/family` | Сімейний кодекс України |" in text
    assert "[80731-10](https://zakon.rada.gov.ua/laws/show/80731-10), [80732-10]" in text
    assert "Господарський кодекс України (втратив чинність 28.08.2025)" in text
    # layout, branch model, trailers, time axes, attribution, warning about bill/*
    for needle in (
        "provisions.md",
        "closing.md",
        "preamble.md",
        "art-0183-7_2.md",
        "bills/<скликання>/<номер>.md",
        "`In-force:`",
        "дата **прийняття**",
        "`Registered:`",
        "`Status-date:`",
        "не стали законом",
        "zakon.rada.gov.ua",
        "data.rada.gov.ua",
        "git log --follow",
    ):
        assert needle in text, needle
    assert "не більше" not in text
    assert "не більше 500 " in readme_markdown([FAMILY], bills_limit=500)


def test_add_readme_goes_into_first_main_commit(tmp_path):
    eds = [ed(date(2002, 1, 10)), ed(date(2003, 1, 1), "407-15")]
    steps = plan_full([CodeSource(FAMILY, {"2947-14": eds})], load)
    add_readme(steps, [FAMILY])
    assert README_PATH in steps[0].changes.modified
    assert all(README_PATH not in s.changes.modified for s in steps[1:])
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    readme = tree_files(repo)[README_PATH].decode("utf-8")
    assert "`codes/family`" in readme
    log = git(repo, "log", "--format=%s", "main", "--", README_PATH).splitlines()
    assert log == ["Сімейний кодекс України: перша редакція"]
    # a branch of a law does not touch README.md
    branch = git(repo, "diff", "--name-only", "bill/law/407-15^", "bill/law/407-15").split()
    assert README_PATH not in branch


def test_add_readme_empty_plan_and_linear():
    add_readme([], [FAMILY])  # nothing to attach to: no error
    steps = plan_linear([CodeSource(FAMILY, {"2947-14": [ed(date(2002, 1, 10))]})], load)
    add_readme(steps, [FAMILY])
    assert README_PATH in steps[0].changes.modified
