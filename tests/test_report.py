from datetime import date

from click.testing import CliRunner
from test_export import make_repo

from jurik import cli
from jurik.config import Code
from jurik.export import BillBranch
from jurik.model import BillRef, Edition, LawMeta
from jurik.parse import EditionCheck
from jurik.rada.index import LawIndex
from jurik.report import (
    CodeCoverage,
    RepoStats,
    branches_by_code,
    code_coverage,
    coverage_markdown,
    repo_stats,
)
from jurik.verify import VerifyResult
from jurik.zakon.card import edition_kind

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України", (r"сімейн\w* кодекс",))
TAX = Code("tax", ("2755-17",), "Податковий кодекс України", (r"податков\w* кодекс",))


def ed(d, *bases):
    return Edition("2947-14", d, list(bases), edition_kind(list(bases)), "https://x")


def meta(nreg, bill, votes, status="adopted", title="Про зміни"):
    return LawMeta(nreg, title, bill, [], None, None, votes, None, status, "")


def check(edition, ok=True, error="TreeError: x"):
    result = VerifyResult(words=1, files=1) if ok else None
    return EditionCheck("family", "2947-14", edition, result, "" if ok else error)


EDITIONS = [
    ed(date(2002, 1, 1)),  # initial, no bases
    ed(date(2003, 1, 1), "100-15"),  # bill + vote
    ed(date(2004, 1, 1), "200-15"),  # bill, no vote
    ed(date(2005, 1, 1), "300-15", "100-15"),  # 300-15 not in index; 100-15 has a vote
    ed(date(2006, 1, 1), "v001p710-07"),  # court
    ed(date(2007, 1, 1), "400-15"),  # law without a bill
]
INDEX = LawIndex(
    by_law={
        "100-15": meta("100-15", BillRef(4, "1"), (300, 0, 0)),
        "200-15": meta("200-15", BillRef(4, "2"), None),
        "400-15": meta("400-15", None, None),
    },
    bills={
        BillRef(4, "9"): meta(
            "", BillRef(4, "9"), None, "rejected", "Про зміни до Сімейного кодексу"
        ),
        BillRef(4, "8"): meta("", BillRef(4, "8"), None, "pending", "Про сімейний кодекс"),
        BillRef(4, "7"): meta("", BillRef(4, "7"), None, "rejected", "Про податковий кодекс"),
    },
)


def test_code_coverage_numbers():
    checks = [check(f"{e.date:%Y%m%d}", ok=i not in (2, 4)) for i, e in enumerate(EDITIONS)]
    checks[4].error = "NotCachedError: 1-01 ed20060101 not cached"
    checks[4].not_cached = True
    row = code_coverage(FAMILY, EDITIONS, checks, INDEX, branches=1)
    assert row == CodeCoverage(
        slug="family",
        title="Сімейний кодекс України",
        editions=6,
        cached=5,
        parsed=4,
        with_bill=3,  # 2003, 2004, 2005 (via 100-15)
        with_votes=2,  # 2003, 2005
        laws=4,  # 100-15, 200-15, 300-15, 400-15
        laws_without_meta=1,  # 300-15
        branches=1,
        bills_by_title=2,
    )


def test_code_coverage_without_index():
    row = code_coverage(FAMILY, EDITIONS, [], None)
    assert (row.parsed, row.with_bill, row.laws_without_meta, row.bills_by_title) == (
        0,
        None,
        None,
        None,
    )
    assert "| family | 6 | 6 (100%) | 0 (0%) | — | — | 4 | — | — | — |" in coverage_markdown([row])


def test_coverage_markdown_rows_and_totals():
    rows = [
        coverage("family", "С", 10, 10, 9, 8, 7, 5, 1, 2, 3),
        coverage("tax", "П", 30, 30, 30, 20, 10, 15, 0, 4, 5),
    ]
    repo = RepoStats(main_commits=40, bill_branches=12, bills=[bill_branch("")] * 6)
    md = coverage_markdown(rows, repo=repo, open_data=["skl4: x"], missing=["civil"])
    assert "| family | 10 | 10 (100%) | 9 (90%) | 8 (80%) | 7 (70%) | 5 | 1 (20%) | 2 | 3 |" in md
    assert (
        "| **total** | 40 | 40 (100%) | 39 (98%) | 28 (70%) | 17 (42%) | 20 | 1 (5%) | 6 | 8 |"
        in md
    )
    assert "40 commits in `--first-parent main`" in md
    assert "`bill/*` branches: 12 (6 merged, 6 without merge)" in md
    assert "- skl4: x" in md
    assert "Not fetched (no cached card): civil." in md
    assert "is not built" in coverage_markdown([])


def coverage(slug, title, *counts):
    names = [
        "editions",
        "cached",
        "parsed",
        "with_bill",
        "with_votes",
        "laws",
        "laws_without_meta",
        "branches",
        "bills_by_title",
    ]
    return CodeCoverage(slug=slug, title=title, **dict(zip(names, counts, strict=True)))


def bill_branch(msg):
    return BillBranch(
        ref="bill/4/x",
        tip="0",
        convocation=4,
        number="x",
        status="rejected",
        registered=None,
        message=msg,
    )


def test_branches_by_code():
    b = bill_branch
    bills = [
        b("Законопроект\n\nСтатус: відхилено.\nСтосується: Сімейний кодекс України.\n"),
        b("X\n\nСтосується: Сімейний кодекс України, Податковий кодекс України.\n"),
        b("X\n\nСтатус: відхилено.\n"),
    ]
    assert branches_by_code(bills, [FAMILY, TAX]) == {"family": 2, "tax": 1}


def test_repo_stats(tmp_path):
    assert repo_stats(tmp_path / "nothing") is None
    stats = repo_stats(make_repo(tmp_path))
    assert (stats.main_commits, stats.bill_branches, stats.unmerged) == (3, 3, 2)
    assert branches_by_code(stats.bills, [FAMILY]) == {"family": 2}


class FakeZakon:
    def __init__(self, root):
        self.root = root

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def card_path(self, zakon_id):
        return self.root / f"{zakon_id}.json.gz"


def test_cli_report_without_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_zakon_client", lambda cache, **kw: FakeZakon(tmp_path))
    result = CliRunner().invoke(cli.main, ["report", "--code", "family"])
    assert result.exit_code == 0, result.output
    md = (tmp_path / "report" / "coverage.md").read_text()
    assert "Not fetched (no cached card): family." in md
    assert "No law index" in md
    assert (tmp_path / "report" / "parse-failures.md").read_text().startswith("# Parse failures")
