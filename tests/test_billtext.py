from datetime import date

import httpx
import pytest

from jurik.model import BillRef
from jurik.rada.billtext import (
    IX_DOWNLOAD_URL,
    BillDoc,
    BillTexts,
    billinfo_documents,
    csv_documents,
    detect,
    file_stem,
    is_text_type,
    paragraphs_from_lines,
    rtf_to_markdown,
    to_markdown,
)
from jurik.rada.client import RadaClient
from jurik.rada.index import read_billinfo


@pytest.fixture
def bill_fixture(fixtures_dir):
    return lambda ext: (fixtures_dir / "rada" / f"bill.{ext}").read_bytes()


@pytest.mark.parametrize("ext", ["docx", "pdf", "rtf", "html", "doc"])
def test_detect_real_documents(bill_fixture, ext):
    assert detect(bill_fixture(ext)) == ext


def test_docx_to_markdown_paragraphs(bill_fixture):
    text = to_markdown(bill_fixture("docx"))
    paras = text.split("\n\n")
    assert paras[0] == "Проект"
    assert "Закон України" in paras
    assert any(p.startswith("1. Мораторій на купівлю-продаж земель") for p in paras)
    assert all(p.strip() == p and p for p in paras)


def test_pdf_to_text(bill_fixture):
    text = to_markdown(bill_fixture("pdf"))
    paras = text.split("\n\n")
    assert "ЗАКОН УКРАЇНИ" in paras
    assert (
        "1. Частину другу статті 246 Сімейного кодексу України (Відомості Верховної Ради "
        "України, 2002 р., № 21—22, ст. 135) виключити." in paras
    )


def test_rtf_to_markdown(bill_fixture):
    paras = to_markdown(bill_fixture("rtf")).split("\n\n")
    assert paras[:2] == ["Проект", "Вноситься народними депутатами України"]
    assert "Про внесення змін до Сімейного кодексу України" in paras


def test_typewriter_rtf_is_reflowed():
    rtf = (
        r"{\rtf1\ansi\ansicpg1251 \par         ЗАКОН\par"
        r"     Перший  абзац  довгий\par продовження.\par     Другий.\par}"
    ).encode("cp1251")
    assert rtf_to_markdown(rtf).split("\n\n") == [
        "ЗАКОН",
        "Перший абзац довгий продовження.",
        "Другий.",
    ]


def test_html_pre_reflowed_by_indentation(bill_fixture):
    paras = to_markdown(bill_fixture("html")).split("\n\n")
    assert "ЗАКОН УКРАЇНИ" in paras
    assert (
        "Рамкову угоду щодо грантів технічної допомоги між Україною та Міжнародним банком "
        "реконструкції та розвитку, підписану 14 січня 1998 року, ратифікувати." in paras
    )


def test_html_block_tags():
    html = "<html><body><p>Один</p><div><p>Два  <b>три</b></p></div></body></html>"
    assert to_markdown(html.encode("cp1251")) == "Один\n\nДва три"


def test_word97_doc_has_no_text(bill_fixture):
    assert to_markdown(bill_fixture("doc")) is None
    assert to_markdown(b"") is None


def test_paragraphs_from_lines():
    lines = ["Стаття 1. Перше речення", "продовжується тут.", "2. Друге.", "", "Третій"]
    assert paragraphs_from_lines(lines) == [
        "Стаття 1. Перше речення продовжується тут.",
        "2. Друге.",
        "Третій",
    ]


def test_is_text_type():
    for kind in ("Проект Закону", "Проєкт Закону", "Проект Закону (на заміну)", "Проєкт Кодексу"):
        assert is_text_type(kind)
    for kind in ("Проект Постанови", "Пояснювальна записка", "Проєкт Постанови до Закону"):
        assert not is_text_type(kind)


def test_documents_from_open_data(rada_cache):
    docs = csv_documents(rada_cache, 5)
    assert docs["0903"].url.endswith("pf35401=89729")
    assert "2221" not in docs  # no «Проект Закону» document
    ix = billinfo_documents(read_billinfo(rada_cache / "rada" / "skl9" / "billinfo-skl9.zip"))
    assert ix["15112-д"].file_id == "3454906"


def test_latest_text_document_wins():
    bills = [
        {
            "registrationNumber": "1",
            "documents": {
                "source": [
                    {
                        "kind": "Проєкт Закону",
                        "registrationDate": "2020-01-01",
                        "docFiles": [{"id": 1, "name": "a.pdf", "type": "Проєкт Закону"}],
                    },
                    {
                        "kind": "Проєкт Закону",
                        "registrationDate": "2020-02-01",
                        "docFiles": [
                            {"id": 2, "name": "b.pdf", "type": "Проєкт Закону"},
                            {"id": 3, "name": "b.docx", "type": "Проєкт Закону"},
                            {"id": 4, "name": "c.pdf", "type": "Подання"},
                        ],
                    },
                ]
            },
        }
    ]
    assert billinfo_documents(bills)["1"].file_id == "3"


