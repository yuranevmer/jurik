import httpx
import pytest

from jurik.model import BillRef
from jurik.rada.billtext import (
    IX_DOWNLOAD_URL,
    BillTexts,
    billinfo_documents,
    csv_documents,
    detect,
    is_text_type,
    paragraphs_from_lines,
    rtf_to_markdown,
    to_markdown,
)
from jurik.rada.client import RadaClient


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
    from jurik.rada.index import read_billinfo

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


def test_fetch_failure_not_cached(tmp_path, rada_cache):
    messages = []
    texts, _ = make_texts(tmp_path, rada_cache, lambda r: httpx.Response(404))
    texts._echo = messages.append
    assert texts.fetch(BillRef(5, "0903")) is None
    assert texts.cached(BillRef(5, "0903")) is None
    assert "HTTP 404" in messages[0]


def test_fetch_doc_is_cached_but_unavailable(tmp_path, rada_cache, bill_fixture):
    texts, calls = make_texts(
        tmp_path, rada_cache, lambda r: httpx.Response(200, content=bill_fixture("doc"))
    )
    assert texts.fetch(BillRef(5, "0903")) is None
    assert texts.cached(BillRef(5, "0903")).name == "0903.doc"
    assert texts.fetch(BillRef(5, "0903")) is None
    assert len(calls) == 1
