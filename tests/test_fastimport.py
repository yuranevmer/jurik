import subprocess
from datetime import date
from pathlib import Path

import pytest
from gitrepo import git, tree_files

from jurik.build.changeset import ChangeSet
from jurik.build.fastimport import (
    FastImportError,
    Ident,
    Stream,
    git_timestamp,
    prepare_repo,
    quote_path,
    run,
)

RADA = ("Верховна Рада України", "rada@laws.invalid")


def ident(d):
    return Ident(*RADA, d)


def test_timestamp_noon_kyiv_with_dst():
    assert git_timestamp(date(2011, 1, 1)) == "1293876000 +0200"
    assert git_timestamp(date(2011, 7, 1)).endswith(" +0300")


def test_quote_path():
    assert quote_path("codes/a/art-0001.md") == "codes/a/art-0001.md"
    assert quote_path('"x') == '"\\"x"'


def test_three_commits_trees_dates_and_trailers(tmp_path):
    stream = Stream()
    days = [date(2002, 1, 10), date(2003, 1, 1), date(2005, 6, 25)]
    changes = [
        ChangeSet(modified={"codes/f/art-0001.md": b"one\n", "codes/f/art-0002.md": b"two\n"}),
        ChangeSet(modified={"codes/f/art-0002.md": b"two v2\n"}),
        ChangeSet(deleted={"codes/f/art-0001.md"}, modified={"codes/f/art-0003.md": b"3\n"}),
    ]
    head = None
    for d, cs in zip(days, changes, strict=True):
        msg = f"step {d}\n\nIn-force: {d.isoformat()}\nReason: law\n"
        head = stream.commit("main", [head] if head else [], ident(d), ident(d), msg, cs)
    repo = tmp_path / "laws"
    run(stream, repo)

    log = git(
        repo,
        "log",
        "--format=%ad|%an|%ae|%(trailers:key=In-force,valueonly,separator=%x2C)",
        "--date=iso",
    )
    lines = [line for line in log.splitlines() if line]
    assert lines == [
        "2005-06-25 12:00:00 +0300|Верховна Рада України|rada@laws.invalid|2005-06-25",
        "2003-01-01 12:00:00 +0200|Верховна Рада України|rada@laws.invalid|2003-01-01",
        "2002-01-10 12:00:00 +0200|Верховна Рада України|rada@laws.invalid|2002-01-10",
    ]
    stat = git(repo, "show", "--stat", "--format=", "HEAD~1")
    assert "art-0002.md" in stat and "art-0001.md" not in stat
    assert tree_files(repo) == {"codes/f/art-0002.md": b"two v2\n", "codes/f/art-0003.md": b"3\n"}
    # working tree is checked out
    assert (repo / "codes/f/art-0003.md").read_bytes() == b"3\n"
    assert git(repo, "status", "--porcelain") == ""


def test_blob_written_once_for_same_content():
    stream = Stream()
    a = stream.blob(b"same")
    b = stream.blob(b"same")
    c = stream.blob(b"other")
    assert a == b != c
    assert stream.blob_count == 2
    stream.commit(
        "main",
        [],
        ident(date(2020, 1, 1)),
        ident(date(2020, 1, 1)),
        "m\n",
        ChangeSet(modified={"x.md": b"same", "y.md": b"same"}),
    )
    stream.file.seek(0)
    assert stream.file.read().count(b"data 4\nsame") == 1


def test_empty_changeset_still_makes_commit(tmp_path):
    stream = Stream()
    d = date(2020, 1, 1)
    first = stream.commit("main", [], ident(d), ident(d), "a\n", ChangeSet({"a": b"1"}))
    stream.commit("main", [first], ident(d), ident(d), "b\n", ChangeSet())
    run(stream, tmp_path / "r")
    assert git(tmp_path / "r", "rev-list", "--count", "main").strip() == "2"


def test_fast_import_failure_raises_with_stderr(tmp_path):
    stream = Stream()
    stream.file.write(b"this is not a fast-import command\n")
    with pytest.raises(FastImportError, match="fast-import.*(Unsupported|unsupported)"):
        run(stream, tmp_path / "r")


def test_prepare_repo_refuses_foreign_directory(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("keep me")
    with pytest.raises(FastImportError, match="not a repository built by jurik"):
        prepare_repo(out)
    assert (out / "notes.txt").exists()


def test_prepare_repo_refuses_foreign_git_checkout(tmp_path):
    out = tmp_path / "project"
    out.mkdir()
    subprocess.run(["git", "init", "-q", str(out)], check=True)
    (out / "src.py").write_text("keep me")
    with pytest.raises(FastImportError, match="not a repository built by jurik"):
        prepare_repo(out)
    assert (out / "src.py").exists()


def test_prepare_repo_refuses_current_directory_and_parents(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    for target in (Path("."), work, tmp_path):
        with pytest.raises(FastImportError, match="current directory"):
            prepare_repo(target)
    assert work.exists()


def test_built_repo_is_marked_and_recreatable(tmp_path):
    repo = tmp_path / "r"
    prepare_repo(repo)
    assert git(repo, "config", "--get", "jurik.built").strip() == "true"
    (repo / "junk").write_text("x")
    prepare_repo(repo)
    assert not (repo / "junk").exists()


def test_ident_strips_angle_brackets_and_newlines():
    who = Ident("Іван <Петренко>\nX", "a<b>@x\n", date(2020, 1, 1))
    line = who.git()
    assert line.startswith("Іван Петренко X <a b @x> ")
    assert "\n" not in line


def test_rebuild_replaces_previous_repo(tmp_path):
    repo = tmp_path / "r"
    d = date(2020, 1, 1)
    for content in (b"1", b"2"):
        stream = Stream()
        stream.commit("main", [], ident(d), ident(d), "m\n", ChangeSet({"a": content}))
        run(stream, repo)
    assert git(repo, "rev-list", "--count", "main").strip() == "1"
    assert (repo / "a").read_bytes() == b"2"
