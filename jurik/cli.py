"""Command line interface."""

from __future__ import annotations

import time
from pathlib import Path

import click

from jurik.build.fastimport import FastImportError, Stream, run
from jurik.build.history import (
    BuildError,
    cached_tree_loader,
    plan_full,
    plan_linear,
    sources_from_cache,
    write_steps,
)
from jurik.config import Code, ConfigError, Paths, get_code, load_codes
from jurik.parse import EditionCheck, failures_report, parse_code
from jurik.rada.client import CONVOCATIONS, RadaClient, RadaError
from jurik.rada.index import build_index
from jurik.rada.match import coverage_report
from jurik.render import RenderError
from jurik.zakon.card import CardParseError, base_kind, parse_card
from jurik.zakon.fetch import FetchResult, fetch_code
from jurik.zakon.http import ZakonClient


def make_zakon_client(cache: Path, *, refetch: bool = False) -> ZakonClient:
    """Factory for the zakon client (patched in tests)."""
    return ZakonClient(cache, refetch=refetch)


def make_rada_client(cache: Path, *, refetch: bool = False) -> RadaClient:
    """Factory for the Rada open data client (patched in tests)."""
    return RadaClient(cache, refetch=refetch)


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
@click.option("--convocation", type=click.IntRange(3, 9), help="Convocation number (3..9).")
@click.option("--refetch", is_flag=True, help="Re-download even if cached.")
def rada(convocation: int | None, refetch: bool) -> None:
    """Download Rada open data into cache/rada/, build the law index, print coverage."""
    convocations = [convocation] if convocation else list(CONVOCATIONS)
    paths = Paths(Path.cwd())
    try:
        codes = load_codes()
        with make_rada_client(paths.cache, refetch=refetch) as client:
            for n in convocations:
                click.echo(f"skl{n}: fetching")
                client.fetch_convocation(n, echo=click.echo)
        index = build_index(paths.cache, convocations)
    except (ConfigError, RadaError) as e:
        raise click.ClickException(str(e)) from e
    bases = cached_code_bases(codes, paths.cache)
    for line in coverage_report(index, codes, bases):
        click.echo(line)


def cached_code_bases(codes: list[Code], cache: Path) -> dict[str, list[str]]:
    """Law bases of every edition of each code whose card is already cached (no network)."""
    out: dict[str, list[str]] = {}
    with make_zakon_client(cache) as client:
        for code in codes:
            bases: list[str] = []
            for zakon_id in code.zakon_ids:
                if not client.is_cached(client.card_path(zakon_id)):
                    break
                try:
                    card = parse_card(client.card(zakon_id), zakon_id)
                except CardParseError:
                    break
                bases += [b for ed in card.editions for b in ed.bases if base_kind(b) == "law"]
            else:
                out[code.slug] = bases
    return out


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--edition", help="Edition date YYYYMMDD.")
@click.option("--all", "all_editions", is_flag=True, help="Parse all editions.")
def parse(code_slug: str | None, edition: str | None, all_editions: bool) -> None:
    """Parse cached editions and verify text completeness.

    Default: the latest edition of each document. Failures go to report/parse-failures.md.
    """
    try:
        codes = load_codes()
        selected = [get_code(codes, code_slug)] if code_slug else codes
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    paths = Paths(Path.cwd())
    checks: list[EditionCheck] = []
    with make_zakon_client(paths.cache) as client:
        for code in selected:
            checks += parse_code(
                code, client, edition=edition, all_editions=all_editions, echo=click.echo
            )
    paths.report.mkdir(parents=True, exist_ok=True)
    report_path = paths.report / "parse-failures.md"
    report_path.write_text(failures_report(checks), encoding="utf-8")
    failed = sum(not c.ok for c in checks)
    click.echo(f"{len(checks) - failed} ok, {failed} failed")
    if failed:
        raise click.ClickException(f"{failed} edition(s) failed, see {report_path}")


@main.command()
@click.option("--code", "code_slugs", multiple=True, help="Code slug (repeatable); default all.")
@click.option("--linear", is_flag=True, help="Linear history without bill branches.")
@click.option("--bills-limit", type=int, help="Limit number of bill branches.")
@click.option("--out", type=click.Path(path_type=Path), default=Path("build/laws"))
def build(code_slugs: tuple[str, ...], linear: bool, bills_limit: int | None, out: Path) -> None:
    """Build the laws git repository from cached editions (recreated from scratch)."""
    started = time.monotonic()
    paths = Paths(Path.cwd())
    try:
        codes = load_codes()
        selected = [get_code(codes, s) for s in code_slugs] if code_slugs else codes
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    try:
        index = build_index(paths.cache)
    except RadaError as e:
        click.echo(f"warning: no law index ({e}); messages without law titles", err=True)
        index = None
    try:
        with make_zakon_client(paths.cache) as client:
            sources = sources_from_cache(selected, client)
            plan = plan_linear if linear else plan_full
            steps = plan(sources, cached_tree_loader(client), index=index)
        stream = Stream()
        write_steps(steps, stream)
        run(stream, out)
    except (BuildError, FastImportError, RenderError) as e:
        raise click.ClickException(str(e)) from e
    took = time.monotonic() - started
    branches = len({s.ref for s in steps if s.kind == "branch"})
    merges = sum(s.kind == "merge" for s in steps)
    click.echo(
        f"{out}: {stream.commit_count} commits, {branches} branches, {merges} merges, "
        f"{stream.blob_count} blobs, {len(selected)} code(s), {took:.1f}s"
    )


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
