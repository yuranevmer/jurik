import gzip
import json

import httpx
import pytest

from jurik.http import OPENDATA_UA
from jurik.zakon.http import (
    NotCachedError,
    ZakonClient,
    ZakonError,
    check_error_page,
    read_gz,
    safe_id,
    write_gz,
)

DOC = "<html><body>" + "<p>Стаття 1. Текст</p>" * 50 + "</body></html>"


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make_client(tmp_path, handler, **kw):
    t = FakeTime()
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = ZakonClient(
        tmp_path,
        transport=httpx.MockTransport(wrapped),
        sleep=t.sleep,
        clock=t.clock,
        **kw,
    )
    return client, calls, t


def test_safe_id():
    assert safe_id("254к/96-вр") == "254к_96-вр"
    assert safe_id("2947-14") == "2947-14"


def test_opendata_urls_and_paths(tmp_path):
    c = ZakonClient(tmp_path)
    assert c.card_url("254к/96-вр") == "https://data.rada.gov.ua/laws/card/254к/96-вр.json"
    assert c.edition_url("2947-14", "20020110") == (
        "https://data.rada.gov.ua/laws/show/2947-14/ed20020110"
    )
    assert c.card_path("254к/96-вр") == tmp_path / "zakon" / "254к_96-вр" / "card.json.gz"
    assert c.edition_path("2947-14", "20020110") == (
        tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz"
    )


def test_user_agent(tmp_path):
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=DOC))
    client.get("https://example/x")
    assert calls[0].headers["user-agent"] == OPENDATA_UA


def test_cache_miss_then_hit(tmp_path):
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=DOC))
    assert client.edition("2947-14", "20020110") == DOC
    assert len(calls) == 1
    path = tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz"
    assert path.is_file()
    # second call is served from disk
    assert client.edition("2947-14", "20020110") == DOC
    assert len(calls) == 1


def test_cache_hit_does_not_touch_network(tmp_path):
    write_gz(tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz", "cached")

    def boom(request):
        raise AssertionError("network used")

    client, calls, _ = make_client(tmp_path, boom)
    assert client.edition("2947-14", "20020110") == "cached"
    assert calls == []


def test_refetch_redownloads(tmp_path):
    write_gz(tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz", "old")
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=DOC), refetch=True)
    assert client.edition("2947-14", "20020110") == DOC
    assert len(calls) == 1
    assert read_gz(tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz") == DOC


def test_gzip_roundtrip(tmp_path):
    path = tmp_path / "a" / "b.html.gz"
    write_gz(path, "Кодекс — ʼапостроф")
    assert gzip.decompress(path.read_bytes()).decode("utf-8") == "Кодекс — ʼапостроф"
    assert read_gz(path) == "Кодекс — ʼапостроф"
    assert not list(path.parent.glob("*.tmp"))


def test_card_json_cached(tmp_path):
    body = json.dumps({"nreg": "2947-14", "eds": []})
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=body))
    assert json.loads(client.card("2947-14"))["nreg"] == "2947-14"
    assert str(calls[0].url) == "https://data.rada.gov.ua/laws/card/2947-14.json"
    assert (tmp_path / "zakon" / "2947-14" / "card.json.gz").is_file()


def test_card_invalid_json_not_cached(tmp_path):
    client, _, _ = make_client(tmp_path, lambda r: httpx.Response(200, text="<html>" + "x" * 60000))
    with pytest.raises(ZakonError, match="invalid JSON"):
        client.card("2947-14")
    assert not (tmp_path / "zakon" / "2947-14" / "card.json.gz").exists()


def test_card_cached_unless_refresh(tmp_path):
    path = tmp_path / "zakon" / "2947-14" / "card.json.gz"
    write_gz(path, '{"old": 1}')
    new = json.dumps({"nreg": "2947-14", "eds": []})
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=new))
    assert client.card("2947-14") == '{"old": 1}'
    assert calls == []
    assert client.card("2947-14", refresh=True) == new
    assert len(calls) == 1
    assert read_gz(path) == new


def test_card_refresh_invalid_json_keeps_cache(tmp_path):
    path = tmp_path / "zakon" / "2947-14" / "card.json.gz"
    write_gz(path, '{"old": 1}')
    client, _, _ = make_client(tmp_path, lambda r: httpx.Response(200, text="<html>" + "x" * 60000))
    with pytest.raises(ZakonError, match="invalid JSON"):
        client.card("2947-14", refresh=True)
    assert read_gz(path) == '{"old": 1}'


