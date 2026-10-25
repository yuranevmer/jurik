import json
from datetime import date

import pytest
from click.testing import CliRunner
from gitrepo import git

from jurik.build.changeset import ChangeSet
from jurik.build.fastimport import Ident, Stream, run
from jurik.build.message import format_message, rejected_bill_message
from jurik.cli import main
from jurik.export import ExportError, commit_at, export
from jurik.model import BillRef, LawMeta, Person

RADA = ("Верховна Рада України", "rada@laws.invalid")
KOVAL = Person("Олена Коваль", "koval-olena-petrivna")
ART = "codes/family/01-hlava-1/art-0001.md"


def main_msg(subject, in_force):
    return format_message(subject, [("In-force", in_force.isoformat())])


def bill(number, registered, status):
    return LawMeta(
        "",
        "Про внесення змін до Сімейного кодексу",
        BillRef(4, number),
        [KOVAL],
        registered,
        None,
        None,
        None,
        status,
        card_url=f"https://rada/card/{number}",
    )


def make_repo(tmp_path):
    """main: initial (in force 2002), A (in force 2010, committed 2012), B (in force 2015,
    committed 2011); two unmerged bill branches (one registered after 2014), one merged."""
    s = Stream()

    def ident(d, who=RADA):
        return Ident(*who, d)

    def commit(ref, parents, when, msg, files):
        return s.commit(ref, parents, ident(when), ident(when), msg, ChangeSet(modified=files))

    c0 = commit("main", [], date(2002, 1, 10), main_msg("init", date(2002, 1, 10)), {ART: b"a0\n"})
    br = commit("bill/4/100", [c0], date(2009, 1, 1), "Законопроект № 100\n", {ART: b"a1\n"})
    c1 = commit("main", [c0, br], date(2012, 1, 1), main_msg("A", date(2010, 1, 1)), {ART: b"a1\n"})
    commit("main", [c1], date(2011, 1, 1), main_msg("B", date(2015, 6, 1)), {ART: b"a2\n"})
    for meta in (
        bill("5555-д", date(2004, 2, 1), "rejected"),
        bill("7000", date(2016, 1, 1), "pending"),
    ):
        path = f"bills/4/{'5555-d' if meta.bill.number == '5555-д' else '7000'}.md"
        msg = rejected_bill_message(meta=meta, titles=["Сімейний кодекс України"])
        ref = "bill/4/" + path.rsplit("/", 1)[1].removesuffix(".md")
        commit(
            ref, [c0], meta.registered, msg, {path: f"# Законопроект {meta.bill.number}\n".encode()}
        )
    repo = tmp_path / "laws"
    run(s, repo)
    return repo


def test_commit_at_uses_in_force_not_committer_date(tmp_path):
    repo = make_repo(tmp_path)
    shas = git(repo, "log", "--first-parent", "--reverse", "--format=%H", "main").split()
    assert commit_at(repo, date(2001, 1, 1)) is None
    assert commit_at(repo, date(2002, 1, 10)) == shas[0]
    # B's committer date (2011) is before 2014 but it is in force only from 2015
    assert commit_at(repo, date(2014, 1, 1)) == shas[1]
    assert commit_at(repo, date(2015, 6, 1)) == shas[2]


def test_export_tree_and_manifest(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / "dataset"
    res = export(repo, date(2014, 1, 1), out)
    assert (out / ART).read_text() == "a1\n"
    assert not (out / "bills").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["at"] == "2014-01-01"
    assert manifest["commit"] == res.commit
    assert manifest["in_force"] == "2010-01-01"
    assert manifest["codes"] == ["family"]
    assert "bills" not in manifest
    # re-export over a previous export replaces it
    export(repo, date(2015, 6, 1), out)
    assert (out / ART).read_text() == "a2\n"


def test_export_refuses_foreign_dir_and_early_date(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / "busy"
    out.mkdir()
    (out / "x.txt").write_text("x")
    with pytest.raises(ExportError, match="not empty"):
        export(repo, date(2014, 1, 1), out)
    with pytest.raises(ExportError, match="no commit"):
        export(repo, date(2000, 1, 1), tmp_path / "early")


def test_export_with_bills_adds_banner_with_status(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / "dataset"
    res = export(repo, date(2020, 1, 1), out, with_bills=True)
    assert res.bills == ["bills/4/5555-d.md", "bills/4/7000.md"]  # merged bill/4/100 skipped
    text = (out / "bills/4/5555-d.md").read_text()
    assert text.startswith(
        "> Законопроект № 5555-д (IV скликання), статус: відхилено. Не є чинним законом.\n\n"
        "# Законопроект 5555-д\n"
    )
    pending = (out / "bills/4/7000.md").read_text()
    assert pending.startswith("> Законопроект № 7000 (IV скликання), статус: на розгляді.")
    assert json.loads((out / "manifest.json").read_text())["bills"] == res.bills
    # bills registered after the date are left out
    res = export(repo, date(2014, 1, 1), out, with_bills=True)
    assert res.bills == ["bills/4/5555-d.md"]


def test_cli_export(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = ["export", "--repo", str(repo), "--at", "2014-01-01", "--out", "ds", "--with-bills"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert "1 bill(s)" in result.output
    assert (tmp_path / "ds" / ART).is_file()
    result = CliRunner().invoke(
        main, ["export", "--repo", str(repo), "--at", "1990-01-01", "--out", "x"]
    )
    assert result.exit_code == 1
    assert "no commit" in result.output
