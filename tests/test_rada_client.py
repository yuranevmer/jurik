import io
import zipfile

import httpx
import pytest

from jurik.rada.client import (
    BILLINFO,
    RadaClient,
    RadaError,
    check_payload,
    convocation_files,
    read_cached,
)

CSV = b"bill_id,act_number\n1,229-IX\n"


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("billinfo-skl9.json", "[]")
    return buf.getvalue()


def make_client(tmp_path, handler, **kw):
    calls: list[str] = []
    sleeps: list[float] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    client = RadaClient(
        tmp_path,
        transport=httpx.MockTransport(wrapped),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        **kw,
    )
    return client, calls, sleeps


def ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["User-Agent"] == "OpenData"
    if request.url.path.endswith(".zip"):
        return httpx.Response(200, content=make_zip())
    return httpx.Response(200, content=CSV)


def test_convocation_files_csv_and_billinfo():
    names3 = [n for n, _ in convocation_files(3)]
    assert "bills_main-skl3.csv" in names3
    assert "plenary_result_event-skl3.csv" in names3
    assert BILLINFO not in names3
    names9 = dict(convocation_files(9))
    assert names9[BILLINFO] == "https://data.rada.gov.ua/ogd/zpr/skl9/billinfo-skl9.zip"
    assert "bills_main-skl9.csv" not in names9
    assert names9["plenary_agenda-skl9.csv"].startswith(
        "https://data.rada.gov.ua/ogd/zal/ppz/skl9/"
    )
    with pytest.raises(RadaError):
        convocation_files(2)


def test_fetch_caches_and_reuses(tmp_path):
    client, calls, _ = make_client(tmp_path, ok_handler)
    paths = client.fetch_convocation(5)
    assert len(paths) == 8 and len(calls) == 8
    assert all(p.parent == tmp_path / "rada" / "skl5" for p in paths)
    assert read_cached(client.path(5, "bills_acts-skl5.csv")) == CSV
    client.fetch_convocation(5)
    assert len(calls) == 8  # cache hit, no network

    refetch, calls2, _ = make_client(tmp_path, ok_handler, refetch=True)
    refetch.fetch_convocation(5)
    assert len(calls2) == 8


def test_zip_kept_as_is(tmp_path):
    client, _, _ = make_client(tmp_path, ok_handler)
    client.fetch_convocation(9)
    path = client.path(9, BILLINFO)
    assert path.name == BILLINFO
    assert zipfile.ZipFile(path).namelist() == ["billinfo-skl9.json"]


def test_retry_on_503(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=CSV)

    client, calls, sleeps = make_client(tmp_path, handler, min_interval=0)
    assert client.get("https://data.rada.gov.ua/x.csv") == CSV
    assert len(calls) == 2
    assert sleeps == [5.0]


def test_retry_on_transport_error(tmp_path):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        if state["n"] == 2:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, content=CSV)

    client, calls, sleeps = make_client(tmp_path, handler, min_interval=0)
    assert client.get("https://data.rada.gov.ua/x.csv") == CSV
    assert len(calls) == 3
    assert sleeps == [5.0, 10.0]


def test_gives_up_after_retries(tmp_path):
    client, calls, sleeps = make_client(tmp_path, lambda r: httpx.Response(503), min_interval=0)
    with pytest.raises(RadaError, match=r"after 4 attempts \(HTTP 503\)"):
        client.get("https://data.rada.gov.ua/x.csv")
    assert len(calls) == 4
    assert sleeps == [5.0, 10.0, 20.0]


def test_request_passes_headers(tmp_path):
    def handler(request):
        assert request.headers["X-File-Id"] == "7"
        return httpx.Response(200, content=b"x", headers={"Size": "1"})

    client, _, _ = make_client(tmp_path, handler)
    resp = client.request("https://data.rada.gov.ua/d", headers={"X-File-Id": "7"})
    assert resp.headers["Size"] == "1"


def test_404_html_not_cached(tmp_path):
    def handler(request):
        return httpx.Response(404, content="<html>не знайдено</html>".encode("cp1251"))

    client, _, _ = make_client(tmp_path, handler)
    with pytest.raises(RadaError, match="HTTP 404"):
        client.fetch_convocation(3)
    assert not (tmp_path / "rada" / "skl3").exists()


def test_html_200_rejected(tmp_path):
    client, _, _ = make_client(tmp_path, lambda r: httpx.Response(200, content=b"<html></html>"))
    with pytest.raises(RadaError, match="HTML"):
        client.fetch_convocation(3)
    assert not list(tmp_path.rglob("*.gz"))


def test_check_payload_rejects_bad_zip():
    with pytest.raises(RadaError, match="not a zip"):
        check_payload(BILLINFO, b"PK garbage", "u")
    check_payload(BILLINFO, make_zip(), "u")


def test_interval_between_requests(tmp_path):
    client, _, sleeps = make_client(tmp_path, ok_handler, min_interval=2.5)
    client.get("https://data.rada.gov.ua/a.csv")
    client.get("https://data.rada.gov.ua/b.csv")
    assert sleeps == [2.5]


def test_read_cached_corrupt_gzip(tmp_path):
    bad = tmp_path / "x.csv.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(RadaError, match="x.csv.gz"):
        read_cached(bad)