def make_texts(tmp_path, rada_cache, handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    client = RadaClient(
        tmp_path, transport=httpx.MockTransport(wrapped), sleep=lambda _: None, retries=0
    )
    return BillTexts(rada_cache, client), calls


def test_fetch_no_document_returns_none(tmp_path, rada_cache):
    def handler(request):
        raise AssertionError("no request expected")

    texts, calls = make_texts(tmp_path, rada_cache, handler)
    assert texts.fetch(BillRef(5, "2221")) is None
    assert texts.fetch(BillRef(5, "99999")) is None
    assert calls == []


def test_fetch_csv_document_cached(tmp_path, rada_cache, bill_fixture):
    texts, calls = make_texts(
        tmp_path, rada_cache, lambda r: httpx.Response(200, content=bill_fixture("rtf"))
    )
    text = texts.fetch(BillRef(5, "0903"))
    assert "Про внесення змін до Сімейного кодексу України" in text
    assert (rada_cache / "rada" / "bills" / "5" / "0903.rtf").read_bytes() == bill_fixture("rtf")
    assert texts.fetch(BillRef(5, "0903")) == text
    assert len(calls) == 1
    assert "pf35401=89729" in str(calls[0].url)


def test_fetch_ix_chunked(tmp_path, rada_cache, bill_fixture):
    data = bill_fixture("pdf")
    size = 40000

    def handler(request):
        assert str(request.url) == IX_DOWNLOAD_URL
        assert request.headers["X-File-Id"] == "3454906"
        chunk = int(request.headers["X-Current-Chunk"])
        part = data[chunk * size : (chunk + 1) * size]
        return httpx.Response(200, content=part, headers={"Size": str(len(data))})

    texts, calls = make_texts(tmp_path, rada_cache, handler)
    text = texts.fetch(BillRef(9, "15112-д"))
    assert "ЗАКОН УКРАЇНИ" in text
    assert len(calls) == 3
    assert (rada_cache / "rada" / "bills" / "9" / "15112-д.pdf").read_bytes() == data


def test_fetch_failure_is_remembered_for_a_week(tmp_path, rada_cache, bill_fixture):
    messages = []
    status = [404]
    texts, calls = make_texts(
        tmp_path, rada_cache, lambda r: httpx.Response(status[0], content=bill_fixture("rtf"))
    )
    texts._echo = messages.append
    texts.today = date(2026, 11, 1)
    bill = BillRef(5, "0903")
    assert texts.fetch(bill) is None
    assert texts.cached(bill) is None
    assert "HTTP 404" in messages[0]
    marker = rada_cache / "rada" / "bills" / "5" / "0903.failed"
    assert marker.read_text().startswith("2026-11-01 ")
    # not asked again within a week (also by a new BillTexts: the marker is on disk)
    texts.today = date(2026, 11, 7)
    assert texts.fetch(bill) is None
    assert len(calls) == 1
    # retried after a week; a success replaces the marker
    status[0] = 200
    texts.today = date(2026, 11, 8)
    assert "Сімейного кодексу" in texts.fetch(bill)
    assert len(calls) == 2
    assert not marker.exists()
    assert texts.cached(bill).name == "0903.rtf"


def test_unreachable_host_is_skipped_for_the_rest_of_the_run(tmp_path, rada_cache):
    def handler(request):
        raise httpx.ConnectError("nodename nor servname provided")

    texts, calls = make_texts(tmp_path, rada_cache, handler)
    texts._docs[5] = {
        "1": BillDoc(url="http://gone.invalid/a"),
        "2": BillDoc(url="http://gone.invalid/b"),
        "3": BillDoc(url="http://other.invalid/c"),
    }
    assert texts.fetch(BillRef(5, "1")) is None
    assert len(calls) == 1
    # another bill on the same host: no request at all; another host is still asked
    assert texts.fetch(BillRef(5, "2")) is None
    assert len(calls) == 1
    assert texts.fetch(BillRef(5, "3")) is None
    assert len(calls) == 2


def test_failure_marker_with_bad_date_is_ignored(tmp_path, rada_cache, bill_fixture):
    texts, calls = make_texts(
        tmp_path, rada_cache, lambda r: httpx.Response(200, content=bill_fixture("rtf"))
    )
    folder = rada_cache / "rada" / "bills" / "5"
    folder.mkdir(parents=True)
    (folder / "0903.failed").write_text("garbage")
    assert texts.cached(BillRef(5, "0903")) is None
    assert texts.fetch(BillRef(5, "0903"))
    assert len(calls) == 1


def test_fetch_doc_is_cached_but_unavailable(tmp_path, rada_cache, bill_fixture):
    texts, calls = make_texts(
        tmp_path, rada_cache, lambda r: httpx.Response(200, content=bill_fixture("doc"))
    )
    assert texts.fetch(BillRef(5, "0903")) is None
    assert texts.cached(BillRef(5, "0903")).name == "0903.doc"
    assert texts.fetch(BillRef(5, "0903")) is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("number", "stem"),
    [
        ("0903", "0903"),
        ("15112-д", "15112-д"),
        (" 3029-д ", "3029-д"),
        ("1234/1", "1234_1"),
        ("1..2", "1..2"),
        ("..", "__"),
        ("../../etc", "___.._etc"),
        ("..\\x", "___x"),
        (".hidden", "_hidden"),
        ("a\x00b", "a_b"),
        ("", "_"),
        ("   ", "_"),
    ],
)
def test_file_stem(number, stem):
    assert file_stem(number) == stem


