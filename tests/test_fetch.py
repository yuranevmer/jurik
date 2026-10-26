import json
from datetime import date

import httpx
from click.testing import CliRunner

from jurik import cli
from jurik.config import Code, load_codes
from jurik.zakon.fetch import fetch_code
from jurik.zakon.http import ZakonClient, read_gz, write_gz

DOC = "<html><body>" + "<p>Стаття 1. Текст</p>" * 50 + "</body></html>"
TODAY = date(2026, 10, 25)


def card_json(nreg: str, dates: list[int]) -> str:
    return json.dumps(
        {
            "nreg": nreg,
            "nazva": "Тестовий кодекс",
            "orgdat": 20020110,
            "status": 5,
            "eds": [{"datred": d, "pidstava": ""} for d in dates],
        },
        ensure_ascii=False,
    )


CARDS = {
    "1-01": card_json("1-01", [20020110, 20050101, 20100101, 20990101, 30000101]),
    "2-02": card_json("2-02", [20030101]),
}


def make_handler(calls, *, fail=frozenset()):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if any(path.endswith(f) for f in fail):
            return httpx.Response(404)
        if path.startswith("/laws/card/"):
            nreg = path.removeprefix("/laws/card/").removesuffix(".json")
            if nreg in CARDS:
                return httpx.Response(200, text=CARDS[nreg])
            return httpx.Response(404)
        return httpx.Response(200, text=DOC)

    return handler


def make_client(tmp_path, calls, **kw):
    return ZakonClient(
        tmp_path,
        transport=httpx.MockTransport(make_handler(calls, fail=kw.pop("fail", frozenset()))),
        sleep=lambda s: None,
        min_interval=0,
        **kw,
    )


def test_fetch_downloads_only_missing_and_skips_future(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls)
    write_gz(client.edition_path("1-01", "20050101"), "cached text")
    out: list[str] = []
    res = fetch_code(Code("t", ("1-01",), "Тестовий"), client, echo=out.append, today=TODAY)
    assert res.ok
    assert (res.downloaded, res.cached, res.future) == (2, 1, 2)
    eds = [c for c in calls if "/laws/show/" in c]
    assert eds == ["/laws/show/1-01/ed20020110", "/laws/show/1-01/ed20100101"]
    assert not client.edition_path("1-01", "20990101").exists()
    assert not client.edition_path("1-01", "30000101").exists()
    assert read_gz(client.edition_path("1-01", "20050101")) == "cached text"
    assert read_gz(client.edition_path("1-01", "20100101")) == DOC
    assert out[-1] == "t: 2 downloaded, 1 cached, 2 future skipped, 0 errors"


def test_fetch_second_run_uses_cache(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls)
    code = Code("t", ("1-01",), "Тестовий")
    fetch_code(code, client, echo=lambda s: None, today=TODAY)
    calls.clear()
    res = fetch_code(code, client, echo=lambda s: None, today=TODAY)
    assert calls == ["/laws/card/1-01.json"]  # only the card is refreshed
    assert (res.downloaded, res.cached) == (0, 3)


def test_fetch_refreshes_cached_card(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls)
    write_gz(client.card_path("1-01"), card_json("1-01", [20020110]))
    write_gz(client.edition_path("1-01", "20020110"), "cached text")
    res = fetch_code(Code("t", ("1-01",), "Тестовий"), client, echo=lambda s: None, today=TODAY)
    assert res.ok
    # the fresh card lists two more current editions; the cached one is not downloaded again
    assert (res.downloaded, res.cached) == (2, 1)
    assert calls == [
        "/laws/card/1-01.json",
        "/laws/show/1-01/ed20050101",
        "/laws/show/1-01/ed20100101",
    ]
    assert read_gz(client.card_path("1-01")) == CARDS["1-01"]
    assert read_gz(client.edition_path("1-01", "20020110")) == "cached text"


def test_fetch_card_refresh_failure_uses_cached_card(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls, fail={"1-01.json"})
    cached = card_json("1-01", [20020110])
    write_gz(client.card_path("1-01"), cached)
    out: list[str] = []
    res = fetch_code(Code("t", ("1-01",), "Тестовий"), client, echo=out.append, today=TODAY)
    assert res.ok
    assert res.downloaded == 1
    assert any("warning: card refresh failed, using cached card" in line for line in out)
    assert read_gz(client.card_path("1-01")) == cached


def test_fetch_refetch_redownloads(tmp_path):
    calls: list[str] = []
    code = Code("t", ("1-01",), "Тестовий")
    fetch_code(code, make_client(tmp_path, calls), echo=lambda s: None, today=TODAY)
    calls.clear()
    res = fetch_code(
        code, make_client(tmp_path, calls, refetch=True), echo=lambda s: None, today=TODAY
    )
    assert res.downloaded == 3
    assert len(calls) == 4  # card + 3 editions


def test_fetch_edition_error_collected_others_downloaded(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls, fail={"ed20050101"})
    res = fetch_code(Code("t", ("1-01",), "Тестовий"), client, echo=lambda s: None, today=TODAY)
    assert not res.ok
    assert res.downloaded == 2
    assert [(e.zakon_id, e.edition) for e in res.errors] == [("1-01", "20050101")]
    assert "404" in res.errors[0].message
    assert client.edition_path("1-01", "20100101").exists()
    assert not client.edition_path("1-01", "20050101").exists()


def test_fetch_multiple_ids_card_error_does_not_stop_others(tmp_path):
    calls: list[str] = []
    client = make_client(tmp_path, calls)
    res = fetch_code(
        Code("t", ("9-99", "2-02"), "Тестовий"), client, echo=lambda s: None, today=TODAY
    )
    assert [(e.zakon_id, e.edition) for e in res.errors] == [("9-99", "card")]
    assert res.downloaded == 1
    assert client.edition_path("2-02", "20030101").exists()


def test_cli_fetch(tmp_path, monkeypatch, write_registry):
    calls: list[str] = []
    registry = write_registry(
        "- slug: a\n  zakon_id: '1-01'\n  title: A\n- slug: b\n  zakon_id: '2-02'\n  title: B\n"
    )
    monkeypatch.setattr(cli, "load_codes", lambda: load_codes(registry))
    monkeypatch.setattr(
        cli, "make_zakon_client", lambda cache, refetch=False: make_client(cache, calls)
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.main, ["fetch", "--code", "b"])
    assert result.exit_code == 0, result.output
    assert "b: 1 downloaded" in result.output
    assert (tmp_path / "cache" / "zakon" / "2-02" / "ed20030101.html.gz").exists()
    assert not (tmp_path / "cache" / "zakon" / "1-01").exists()

    result = CliRunner().invoke(cli.main, ["fetch", "--code", "nope"])
    assert result.exit_code == 1
    assert "unknown code" in result.output


def test_cli_fetch_errors_exit_nonzero(tmp_path, monkeypatch, write_registry):
    calls: list[str] = []
    registry = write_registry("- slug: x\n  zakon_id: '9-99'\n  title: X\n")
    monkeypatch.setattr(cli, "load_codes", lambda: load_codes(registry))
    monkeypatch.setattr(
        cli, "make_zakon_client", lambda cache, refetch=False: make_client(cache, calls)
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.main, ["fetch"])
    assert result.exit_code == 1
    assert "ERROR x 9-99 card" in result.output