def test_retry_on_503(tmp_path):
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, text=DOC)])
    client, calls, t = make_client(tmp_path, lambda r: next(responses))
    assert client.edition("2947-14", "20020110") == DOC
    assert len(calls) == 3
    # exponential backoff 2s, 4s; after the 2s backoff the 2.5s interval still needs 0.5s
    assert t.sleeps == [2.0, 0.5, 4.0]


def test_retry_on_transport_error(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, text=DOC)

    client, calls, t = make_client(tmp_path, handler)
    assert client.edition("2947-14", "20020110") == DOC
    assert len(calls) == 2
    assert t.sleeps == [2.0, 0.5]


def test_transport_error_gives_up(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client, calls, _ = make_client(tmp_path, handler, retries=1)
    with pytest.raises(ZakonError, match=r"after 2 attempts \(transport error: refused\)"):
        client.get("https://example/x")
    assert len(calls) == 2


def test_retry_on_timeout(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text=DOC)

    client, calls, _ = make_client(tmp_path, handler)
    assert client.get("https://example/x") == DOC
    assert len(calls) == 2


def test_gives_up_after_retries(tmp_path):
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(503))
    with pytest.raises(ZakonError, match="after 4 attempts"):
        client.edition("2947-14", "20020110")
    assert len(calls) == 4
    assert not (tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz").exists()


@pytest.mark.parametrize("status", [403, 404, 302])
def test_client_error_not_cached_not_retried(tmp_path, status):
    headers = {"location": "https://zakon.rada.gov.ua/x"} if status == 302 else {}
    client, calls, _ = make_client(
        tmp_path, lambda r: httpx.Response(status, text="denied", headers=headers)
    )
    with pytest.raises(ZakonError, match=f"HTTP {status}"):
        client.edition("2947-14", "20020110")
    assert len(calls) == 1
    assert not (tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz").exists()


@pytest.mark.parametrize(
    "body",
    [
        "<html><body><h1>Документ не знайдено</h1></body></html>",
        '<html><div class="g-recaptcha"></div></html>',
        "   ",
    ],
)
def test_error_page_not_cached(tmp_path, body):
    client, _, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=body))
    with pytest.raises(ZakonError):
        client.edition("2947-14", "20020110")
    assert not (tmp_path / "zakon" / "2947-14" / "ed20020110.html.gz").exists()


def test_long_document_mentioning_marker_is_not_error():
    text = "<p>Документ не знайдено</p>" + "<p>текст</p>" * 10000
    check_error_page(text, "u")


@pytest.mark.parametrize("name", ["card-family.html", "card-tax.html", "card-labour.html"])
def test_real_cards_are_not_error_pages(fixtures_dir, name):
    check_error_page((fixtures_dir / "zakon" / name).read_text(encoding="utf-8"), name)


def test_min_interval_between_requests(tmp_path):
    client, calls, t = make_client(tmp_path, lambda r: httpx.Response(200, text=DOC))
    client.get("https://example/1")
    assert t.sleeps == []
    t.now += 1.0
    client.get("https://example/2")
    assert t.sleeps == [pytest.approx(1.5)]
    t.now += 10.0
    client.get("https://example/3")
    assert len(t.sleeps) == 1


@pytest.mark.network
def test_real_opendata_card(tmp_path):
    with ZakonClient(tmp_path) as client:
        data = json.loads(client.card("2947-14"))
    assert data["nreg"] == "2947-14"
    assert len(data["eds"]) >= 60


def test_read_gz_unreadable_is_zakon_error(tmp_path):
    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress("текст".encode())[:-6])
    with pytest.raises(ZakonError, match="unreadable cache"):
        read_gz(path)
    with pytest.raises(ZakonError, match="unreadable cache"):
        read_gz(tmp_path / "missing.gz")


def test_cached_edition_never_downloads(tmp_path):
    client, calls, _ = make_client(tmp_path, lambda r: httpx.Response(200, text=DOC))
    with pytest.raises(NotCachedError, match="2947-14 ed20020110 not cached"):
        client.cached_edition("2947-14", "20020110")
    write_gz(client.edition_path("2947-14", "20020110"), DOC)
    assert client.cached_edition("2947-14", "20020110") == DOC
    assert calls == []