@pytest.mark.parametrize("number", ["..", "../x", "..\\x", "/abs", ".", ""])
def test_file_stem_stays_in_folder(tmp_path, number):
    folder = tmp_path / "9"
    path = (folder / f"{file_stem(number)}.pdf").resolve()
    assert path.parent == folder.resolve()
    assert not path.name.startswith(".")


def ix_texts(tmp_path, rada_cache, handler):
    messages = []
    texts, calls = make_texts(tmp_path, rada_cache, handler)
    texts._echo = messages.append
    return texts, calls, messages


def assert_nothing_cached(rada_cache):
    folder = rada_cache / "rada" / "bills" / "9"
    # only failure markers, no text
    assert not folder.exists() or all(f.suffix == ".failed" for f in folder.iterdir())


def test_fetch_ix_size_mismatch(tmp_path, rada_cache, bill_fixture):
    data = bill_fixture("pdf")

    def handler(request):
        chunk = int(request.headers["X-Current-Chunk"])
        part = data[:1000] if chunk == 0 else b""  # the server stops early
        return httpx.Response(200, content=part, headers={"Size": str(len(data))})

    texts, calls, messages = ix_texts(tmp_path, rada_cache, handler)
    assert texts.fetch(BillRef(9, "15112-д")) is None
    assert len(calls) == 2
    assert f"got 1000 of {len(data)} bytes" in messages[0]
    assert messages[0].startswith("warning: bill 9/15112-д")
    assert_nothing_cached(rada_cache)


def test_fetch_ix_short_last_chunk_overshoot(tmp_path, rada_cache, bill_fixture):
    data = bill_fixture("pdf")

    def handler(request):  # announced size smaller than what was sent
        return httpx.Response(200, content=data, headers={"Size": str(len(data) - 1)})

    texts, _, messages = ix_texts(tmp_path, rada_cache, handler)
    assert texts.fetch(BillRef(9, "15112-д")) is None
    assert f"got {len(data)} of {len(data) - 1} bytes" in messages[0]
    assert_nothing_cached(rada_cache)


def test_fetch_ix_no_size_header_single_chunk(tmp_path, rada_cache, bill_fixture):
    data = bill_fixture("pdf")

    def handler(request):
        assert request.headers["X-Current-Chunk"] == "0"
        return httpx.Response(200, content=data)

    texts, calls, messages = ix_texts(tmp_path, rada_cache, handler)
    assert "ЗАКОН УКРАЇНИ" in texts.fetch(BillRef(9, "15112-д"))
    assert len(calls) == 1
    assert messages == []
    assert (rada_cache / "rada" / "bills" / "9" / "15112-д.pdf").read_bytes() == data


def test_fetch_ix_empty_first_chunk(tmp_path, rada_cache):
    def handler(request):
        return httpx.Response(200, content=b"", headers={"Size": "0"})

    texts, calls, messages = ix_texts(tmp_path, rada_cache, handler)
    assert texts.fetch(BillRef(9, "15112-д")) is None
    assert len(calls) == 1
    assert messages == ["warning: bill 9/15112-д: empty document"]
    assert_nothing_cached(rada_cache)


def test_fetch_ix_undetectable_document(tmp_path, rada_cache):
    def handler(request):  # whitespace only: detect() is None
        return httpx.Response(200, content=b"  \n ", headers={"Size": "4"})

    texts, _, messages = ix_texts(tmp_path, rada_cache, handler)
    assert texts.fetch(BillRef(9, "15112-д")) is None
    assert messages == ["warning: bill 9/15112-д: empty document"]
    assert_nothing_cached(rada_cache)


@pytest.mark.parametrize(("convocation", "missing"), [(9, "billinfo-skl9"), (5, "bills_")])
def test_fetch_without_open_data_warns_once(tmp_path, convocation, missing):
    def handler(request):
        raise AssertionError("no request expected")

    cache = tmp_path / "empty-cache"
    messages = []
    texts, calls = make_texts(tmp_path, cache, handler)
    texts._echo = messages.append
    assert texts.fetch(BillRef(convocation, "1")) is None
    assert texts.fetch(BillRef(convocation, "2")) is None
    assert calls == []
    assert len(messages) == 1
    assert messages[0].startswith(f"warning: bill texts of convocation {convocation}:")
    assert missing in messages[0] and "not cached" in messages[0]
    assert not (cache / "rada" / "bills").exists()


def test_fetch_without_open_data_uses_cached_text(tmp_path, bill_fixture):
    folder = tmp_path / "rada" / "bills" / "5"
    folder.mkdir(parents=True)
    (folder / "0903.rtf").write_bytes(bill_fixture("rtf"))
    texts = BillTexts(tmp_path)
    assert "Сімейного кодексу" in texts.fetch(BillRef(5, "0903"))
