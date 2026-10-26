"""Write a ``git fast-import`` stream and run it into a fresh repository.

Blobs are deduplicated by sha256 of their content: each unique file is written once and
later commits refer to its mark. Commits carry only the ``M``/``D`` lines of their change
set, so ``git diff commit^ commit`` shows exactly that change set.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jurik.build.changeset import ChangeSet
from jurik.build.gitcmd import GitError, run_git
from jurik.build.refs import MAIN
from jurik.model import KYIV

COMMIT_HOUR = 12
FILE_MODE = "100644"
# `git config` key that marks a repository created by `jurik build` (safe to recreate)
MARKER_KEY = "jurik.built"


class FastImportError(GitError):
    """``git`` failed while creating the repository, or ``--out`` cannot be used."""


_IDENT_BAD = re.compile(r"[<>\x00-\x1f\x7f]+")


def ident_part(s: str) -> str:
    """Name or e-mail usable in an ident line (``<``, ``>``, newlines and controls dropped)."""
    return " ".join(_IDENT_BAD.sub(" ", s).split())


@dataclass(frozen=True)
class Ident:
    """Author or committer of a commit; the time is 12:00 Kyiv time on ``when``."""

    name: str
    email: str
    when: date

    def git(self) -> str:
        name, email = ident_part(self.name), ident_part(self.email)
        return f"{name} <{email}> {git_timestamp(self.when)}"


def git_timestamp(day: date) -> str:
    """``<epoch> <±HHMM>`` for 12:00 Europe/Kyiv on ``day`` (offset from tzdata, DST aware)."""
    moment = datetime(day.year, day.month, day.day, COMMIT_HOUR, tzinfo=KYIV)
    offset = moment.utcoffset()
    assert offset is not None
    minutes = int(offset.total_seconds()) // 60
    sign = "+" if minutes >= 0 else "-"
    hh, mm = divmod(abs(minutes), 60)
    return f"{int(moment.timestamp())} {sign}{hh:02d}{mm:02d}"


def quote_path(path: str) -> str:
    """Paths are written verbatim unless fast-import needs C-style quoting."""
    if not path or path.startswith('"') or "\n" in path or path != path.strip():
        escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return path


class Stream:
    """A fast-import stream being written to a temporary file (``file``)."""

    def __init__(self) -> None:
        self.file = tempfile.TemporaryFile()  # noqa: SIM115
        self._next_mark = 1
        self._blobs: dict[bytes, int] = {}
        self.blob_count = 0
        self.commit_count = 0

    def _mark(self) -> int:
        mark = self._next_mark
        self._next_mark += 1
        return mark

    def _data(self, payload: bytes) -> None:
        self.file.write(b"data %d\n" % len(payload) + payload + b"\n")

    def blob(self, content: bytes) -> int:
        """Mark of a blob with ``content``; written to the stream only the first time."""
        key = hashlib.sha256(content).digest()
        mark = self._blobs.get(key)
        if mark is None:
            mark = self._blobs[key] = self._mark()
            self.file.write(b"blob\nmark :%d\n" % mark)
            self._data(content)
            self.blob_count += 1
        return mark

    def commit(
        self,
        ref: str,
        parents: Sequence[int],
        author: Ident,
        committer: Ident,
        message: str,
        changeset: ChangeSet,
    ) -> int:
        """Write a commit on ``refs/heads/<ref>``; ``parents[0]`` is ``from``, the rest
        ``merge``. Without parents the commit is a root commit. Returns its mark."""
        files = [(path, self.blob(content)) for path, content in changeset.modified.items()]
        mark = self._mark()
        lines = [
            f"commit refs/heads/{ref}",
            f"mark :{mark}",
            f"author {author.git()}",
            f"committer {committer.git()}",
        ]
        self.file.write(("\n".join(lines) + "\n").encode("utf-8"))
        self._data(message.encode("utf-8"))
        ops = [f"from :{parents[0]}"] if parents else []
        ops += [f"merge :{p}" for p in parents[1:]]
        ops += [f"D {quote_path(p)}" for p in sorted(changeset.deleted)]
        ops += [f"M {FILE_MODE} :{m} {quote_path(p)}" for p, m in sorted(files)]
        self.file.write(("\n".join(ops) + "\n\n" if ops else "\n").encode("utf-8"))
        self.commit_count += 1
        return mark


def _is_jurik_repo(path: Path) -> bool:
    try:
        out = run_git(path, "config", "--local", "--get", MARKER_KEY, error=FastImportError)
    except FastImportError:
        return False
    return out.strip() == b"true"


def prepare_repo(repo_path: Path) -> None:
    """Recreate ``repo_path`` as an empty repository with ``main`` as HEAD.

    An existing directory is removed only if it is empty or a repository made by a previous
    ``jurik build`` (``jurik.built`` in its ``.git/config``), and never when it is the
    current directory or one of its parents: a typo in ``--out`` cannot wipe unrelated
    files or another git checkout.
    """
    resolved = repo_path.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or resolved in cwd.parents:
        raise FastImportError(f"{repo_path} is the current directory or its parent")
    if repo_path.exists():
        if not repo_path.is_dir():
            raise FastImportError(f"{repo_path} exists and is not a directory")
        if any(repo_path.iterdir()) and not (
            (repo_path / ".git").is_dir() and _is_jurik_repo(repo_path)
        ):
            raise FastImportError(
                f"{repo_path} is not empty and is not a repository built by jurik "
                "(remove it by hand to rebuild there)"
            )
        shutil.rmtree(repo_path)
    repo_path.mkdir(parents=True)
    run_git(repo_path, "init", "-q", "-b", MAIN, error=FastImportError)
    run_git(repo_path, "config", MARKER_KEY, "true", error=FastImportError)


def run(stream: Stream, repo_path: Path) -> None:
    """Create a fresh repository at ``repo_path``, import ``stream`` and check out ``main``."""
    prepare_repo(repo_path)
    out = stream.file
    out.flush()
    out.seek(0)
    run_git(repo_path, "fast-import", "--quiet", stdin=out, error=FastImportError)
    if run_git(repo_path, "for-each-ref", f"refs/heads/{MAIN}", error=FastImportError).strip():
        run_git(repo_path, "reset", "-q", "--hard", MAIN, error=FastImportError)
