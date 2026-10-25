"""Command line interface."""

from __future__ import annotations

from pathlib import Path

import click

from jurik.config import ConfigError, Paths, get_code, load_codes
from jurik.zakon.fetch import FetchResult, fetch_code
from jurik.zakon.http import ZakonClient


def make_zakon_client(cache: Path, *, refetch: bool = False) -> ZakonClient:
    """Factory for the zakon client (patched in tests)."""
    return ZakonClient(cache, refetch=refetch)


def _not_implemented(name: str) -> None:
    raise click.ClickException(f"'{name}' is not implemented yet")


@click.group()
def main() -> None:
    """Build a git repository of Ukrainian codes with legislative history."""


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--refetch", is_flag=True, help="Re-download even if cached.")
def fetch(code_slug: str | None, refetch: bool) -> None:
    """Download cards and editions from zakon.rada.gov.ua into cache/zakon/."""
    try:
        codes = load_codes()
        selected = [get_code(codes, code_slug)] if code_slug else codes
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    failed: list[FetchResult] = []
    with make_zakon_client(Paths(Path.cwd()).cache, refetch=refetch) as client:
        for code in selected:
            res = fetch_code(code, client, echo=click.echo)
            if not res.ok:
                failed.append(res)
    if failed:
        for res in failed:
            for err in res.errors:
                msg = f"ERROR {res.slug} {err.zakon_id} {err.edition}: {err.message}"
                click.echo(msg, err=True)
        raise click.ClickException(f"fetch finished with errors in {len(failed)} code(s)")


@main.command()
@click.option("--convocation", type=int, help="Convocation number (3..9).")
def rada(convocation: int | None) -> None:
    """Download Rada open data into cache/rada/."""
    _not_implemented("rada")


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--edition", help="Edition date YYYYMMDD.")
@click.option("--all", "all_editions", is_flag=True, help="Parse all editions.")
def parse(code_slug: str | None, edition: str | None, all_editions: bool) -> None:
    """Parse editions and verify text completeness."""
    _not_implemented("parse")


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--linear", is_flag=True, help="Linear history without bill branches.")
@click.option("--bills-limit", type=int, help="Limit number of bill branches.")
@click.option("--out", type=click.Path(path_type=Path), default=Path("build/laws"))
def build(code_slug: str | None, linear: bool, bills_limit: int | None, out: Path) -> None:
    """Build the laws git repository."""
    _not_implemented("build")


@main.command()
def report() -> None:
    """Write report/coverage.md and report/parse-failures.md."""
    _not_implemented("report")


@main.command()
@click.option("--at", "at_date", help="State at date YYYY-MM-DD.")
@click.option("--with-bills", is_flag=True, help="Include bill texts.")
@click.option("--out", type=click.Path(path_type=Path), required=True)
def export(at_date: str | None, with_bills: bool, out: Path) -> None:
    """Export a dataset from main at a given date."""
    _not_implemented("export")
