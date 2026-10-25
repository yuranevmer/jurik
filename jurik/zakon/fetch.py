"""Download cards and all in-force editions of a code into the cache."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from jurik.config import Code
from jurik.zakon.card import CardParseError, parse_card
from jurik.zakon.http import ZakonClient, ZakonError


@dataclass
class FetchError:
    zakon_id: str
    edition: str  # "YYYYMMDD" or "card"
    message: str


@dataclass
class FetchResult:
    slug: str
    downloaded: int = 0
    cached: int = 0
    future: int = 0
    errors: list[FetchError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return (
            f"{self.slug}: {self.downloaded} downloaded, {self.cached} cached, "
            f"{self.future} future skipped, {len(self.errors)} errors"
        )


def fetch_code(
    code: Code,
    client: ZakonClient,
    *,
    echo: Callable[[str], None] = print,
    today: date | None = None,
) -> FetchResult:
    """Fetch the card and every non-future edition of every zakon_id of ``code``.

    Errors of a single card or edition are collected into the result and never stop the
    remaining downloads.
    """
    result = FetchResult(code.slug)
    for zakon_id in code.zakon_ids:
        try:
            card = parse_card(client.card(zakon_id), zakon_id, today=today)
        except (ZakonError, CardParseError) as e:
            result.errors.append(FetchError(zakon_id, "card", str(e)))
            echo(f"{code.slug} {zakon_id}: card failed: {e}")
            continue
        editions = card.current()
        result.future += len(card.editions) - len(editions)
        total = len(editions)
        for i, ed in enumerate(editions, 1):
            ed_date = ed.date.strftime("%Y%m%d")
            if client.is_cached(client.edition_path(zakon_id, ed_date)):
                result.cached += 1
                continue
            try:
                client.edition(zakon_id, ed_date)
            except ZakonError as e:
                result.errors.append(FetchError(zakon_id, ed_date, str(e)))
                echo(f"{code.slug} {zakon_id} ed{ed_date} [{i}/{total}]: failed: {e}")
                continue
            result.downloaded += 1
            echo(f"{code.slug} {zakon_id} ed{ed_date} [{i}/{total}]")
    echo(result.summary())
    return result
