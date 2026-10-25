"""Cached, rate-limited downloader of Rada open data files (data.rada.gov.ua/ogd/).

Files per convocation (see ``docs/spike-sources.md``):

- bills, III–VIII: ``/ogd/zpr/skl<n>/bills_{main,acts,initiators,passings,documents}-skl<n>.csv``
  (closed datasets, complete);
- bills, IX: ``/ogd/zpr/skl9/billinfo-skl9.zip`` (one JSON, updated daily; the IX CSVs stopped
  updating in January 2025);
- plenary votes, III–IX: ``/ogd/zal/ppz/skl<n>/plenary_{agenda,event_question,result_event}
  -skl<n>.csv``.

Everything is cached under ``<cache>/rada/skl<n>/`` (CSV gzip-compressed as ``<name>.gz``,
the zip as is). Present files are never re-downloaded unless ``refetch=True``; error pages
and truncated files are never cached.
"""

from __future__ import annotations

import gzip
import io
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

BASE_URL = "https://data.rada.gov.ua"
USER_AGENT = "OpenData"
CONVOCATIONS = range(3, 10)
CSV_BILLS = ("main", "acts", "initiators", "passings", "documents")
CSV_PLENARY = ("agenda", "event_question", "result_event")
BILLINFO = "billinfo-skl9.zip"


class RadaError(RuntimeError):
    """Download failed, returned an error page, or a cached file is unreadable."""


def convocation_files(n: int) -> list[tuple[str, str]]:
    """``(file name, url)`` of every file needed for convocation ``n``."""
    if n not in CONVOCATIONS:
        raise RadaError(f"convocation {n} is outside III–IX (3..9)")
    files: list[tuple[str, str]] = []
    if n == 9:
        files.append((BILLINFO, f"{BASE_URL}/ogd/zpr/skl9/{BILLINFO}"))
    else:
        for kind in CSV_BILLS:
            name = f"bills_{kind}-skl{n}.csv"
            files.append((name, f"{BASE_URL}/ogd/zpr/skl{n}/{name}"))
    for kind in CSV_PLENARY:
        name = f"plenary_{kind}-skl{n}.csv"
        files.append((name, f"{BASE_URL}/ogd/zal/ppz/skl{n}/{name}"))
    return files


def cache_name(name: str) -> str:
    return name if name.endswith(".zip") else name + ".gz"


def check_payload(name: str, data: bytes, url: str) -> None:
    """Reject HTML error pages and broken archives before they reach the cache."""
    if not data.strip():
        raise RadaError(f"{url}: empty response")
    if name.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if not zf.namelist():
                    raise RadaError(f"{url}: empty zip")
                bad = zf.testzip()
        except zipfile.BadZipFile as e:
            raise RadaError(f"{url}: not a zip archive ({e})") from e
        if bad:
            raise RadaError(f"{url}: corrupt member {bad!r} in zip")
        return
    head = data[:512].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml", b"<")):
        raise RadaError(f"{url}: got an HTML page instead of CSV")
    if b"," not in data.split(b"\n", 1)[0]:
        raise RadaError(f"{url}: first line is not a CSV header")


class RadaClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        refetch: bool = False,
        min_interval: float = 2.5,
        retries: int = 3,
        backoff: float = 5.0,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_dir = Path(cache_dir) / "rada"
        self.refetch = refetch
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RadaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def path(self, n: int, name: str) -> Path:
        return self.cache_dir / f"skl{n}" / cache_name(name)

    def fetch_convocation(
        self, n: int, *, echo: Callable[[str], None] | None = None
    ) -> list[Path]:
        """Download all files of convocation ``n`` into the cache; return cached paths."""
        paths = []
        for name, url in convocation_files(n):
            path = self.path(n, name)
            if path.is_file() and not self.refetch:
                paths.append(path)
                continue
            if echo:
                echo(f"  download {url}")
            data = self.get(url)
            check_payload(name, data, url)
            write_cached(path, data)
            paths.append(path)
        return paths

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get(self, url: str) -> bytes:
        """GET with rate limit and retries on 5xx/timeouts/transport errors."""
        last_error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * 2 ** (attempt - 1))
            self._throttle()
            try:
                resp = self._http.get(url)
            except httpx.TimeoutException as e:
                last_error = f"timeout: {e}"
                continue
            except httpx.TransportError as e:
                last_error = f"transport error: {e}"
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                continue
            if resp.status_code != 200:
                raise RadaError(f"{url}: HTTP {resp.status_code}")
            return resp.content
        raise RadaError(f"{url}: failed after {self.retries + 1} attempts ({last_error})")


def write_cached(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data if path.suffix == ".zip" else gzip.compress(data, mtime=0))
    tmp.replace(path)


def read_cached(path: Path) -> bytes:
    """Raw bytes of a cached file (gunzipped CSV, or the zip as is)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise RadaError(f"{path}: {e}") from e
    if path.suffix == ".zip":
        return raw
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError) as e:
        raise RadaError(f"{path}: corrupt gzip ({e})") from e
