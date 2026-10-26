"""Shared HTTP plumbing: a throttled, retrying GET client and an atomic file writer.

Both data.rada.gov.ua clients (``jurik.zakon.http.ZakonClient`` for document cards and
editions, ``jurik.rada.client.RadaClient`` for open data files) are built on top of it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Self

import httpx

BASE_URL = "https://data.rada.gov.ua"
# data.rada.gov.ua serves the API and open data only to this User-Agent
OPENDATA_UA = "OpenData"


class ThrottledHttp:
    """``httpx.Client`` with a minimum interval between requests and retries.

    ``get`` retries on 5xx, timeouts and transport errors with exponential backoff
    (``backoff``, ``2 * backoff``, …) and raises ``error`` on any other non-200 status or
    once ``retries`` extra attempts are exhausted. Redirects are not followed (a redirect
    is reported as an error together with its ``Location``).
    """

    def __init__(
        self,
        *,
        user_agent: str,
        error: type[Exception],
        min_interval: float,
        retries: int,
        backoff: float,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.error = error
        self.min_interval = min_interval
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ThrottledHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """Throttled GET returning a 200 response; anything else raises ``self.error``."""
        last_error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * 2 ** (attempt - 1))
            self._throttle()
            try:
                resp = self._http.get(url, headers=headers)
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
                raise self.error(f"{url}: HTTP {resp.status_code}{extra}")
            return resp
        raise self.error(f"{url}: failed after {self.retries + 1} attempts ({last_error})")


class OpenDataClient:
    """Base of the data.rada.gov.ua clients: a cache directory and a ``ThrottledHttp`` with
    the OpenData User-Agent that raises ``error``; usable as a context manager."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        refetch: bool,
        error: type[Exception],
        min_interval: float,
        retries: int,
        backoff: float,
        timeout: float,
        transport: httpx.BaseTransport | None,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.cache_dir = cache_dir
        self.refetch = refetch
        self._http = ThrottledHttp(
            user_agent=OPENDATA_UA,
            error=error,
            min_interval=min_interval,
            retries=retries,
            backoff=backoff,
            timeout=timeout,
            transport=transport,
            sleep=sleep,
            clock=clock,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a sibling ``.tmp`` file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
