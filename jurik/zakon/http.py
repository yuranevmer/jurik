"""Cached, rate-limited HTTP client for document cards and editions.

Two sources serve the same documents:

- ``opendata`` (default): the official «Законодавство України» API on data.rada.gov.ua.
  Card = JSON (``/laws/card/<nreg>.json``), edition = clean HTML
  (``/laws/show/<nreg>/ed<YYYYMMDD>``). Requires ``User-Agent: OpenData``.
- ``zakon``: scraping zakon.rada.gov.ua. Card = HTML (``/laws/card/<id>``), edition = print
  page (``/laws/show/<id>/ed<YYYYMMDD>/print``). Requires a browser User-Agent.

Responses are cached gzip-compressed under ``<cache>/zakon/<safe_id>/``. Error pages are
never cached.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
OPENDATA_UA = "OpenData"

ERROR_MARKERS = (
    "Документ не знайдено",
    "Сторінку не знайдено",
    "g-recaptcha",
    "captcha",
    "Доступ заборонено",
)


class ZakonError(RuntimeError):
    """Request failed or returned an error page (never cached)."""


@dataclass(frozen=True)
class Source:
    name: str
    base_url: str
    user_agent: str
    card_path: str  # format with {id}
    edition_path: str  # format with {id}, {date}
    card_file: str
    edition_file: str  # format with {date}
    card_is_json: bool


OPENDATA = Source(
    name="opendata",
    base_url="https://data.rada.gov.ua",
    user_agent=OPENDATA_UA,
    card_path="/laws/card/{id}.json",
    edition_path="/laws/show/{id}/ed{date}",
    card_file="card.json.gz",
    edition_file="ed{date}.html.gz",
    card_is_json=True,
)

ZAKON = Source(
    name="zakon",
    base_url="https://zakon.rada.gov.ua",
    user_agent=BROWSER_UA,
    card_path="/laws/card/{id}",
    edition_path="/laws/show/{id}/ed{date}/print",
    card_file="card.html.gz",
    edition_file="ed{date}.print.html.gz",
    card_is_json=False,
)

SOURCES = {s.name: s for s in (OPENDATA, ZAKON)}


def safe_id(zakon_id: str) -> str:
    """Filesystem-safe document id: ``254к/96-вр`` → ``254к_96-вр``."""
    return zakon_id.replace("/", "_")


def check_error_page(text: str, url: str) -> None:
    """Raise ZakonError if a 200 response is actually an error/captcha page."""
    if not text.strip():
        raise ZakonError(f"{url}: empty response")
    # error pages are short; real documents can quote these words deep inside the text
    head = text[:20000]
    for marker in ERROR_MARKERS:
        if marker in head and len(text) < 50000:
            raise ZakonError(f"{url}: error page ({marker!r})")


class ZakonClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        source: str | Source = OPENDATA,
        refetch: bool = False,
        min_interval: float = 2.5,
        retries: int = 3,
        backoff: float = 2.0,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = SOURCES[source] if isinstance(source, str) else source
        self.cache_dir = Path(cache_dir) / "zakon"
        self.refetch = refetch
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": self.source.user_agent},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ZakonClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- urls and cache paths -------------------------------------------------

    def card_url(self, zakon_id: str) -> str:
        # the slash in ids like 254к/96-вр must stay literal (%2F gives 404)
        return self.source.base_url + self.source.card_path.format(id=zakon_id)

    def edition_url(self, zakon_id: str, ed_date: str) -> str:
        return self.source.base_url + self.source.edition_path.format(id=zakon_id, date=ed_date)

    def card_path(self, zakon_id: str) -> Path:
        return self.cache_dir / safe_id(zakon_id) / self.source.card_file

    def edition_path(self, zakon_id: str, ed_date: str) -> Path:
        return self.cache_dir / safe_id(zakon_id) / self.source.edition_file.format(date=ed_date)

    # --- cached accessors ------------------------------------------------------

    def card(self, zakon_id: str) -> str:
        return self._cached(
            self.card_url(zakon_id), self.card_path(zakon_id), json_body=self.source.card_is_json
        )

    def edition(self, zakon_id: str, ed_date: str) -> str:
        return self._cached(
            self.edition_url(zakon_id, ed_date), self.edition_path(zakon_id, ed_date)
        )

    def is_cached(self, path: Path) -> bool:
        return path.is_file() and not self.refetch

    def _cached(self, url: str, path: Path, *, json_body: bool = False) -> str:
        if self.is_cached(path):
            return read_gz(path)
        text = self.get(url)
        if json_body:
            try:
                json.loads(text, strict=False)
            except ValueError as e:
                raise ZakonError(f"{url}: invalid JSON: {e}") from e
        write_gz(path, text)
        return text

    # --- network ---------------------------------------------------------------

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get(self, url: str) -> str:
        """GET with rate limit and retries on 5xx/timeouts; error pages raise ZakonError."""
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
                where = resp.headers.get("location")
                extra = f" -> {where}" if where else ""
                raise ZakonError(f"{url}: HTTP {resp.status_code}{extra}")
            text = resp.text
            check_error_page(text, url)
            return text
        raise ZakonError(f"{url}: failed after {self.retries + 1} attempts ({last_error})")


def write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    tmp.replace(path)


def read_gz(path: Path) -> str:
    return gzip.decompress(path.read_bytes()).decode("utf-8")
