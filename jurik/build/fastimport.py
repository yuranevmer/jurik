"""Write a ``git fast-import`` stream and run it into a fresh repository.

Blobs are deduplicated by sha256 of their content: each unique file is written once and
later commits refer to its mark. Commits carry only the ``M``/``D`` lines of their change
set, so ``git diff commit^ commit`` shows exactly that change set.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from jurik.build.changeset import ChangeSet

KYIV = ZoneInfo("Europe/Kyiv")
COMMIT_HOUR = 12
FILE_MODE = "100644"


class FastImportError(RuntimeError):
    """``git`` failed while creating the repository."""


@dataclass(frozen=True)
class Ident:
    """Author or committer of a commit; the time is 12:00 Kyiv time on ``when``."""

    name: str
    email: str
    when: date

    def git(self) -> str:
        return f"{self.name} <{self.email}> {git_timestamp(self.when)}"


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
    """A fast-import stream being written to a temporary file (or ``out``)."""

    def __init__(self, out: BinaryIO | None = None) -> None:
        self._out = out if out is not None else tempfile.TemporaryFile()  # noqa: SIM115
        self._next_mark = 1
        self._blobs: dict[bytes, int] = {}
        self.blob_count = 0
        self.commit_count = 0

    @property
    def file(self) -> BinaryIO:
        return self._out

    def _mark(self) -> int:
        mark = self._next_mark
        self._next_mark += 1
        return mark

    def _write(self, data: bytes) -> None:
        self._out.write(data)

    def _data(self, payload: bytes) -> None:
        self._write(b"data %d\n" % len(payload) + payload + b"\n")

    def blob(self, content: bytes) -> int:
        """Mark of a blob with ``content``; written to the stream only the first time."""
        key = hashlib.sha256(content).digest()
        mark = self._blobs.get(key)
        if mark is None:
            mark = self._blobs[key] = self._mark()
            self._write(b"blob\nmark :%d\n" % mark)
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
        self._write(("\n".join(lines) + "\n").encode("utf-8"))
        self._data(message.encode("utf-8"))
        ops = [f"from :{parents[0]}"] if parents else []
        ops += [f"merge :{p}" for p in parents[1:]]
        ops += [f"D {quote_path(p)}" for p in sorted(changeset.deleted)]
        ops += [f"M {FILE_MODE} :{m} {quote_path(p)}" for p, m in sorted(files)]
        self._write(("\n".join(ops) + "\n\n" if ops else "\n").encode("utf-8"))
        self.commit_count += 1
        return mark


def _git(repo: Path, *args: str, stdin: BinaryIO | None = None) -> str:
    data: bytes | None = None
    if stdin is not None:
        try:
            stdin.fileno()
        except (OSError, ValueError):  # in-memory stream (io.BytesIO)
            data, stdin = stdin.read(), None
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=stdin,
        input=data,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise FastImportError(f"git {' '.join(args)} failed ({proc.returncode}): {err}")
    return proc.stdout.decode("utf-8", "replace")


def prepare_repo(repo_path: Path) -> None:
    """Recreate ``repo_path`` as an empty repository with ``main`` as HEAD.

    An existing directory is removed only if it is empty or a git repository, so a typo in
    ``--out`` cannot wipe unrelated files.
    """
    if repo_path.exists():
        if not repo_path.is_dir():
            raise FastImportError(f"{repo_path} exists and is not a directory")
        if any(repo_path.iterdir()) and not (repo_path / ".git").exists():
            raise FastImportError(f"{repo_path} is not empty and not a git repository")
        shutil.rmtree(repo_path)
    repo_path.mkdir(parents=True)
    _git(repo_path, "init", "-q", "-b", "main")


def run(stream: Stream, repo_path: Path) -> None:
    """Create a fresh repository at ``repo_path``, import ``stream`` and check out ``main``."""
    prepare_repo(repo_path)
    out = stream.file
    out.flush()
    out.seek(0)
    _git(repo_path, "fast-import", "--quiet", stdin=out)
    if _git(repo_path, "for-each-ref", "refs/heads/main").strip():
        _git(repo_path, "reset", "-q", "--hard", "main")
