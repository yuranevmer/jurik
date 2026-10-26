"""Inputs of a build read from the cache only: in-force editions of the codes, edition
trees and the print html that the text invariant compares against."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from jurik.build.history import BuildError, CodeSource, TreeLoader
from jurik.config import Code
from jurik.errors import JurikError
from jurik.model import DocTree, Edition
from jurik.zakon.card import CardParseError
from jurik.zakon.fetch import cached_card
from jurik.zakon.http import ZakonClient, ZakonError
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree


def sources_from_cache(
    codes: Iterable[Code], client: ZakonClient, *, today: date | None = None
) -> list[CodeSource]:
    """In-force editions of every document of ``codes`` from cached cards; every edition
    must be cached too (the history must be complete), else ``BuildError``."""
    sources: list[CodeSource] = []
    for code in codes:
        src = CodeSource(code, {})
        for zakon_id in code.zakon_ids:
            try:
                card = cached_card(client, zakon_id, today=today)
            except (ZakonError, CardParseError) as e:
                raise BuildError(f"{code.slug}: {e}") from e
            current = card.current()
            missing = [
                f"ed{e.stamp}"
                for e in current
                if not client.edition_path(zakon_id, e.stamp).is_file()
            ]
            if missing:
                raise BuildError(
                    f"{code.slug} {zakon_id}: {len(missing)} edition(s) not cached "
                    f"({', '.join(missing[:5])}), run `jurik fetch --code {code.slug}`"
                )
            src.editions[zakon_id] = current
            src.adopted = src.adopted or card.adopted
            if card.in_force and current and card.in_force > current[0].date:
                src.in_force[zakon_id] = card.in_force
        sources.append(src)
    return sources


def cached_tree_loader(client: ZakonClient) -> TreeLoader:
    """Parse a cached edition into a ``DocTree``."""

    def load(ed: Edition) -> DocTree:
        try:
            return build_tree(extract_paragraphs(client.cached_edition(ed.zakon_id, ed.stamp)), ed)
        except JurikError as e:
            raise BuildError(f"{ed.zakon_id} ed{ed.stamp}: {e}") from e

    return load


def latest_prints(sources: list[CodeSource], client: ZakonClient) -> dict[str, list[str]]:
    """Print html of the latest edition of each document of every code still in force."""
    out: dict[str, list[str]] = {}
    for src in sources:
        if src.code.repealed:
            continue
        htmls = []
        for zakon_id in src.code.zakon_ids:
            if eds := src.editions.get(zakon_id):
                htmls.append(client.cached_edition(zakon_id, eds[-1].stamp))
        out[src.code.slug] = htmls
    return out
