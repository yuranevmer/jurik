"""Code registry (codes.yaml) and project paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PROJECT_ROOT / "codes.yaml"
REQUIRED_FIELDS = ("slug", "zakon_id", "title")


class ConfigError(ValueError):
    """Invalid codes.yaml content."""


@dataclass(frozen=True)
class Code:
    slug: str
    zakon_ids: tuple[str, ...]
    title: str
    title_patterns: tuple[str, ...] = ()
    repealed: date | None = None  # the code lost force on this day (folder is deleted)
    repealed_by: str = ""  # nreg of the repealing law


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def report(self) -> Path:
        return self.root / "report"


def _as_str_list(value: object, slug: str, name: str) -> tuple[str, ...]:
    items = [value] if isinstance(value, str) else value
    if (
        not isinstance(items, list)
        or not items
        or not all(isinstance(x, str) and x.strip() for x in items)
    ):
        raise ConfigError(f"code {slug!r}: {name} must be a non-empty string or list of strings")
    return tuple(x.strip() for x in items)


def parse_codes(data: object) -> list[Code]:
    if not isinstance(data, list):
        raise ConfigError("codes registry must be a list of codes")
    codes: list[Code] = []
    seen_slugs: set[str] = set()
    seen_ids: dict[str, str] = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ConfigError(f"entry #{i}: expected a mapping")
        for name in REQUIRED_FIELDS:
            if entry.get(name) in (None, "", []):
                raise ConfigError(f"entry #{i} ({entry.get('slug', '?')}): missing field {name!r}")
        slug = str(entry["slug"])
        if slug in seen_slugs:
            raise ConfigError(f"duplicate slug {slug!r}")
        seen_slugs.add(slug)
        zakon_ids = _as_str_list(entry["zakon_id"], slug, "zakon_id")
        for zid in zakon_ids:
            if zid in seen_ids:
                raise ConfigError(f"duplicate zakon_id {zid!r} in {seen_ids[zid]!r} and {slug!r}")
            seen_ids[zid] = slug
        patterns = entry.get("title_patterns") or []
        if isinstance(patterns, str):
            patterns = [patterns]
        for pattern in patterns:
            try:
                re.compile(str(pattern))
            except re.error as e:
                msg = f"code {slug!r}: bad title_patterns regex {pattern!r}: {e}"
                raise ConfigError(msg) from e
        repealed = entry.get("repealed")
        if repealed is not None and (
            not isinstance(repealed, date) or isinstance(repealed, datetime)
        ):
            raise ConfigError(f"code {slug!r}: repealed must be a date YYYY-MM-DD")
        codes.append(
            Code(
                slug=slug,
                zakon_ids=zakon_ids,
                title=str(entry["title"]),
                title_patterns=tuple(patterns),
                repealed=repealed,
                repealed_by=str(entry.get("repealed_by") or "").strip().lower(),
            )
        )
    return codes


def load_codes(path: Path = DEFAULT_REGISTRY) -> list[Code]:
    with path.open(encoding="utf-8") as f:
        return parse_codes(yaml.safe_load(f))


def get_code(codes: list[Code], slug: str) -> Code:
    for code in codes:
        if code.slug == slug:
            return code
    raise ConfigError(f"unknown code {slug!r}")
