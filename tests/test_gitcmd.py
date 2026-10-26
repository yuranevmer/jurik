import hashlib

import pytest
from gitrepo import git

from jurik.build.gitcmd import GitError, run_git
from jurik.errors import JurikError


class CustomError(GitError):
    pass


def test_run_git_returns_stdout(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    assert run_git(tmp_path, "rev-parse", "--is-inside-work-tree") == b"true\n"
    blob = run_git(tmp_path, "hash-object", "--stdin", input=b"x").strip()
    assert blob.decode() == hashlib.sha1(b"blob 1\x00x").hexdigest()  # input reaches git


def test_run_git_failure_raises_with_stderr(tmp_path):
    with pytest.raises(GitError, match=r"git log failed \(128\): .*not a git repository"):
        run_git(tmp_path, "log")


def test_run_git_failure_raises_given_error_type(tmp_path):
    with pytest.raises(CustomError) as info:
        run_git(tmp_path, "log", error=CustomError)
    assert isinstance(info.value, JurikError)
