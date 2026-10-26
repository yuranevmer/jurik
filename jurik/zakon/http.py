"""Cached, rate-limited client for document cards and editions.

Source: the official «Законодавство України» API on data.rada.gov.ua (requires
``User-Agent: OpenData``). Card = JSON (``/laws/card/<nreg>.json``), edition = clean HTML
(``/laws/show/<nreg>/ed<YYYYMMDD>``).

Responses are cached gzip-compressed as ``<cache>/zakon/<safe_id>/card.json.gz`` and
``<cache>/zakon/<safe_id>/ed<YYYYMMDD>.html.gz``. Error pages and invalid card JSON are
never cached.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from jurik.errors import JurikError
from jurik.http import BASE_URL, OpenDataClient, write_atomic

CARD_FILE = "card.json.gz"

ERROR_MARKERS = (
    "Документ не знайдено",
    "Сторінку не знайдено",
    "g-recaptcha",
    "captcha",
    "Доступ заборонено",
)


class ZakonError(JurikError):
    """Request failed, returned an error page (never cached), or a cached file is unreadable."""


class NotCachedError(ZakonError):
    """A card or edition read from the cache only has not been downloaded yet."""


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


class ZakonClient(OpenDataClient):
    def __init__(
        self,
        cache_dir: Path,
        *,
        refetch: bool = False,
        min_interval: float = 2.5,
        retries: int = 3,
        backoff: float = 2.0,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            Path(cache_dir) / "zakon",
            refetch=refetch,
            error=ZakonError,
            min_interval=min_interval,
            retries=retries,
            backoff=backoff,
            timeout=timeout,
            transport=transport,
            sleep=sleep,
            clock=clock,
        )

    # --- urls and cache paths -------------------------------------------------

    def card_url(self, zakon_id: str) -> str:
        # the slash in ids like 254к/96-вр must stay literal (%2F gives 404)
        return f"{BASE_URL}/laws/card/{zakon_id}.json"

    def edition_url(self, zakon_id: str, ed_date: str) -> str:
        return f"{BASE_URL}/laws/show/{zakon_id}/ed{ed_date}"

    def card_path(self, zakon_id: str) -> Path:
        return self.cache_dir / safe_id(zakon_id) / CARD_FILE

    def edition_path(self, zakon_id: str, ed_date: str) -> Path:
        return self.cache_dir / safe_id(zakon_id) / f"ed{ed_date}.html.gz"

    # --- cached accessors ------------------------------------------------------

    def card(self, zakon_id: str, *, refresh: bool = False) -> str:
        """Card JSON; ``refresh=True`` re-downloads it even when cached.

        The cached card is replaced only by a valid JSON response.
        """
        path = self.card_path(zakon_id)
        if not refresh and self.is_cached(path):
            return read_gz(path)
        url = self.card_url(zakon_id)
        text = self.get(url)
        try:
            json.loads(text, strict=False)
        except ValueError as e:
            raise ZakonError(f"{url}: invalid JSON: {e}") from e
        write_gz(path, text)
        return text

    def edition(self, zakon_id: str, ed_date: str) -> str:
        path = self.edition_path(zakon_id, ed_date)
        if self.is_cached(path):
            return read_gz(path)
        text = self.get(self.edition_url(zakon_id, ed_date))
        write_gz(path, text)
        return text

    def cached_edition(self, zakon_id: str, ed_date: str) -> str:
        """Edition html from the cache only (never downloads): ``NotCachedError`` if it is
        not downloaded yet, ``ZakonError`` if the file is unreadable."""
        return read_cached(self.edition_path(zakon_id, ed_date), f"{zakon_id} ed{ed_date}")

    def is_cached(self, path: Path) -> bool:
        return path.is_file() and not self.refetch

    # --- network ---------------------------------------------------------------

    def get(self, url: str) -> str:
        """GET with rate limit and retries on 5xx/timeouts; error pages raise ZakonError."""
        text = self._http.get(url).text
        check_error_page(text, url)
        return text


def write_gz(path: Path, text: str) -> None:
    write_atomic(path, gzip.compress(text.encode("utf-8"), mtime=0))


def read_gz(path: Path) -> str:
    """Text of a gzip cache file; ``ZakonError`` if it is missing, truncated or not UTF-8."""
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as e:
        raise ZakonError(f"unreadable cache {path}: {e}") from e


def read_cached(path: Path, what: str) -> str:
    """``read_gz`` of a file that must already be cached: ``NotCachedError`` if it is not."""
    if not path.is_file():
        raise NotCachedError(f"{what} not cached ({path}), run `jurik fetch`")
    return read_gz(path)
