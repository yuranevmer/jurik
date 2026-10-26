from datetime import date

import pytest
from test_export import make_repo

from jurik.build.gitcmd import GitError
from jurik.build.repo import MainCommit, RepoError, git, in_force_of, main_log


def test_main_log_first_parent_oldest_first(tmp_path):
    repo = make_repo(tmp_path)
    commits = main_log(repo)
    assert [c.subject for c in commits] == ["init", "A", "B"]
    assert [len(c.parents) for c in commits] == [0, 2, 1]  # A merges a bill branch
    assert [in_force_of(c) for c in commits] == [
        date(2002, 1, 10),
        date(2010, 1, 1),
        date(2015, 6, 1),
    ]
    assert ("Edition", "2947-14/ed20020110") in commits[0].trailers
    assert git(repo, "rev-parse", "main").decode().strip() == commits[-1].sha


def test_in_force_of_missing_or_bad():
    assert in_force_of(MainCommit("x", [], [], "s")) is None
    assert in_force_of(MainCommit("x", [], [("In-force", "2010-13-01")], "s")) is None


def test_unreadable_repo_is_repo_error(tmp_path):
    with pytest.raises(RepoError, match="git log") as info:
        main_log(tmp_path)
    assert isinstance(info.value, GitError)
