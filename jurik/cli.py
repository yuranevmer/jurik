"""Command line interface."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import click

from jurik.build.check import check_repo
from jurik.build.fastimport import Stream, run
from jurik.build.history import plan_full, plan_linear, write_steps
from jurik.build.readme import add_readme
from jurik.build.sources import cached_tree_loader, latest_prints, sources_from_cache
from jurik.config import Code, ConfigError, Paths, get_code, load_codes
from jurik.errors import JurikError
from jurik.export import export as export_dataset
from jurik.model import kyiv_today
from jurik.parse import EditionCheck, failures_report, parse_code
from jurik.rada.billtext import BillTexts
from jurik.rada.client import CONVOCATIONS, RadaClient, RadaError
from jurik.rada.index import LawIndex, build_index
from jurik.rada.match import coverage_report
from jurik.report import (
    CodeCoverage,
    branches_by_code,
    code_coverage,
    coverage_markdown,
    repo_stats,
)
from jurik.zakon.fetch import FetchResult, cached_code_bases, cached_editions, fetch_code
from jurik.zakon.http import ZakonClient


def make_zakon_client(cache: Path, *, refetch: bool = False) -> ZakonClient:
    """Factory for the zakon client (patched in tests)."""
    return ZakonClient(cache, refetch=refetch)


def make_rada_client(cache: Path, *, refetch: bool = False) -> RadaClient:
    """Factory for the Rada open data client (patched in tests)."""
    return RadaClient(cache, refetch=refetch)


def make_bill_texts(cache: Path) -> BillTexts:
    """Factory for bill text downloads (patched in tests)."""
    return BillTexts(cache, echo=lambda msg: click.echo(msg, err=True))


def _select_codes(slugs: tuple[str, ...] | list[str]) -> list[Code]:
    """Codes of ``codes.yaml`` with these slugs (all when empty); bad config → CLI error."""
    try:
        codes = load_codes()
        return [get_code(codes, s) for s in slugs] if slugs else codes
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


def _load_index(cache: Path, warning: str) -> LawIndex | None:
    """The Rada law index, or None with ``warning: no law index (…)<warning>`` on stderr."""
    try:
        return build_index(cache)
    except RadaError as e:
        click.echo(f"warning: no law index ({e}){warning}", err=True)
        return None


@click.group()
def main() -> None:
    """Build a git repository of Ukrainian codes with legislative history."""


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--refetch", is_flag=True, help="Re-download even if cached.")
def fetch(code_slug: str | None, refetch: bool) -> None:
    """Download cards and editions (data.rada.gov.ua open data API) into cache/zakon/."""
    selected = _select_codes([code_slug] if code_slug else [])
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
    with make_zakon_client(paths.cache) as zakon:
        bases = cached_code_bases(codes, zakon)
    for line in coverage_report(index, codes, bases):
        click.echo(line)


@main.command()
@click.option("--code", "code_slug", help="Code slug from codes.yaml.")
@click.option("--edition", help="Edition date YYYYMMDD.")
@click.option("--all", "all_editions", is_flag=True, help="Parse all editions.")
def parse(code_slug: str | None, edition: str | None, all_editions: bool) -> None:
    """Parse cached editions and verify text completeness.

    Default: the latest edition of each document. Failures go to report/parse-failures.md.
    """
    selected = _select_codes([code_slug] if code_slug else [])
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
@click.option(
    "--bills-limit",
    type=click.IntRange(min=0),
    help="Max branches of bills that did not become law (earliest first); texts are "
    "downloaded into cache/rada/bills/.",
)
@click.option("--out", type=click.Path(path_type=Path), default=Path("build/laws"))
def build(code_slugs: tuple[str, ...], linear: bool, bills_limit: int | None, out: Path) -> None:
    """Build the laws git repository from cached editions (recreated from scratch).

    Run `jurik fetch` and `jurik rada` first: without the Rada law index the build only
    warns and every law branch becomes bill/law/<nreg> without bill metadata.
    """
    started = time.monotonic()
    paths = Paths(Path.cwd())
    selected = _select_codes(code_slugs)
    index = _load_index(paths.cache, "; messages without law titles")
    try:
        with make_zakon_client(paths.cache) as client:
            sources = sources_from_cache(selected, client)
            if linear:
                steps = plan_linear(sources, cached_tree_loader(client), index=index)
            else:
                with make_bill_texts(paths.cache) as texts:
                    steps = plan_full(
                        sources,
                        cached_tree_loader(client),
                        index=index,
                        bill_texts=texts,
                        bills_limit=bills_limit,
                    )
            prints = latest_prints(sources, client)
        add_readme(steps, selected, bills_limit=None if linear else bills_limit)
        stream = Stream()
        write_steps(steps, stream)
        run(stream, out)
        violations = check_repo(out, steps, prints)
    except (JurikError, OSError) as e:
        # JurikError: missing/broken cache, unusable ref names or messages, git failures;
        # OSError: the output directory
        raise click.ClickException(str(e)) from e
    took = time.monotonic() - started
    branches = len({s.ref for s in steps if s.kind == "branch"})
    merges = sum(s.kind == "merge" for s in steps)
    click.echo(
        f"{out}: {stream.commit_count} commits, {branches} branches, {merges} merges, "
        f"{stream.blob_count} blobs, {len(selected)} code(s), {took:.1f}s"
    )
    for v in violations:
        click.echo(f"VIOLATION {v}", err=True)
    if violations:
        raise click.ClickException(f"check_repo: {len(violations)} violation(s)")
    click.echo("check_repo: 0 violations")


@main.command()
@click.option("--code", "code_slugs", multiple=True, help="Code slug (repeatable); default all.")
@click.option(
    "--repo",
    type=click.Path(path_type=Path),
    default=Path("build/laws"),
    help="Built repository for commit and branch counts.",
)
def report(code_slugs: tuple[str, ...], repo: Path) -> None:
    """Write report/coverage.md and report/parse-failures.md.

    Parses every cached in-force edition (no downloads); codes without a cached card are
    listed as not fetched.
    """
    paths = Paths(Path.cwd())
    selected = _select_codes(code_slugs)
    index = _load_index(paths.cache, "")
    stats = repo_stats(repo)
    branch_counts = branches_by_code(stats.bills, selected) if stats else None
    rows: list[CodeCoverage] = []
    checks: list[EditionCheck] = []
    missing: list[str] = []
    with make_zakon_client(paths.cache) as client:
        for code in selected:
            editions = cached_editions(code, client)
            if editions is None:
                missing.append(code.slug)
                continue
            click.echo(f"{code.slug}: parsing {len(editions)} edition(s)")
            code_checks = parse_code(code, client, all_editions=True, echo=lambda _: None)
            checks += [c for c in code_checks if not c.not_cached]
            branches = branch_counts.get(code.slug) if branch_counts is not None else None
            rows.append(code_coverage(code, editions, code_checks, index, branches))
        bases = cached_code_bases(selected, client) if index is not None else {}
    open_data: list[str] = []
    if index is not None:
        open_data = coverage_report(index, selected, bases)
    paths.report.mkdir(parents=True, exist_ok=True)
    coverage_path = paths.report / "coverage.md"
    coverage_path.write_text(
        coverage_markdown(
            rows,
            repo=stats,
            repo_path=str(repo),
            open_data=open_data,
            missing=missing,
        ),
        encoding="utf-8",
    )
    (paths.report / "parse-failures.md").write_text(failures_report(checks), encoding="utf-8")
    failed = sum(not c.ok for c in checks)
    click.echo(
        f"{coverage_path}: {len(rows)} code(s), {len(checks)} cached edition(s) parsed,"
        f" {failed} failed"
    )


@main.command()
@click.option(
    "--at",
    "at_date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="State at date YYYY-MM-DD (by In-force:); default today.",
)
@click.option("--with-bills", is_flag=True, help="Also export texts of bills that are not law.")
@click.option(
    "--repo", type=click.Path(path_type=Path), default=Path("build/laws"), help="Built repository."
)
@click.option("--out", type=click.Path(path_type=Path), required=True)
def export(at_date: datetime | None, with_bills: bool, repo: Path, out: Path) -> None:
    """Export the markdown of main in force on a date, plus manifest.json."""
    at = at_date.date() if at_date else kyiv_today()
    try:
        res = export_dataset(repo, at, out, with_bills=with_bills)
    except JurikError as e:
        raise click.ClickException(str(e)) from e
    msg = f"{out}: {res.files} file(s) of {len(res.codes)} code(s) at {res.commit[:10]}"
    msg += f" (In-force {res.in_force})"
    if with_bills:
        msg += f", {len(res.bills)} bill(s)"
    click.echo(msg)
