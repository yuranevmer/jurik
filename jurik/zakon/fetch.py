"""Download cards and all in-force editions of a code into the cache."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

from jurik.config import Code
from jurik.model import Edition
from jurik.zakon.card import CardInfo, CardParseError, base_kind, parse_card
from jurik.zakon.http import ZakonClient, ZakonError, read_cached, read_gz


@dataclass
class FetchFailure:
    """A card or edition that could not be downloaded (collected, never raised)."""

    zakon_id: str
    edition: str  # "YYYYMMDD" or "card"
    message: str


@dataclass
class FetchResult:
    slug: str
    downloaded: int = 0
    cached: int = 0
    future: int = 0
    errors: list[FetchFailure] = field(default_factory=list)

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

    The card is always re-downloaded (it is small and lists new editions); if that fails
    but a card is cached, the cached one is used with a warning. Editions already in the
    cache are skipped unless the client was created with ``refetch=True``.

    Errors of a single card or edition are collected into the result and never stop the
    remaining downloads.
    """
    result = FetchResult(code.slug)
    for zakon_id in code.zakon_ids:
        try:
            card = parse_card(_card(client, zakon_id, code.slug, echo), zakon_id, today=today)
        except (ZakonError, CardParseError) as e:
            result.errors.append(FetchFailure(zakon_id, "card", str(e)))
            echo(f"{code.slug} {zakon_id}: card failed: {e}")
            continue
        editions = card.current()
        result.future += len(card.editions) - len(editions)
        total = len(editions)
        for i, ed in enumerate(editions, 1):
            ed_date = ed.stamp
            if client.is_cached(client.edition_path(zakon_id, ed_date)):
                result.cached += 1
                continue
            try:
                client.edition(zakon_id, ed_date)
            except ZakonError as e:
                result.errors.append(FetchFailure(zakon_id, ed_date, str(e)))
                echo(f"{code.slug} {zakon_id} ed{ed_date} [{i}/{total}]: failed: {e}")
                continue
            result.downloaded += 1
            echo(f"{code.slug} {zakon_id} ed{ed_date} [{i}/{total}]")
    echo(result.summary())
    return result


def _card(client: ZakonClient, zakon_id: str, slug: str, echo: Callable[[str], None]) -> str:
    """Refreshed card JSON, falling back to the cached card if the refresh fails."""
    try:
        return client.card(zakon_id, refresh=True)
    except ZakonError as e:
        path = client.card_path(zakon_id)
        if not path.is_file():
            raise
        echo(f"{slug} {zakon_id}: warning: card refresh failed, using cached card: {e}")
        return read_gz(path)


def cached_card(client: ZakonClient, zakon_id: str, *, today: date | None = None) -> CardInfo:
    """The cached card of ``zakon_id`` (never downloads): ``NotCachedError`` if it is not
    cached, ``ZakonError`` if unreadable, ``CardParseError`` if it does not parse."""
    text = read_cached(client.card_path(zakon_id), f"card {zakon_id}")
    return parse_card(text, zakon_id, today=today)


def cached_cards(code: Code, client: ZakonClient) -> dict[str, CardInfo] | None:
    """Cached cards of every document of ``code``; None if one is missing or broken."""
    try:
        return {z: cached_card(client, z) for z in code.zakon_ids}
    except (ZakonError, CardParseError):
        return None


def cached_editions(code: Code, client: ZakonClient) -> list[Edition] | None:
    """In-force editions of every document of ``code`` from cached cards; None if a card
    is not cached or cannot be parsed."""
    cards = cached_cards(code, client)
    if cards is None:
        return None
    return [e for card in cards.values() for e in card.current()]


def cached_code_bases(codes: Iterable[Code], client: ZakonClient) -> dict[str, list[str]]:
    """Law bases of every edition of each code whose card is already cached (no network)."""
    out: dict[str, list[str]] = {}
    for code in codes:
        cards = cached_cards(code, client)
        if cards is not None:
            out[code.slug] = [
                b
                for card in cards.values()
                for ed in card.editions
                for b in ed.bases
                if base_kind(b) == "law"
            ]
    return out
