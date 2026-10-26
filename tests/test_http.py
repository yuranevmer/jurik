import httpx
import pytest

from jurik.http import ThrottledHttp, write_atomic


class Boom(RuntimeError):
    pass


def make(handler, **kw):
    sleeps: list[float] = []
    http = ThrottledHttp(
        user_agent="UA",
        error=Boom,
        min_interval=0,
        retries=kw.pop("retries", 2),
        backoff=1.0,
        timeout=1.0,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    return http, sleeps


def test_get_ok_sends_user_agent():
    seen = []

    def handler(request):
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, content=b"ok")

    with make(handler)[0] as http:
        assert http.get("https://x/a").content == b"ok"
    assert seen == ["UA"]


def test_retries_then_raises_caller_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    http, sleeps = make(handler)
    with pytest.raises(Boom, match=r"after 3 attempts \(transport error: refused\)"):
        http.get("https://x/a")
    assert sleeps == [1.0, 2.0]


def test_redirect_is_error_with_location():
    http, sleeps = make(lambda r: httpx.Response(302, headers={"location": "https://y/"}))
    with pytest.raises(Boom, match=r"HTTP 302 -> https://y/"):
        http.get("https://x/a")
    assert sleeps == []


def test_write_atomic(tmp_path):
    path = tmp_path / "a" / "b.bin"
    write_atomic(path, b"one")
    write_atomic(path, b"two")
    assert path.read_bytes() == b"two"
    assert [p.name for p in path.parent.iterdir()] == ["b.bin"]
