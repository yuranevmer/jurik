"""Texts of bills: pick the bill text document, download it once, convert it to markdown.

Where the text lives (see ``docs/spike-sources.md``):

- III–VIII: ``bills_documents-skl<n>.csv``, ``type=source`` rows whose ``document_type`` is
  «Проект Закону» / «Проект Кодексу» (also «(на заміну)», «(доопрацьований)»); the
  ``webproc34`` link returns html (cp1251), rtf or Word 97 ``.doc`` whatever its file name says;
- IX: ``billinfo-skl9.json``, ``documents.source[kind=«Проєкт Закону»|«Проєкт Кодексу»]``
  ``.docFiles[type=kind]`` (pdf or docx), downloaded in chunks from
  ``itd.rada.gov.ua/billinfo/api/file/download/`` (headers ``X-File-Id``, ``X-Current-Chunk``;
  the response carries ``Size`` and ``ChunkSize``).

When a bill has several text documents the latest one wins (a replacement or reworked text
supersedes the original). Downloads are cached raw as ``<cache>/rada/bills/<conv>/<n>.<ext>``
with the extension of the detected format. Word 97 ``.doc`` has no pure-Python converter, so
its text is unavailable (``fetch`` returns ``None``) although the file is cached.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import html as lxml_html
from pypdf import PdfReader
from striprtf.striprtf import rtf_to_text

from jurik.http import write_atomic
from jurik.model import BillRef, kyiv_today
from jurik.rada.client import BILLINFO, RadaClient, RadaError, cached_file
from jurik.rada.index import norm_bill_number, parse_day, read_billinfo, read_csv

IX_DOWNLOAD_URL = "https://itd.rada.gov.ua/billinfo/api/file/download/"
TEXT_TYPE_RE = re.compile(
    r"^про[еє]кт\s+(закону|кодексу)(\s*\((на\s+заміну|доопрацьований)\))?$", re.IGNORECASE
)
MAX_CHUNKS = 200
# seconds between bill document downloads: one small request per bill (several per IX
# chunked file), so a gentler pace than the 2.5 s of the bulk open data files
BILL_TEXT_INTERVAL = 1.0

Format = Literal["pdf", "docx", "rtf", "doc", "html", "txt"]


@dataclass(frozen=True)
class BillDoc:
    """Where the text of a bill can be downloaded from."""

    url: str = ""  # III–VIII: webproc34 link
    file_id: str = ""  # IX: id for the chunked download API
    name: str = ""  # file name as listed (IX)
    when: date | None = None


def is_text_type(kind: str) -> bool:
    return bool(TEXT_TYPE_RE.match(" ".join((kind or "").split())))


def _latest(docs: Iterable[tuple[date | None, int, BillDoc]]) -> BillDoc | None:
    best = max(docs, key=lambda t: (t[0] or date.min, t[1]), default=None)
    return best[2] if best else None


def csv_documents(cache: Path, n: int) -> dict[str, BillDoc]:
    """III–VIII: normalized bill number → its latest text document."""
    paths = [cached_file(cache, n, f"bills_{k}-skl{n}.csv") for k in ("main", "documents")]
    numbers = {r["bill_id"]: r["number"] for r in read_csv(paths[0], ("bill_id", "number"))}
    found: dict[str, list[tuple[date | None, int, BillDoc]]] = {}
    rows = read_csv(paths[1], ("bill_id", "type", "document_date", "document_type", "document_url"))
    for i, r in enumerate(rows):
        number = numbers.get(r["bill_id"])
        if not number or r["type"] != "source" or not is_text_type(r["document_type"]):
            continue
        when = parse_day(r["document_date"])
        doc = BillDoc(url=r["document_url"].strip(), when=when)
        found.setdefault(norm_bill_number(number), []).append((when, i, doc))
    return {k: d for k, v in found.items() if (d := _latest(v))}


def billinfo_documents(bills: Iterable[dict]) -> dict[str, BillDoc]:
    """IX: normalized bill number → its latest text document (docx preferred over pdf)."""
    out: dict[str, BillDoc] = {}
    for b in bills:
        candidates = []
        for i, src in enumerate((b.get("documents") or {}).get("source") or []):
            kind = src.get("kind") or ""
            if not is_text_type(kind):
                continue
            files = [f for f in src.get("docFiles") or [] if f.get("type") == kind and f.get("id")]
            files.sort(key=lambda f: not str(f.get("name", "")).lower().endswith(".docx"))
            if files:
                when = parse_day(src.get("registrationDate"))
                f = files[0]
                doc = BillDoc(file_id=str(f["id"]), name=str(f.get("name") or ""), when=when)
                candidates.append((when, i, doc))
        if (doc := _latest(candidates)) and b.get("registrationNumber"):
            out.setdefault(norm_bill_number(str(b["registrationNumber"])), doc)
    return out


# --- format detection and conversion ------------------------------------------------------


def detect(data: bytes) -> Format | None:
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return "docx" if "word/document.xml" in zf.namelist() else None
        except zipfile.BadZipFile:
            return None
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc"
    sniff = data[:2048].lstrip().lower()
    if sniff.startswith((b"<!doctype", b"<html", b"<?xml", b"<")) or b"<html" in sniff:
        return "html"
    return "txt" if data.strip() else None


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1251", errors="replace")


_SPACES = re.compile(r"[ \t\u00a0]+")
_END = re.compile(r"[.:;!?»\"”)]$")
_START = re.compile(r"^[A-ZА-ЯЄІЇҐ0-9«\"“(—–-]")


def _clean(s: str) -> str:
    return _SPACES.sub(" ", s.replace("\r", "")).strip()


def paragraphs_from_lines(lines: Iterable[str], *, indent_breaks: bool = False) -> list[str]:
    """Join visual lines (pdf, preformatted text) into paragraphs.

    A paragraph ends at a blank line, or when the next line starts like a new paragraph
    (capital, digit, quote, dash) after a line that ends a sentence or is much shorter than
    a full line; with ``indent_breaks`` an indented line also starts a new paragraph
    (typewriter-style ``<pre>`` texts).
    """
    lines = list(lines)
    widths = sorted(len(_clean(ln)) for ln in lines if ln.strip())
    full = widths[int(len(widths) * 0.8)] if widths else 0
    out: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            out.append(_clean(" ".join(cur)))
            cur.clear()

    for raw in lines:
        line = raw.rstrip()
        text = _clean(line)
        if not text:
            flush()
            continue
        if cur:
            indented = indent_breaks and len(line) - len(line.lstrip()) >= 2
            short = not indent_breaks and len(cur[-1]) < 0.6 * full
            if indented or ((_END.search(cur[-1]) or short) and _START.match(text)):
                flush()
        cur.append(text)
    flush()
    return [p for p in out if p]


def docx_to_markdown(data: bytes) -> str:
    """Paragraphs (headings as ``#``) and tables (``| a | b |`` rows) in document order."""
    document = docx.Document(io.BytesIO(data))
    blocks: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = _clean(item.text)
            if not text:
                continue
            style = (item.style.name if item.style is not None else "") or ""
            m = re.match(r"Heading (\d)", style)
            blocks.append(f"{'#' * int(m.group(1))} {text}" if m else text)
        elif isinstance(item, Table):
            rows = []
            for row in item.rows:
                cells = [_clean(c.text).replace("|", "\\|") for c in row.cells]
                if any(cells):
                    rows.append("| " + " | ".join(cells) + " |")
            if rows:
                blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def pdf_to_markdown(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines += [ln for ln in text.splitlines() if not re.fullmatch(r"\s*\d{1,3}\s*", ln)]
    return "\n\n".join(paragraphs_from_lines(lines))


def rtf_to_markdown(data: bytes) -> str:
    m = re.search(rb"\\ansicpg(\d+)", data[:4096])
    encoding = f"cp{m.group(1).decode()}" if m else "cp1251"
    try:
        "".encode(encoding)
    except LookupError:
        encoding = "cp1251"
    # RTF is 7-bit with \'xx escapes, but raw 8-bit bytes are in the same code page
    text = rtf_to_text(data.decode(encoding, errors="replace"), encoding=encoding, errors="replace")
    lines = text.splitlines()
    filled = [ln for ln in lines if ln.strip()]
    if sum(ln.startswith("   ") for ln in filled) > 0.3 * len(filled):
        # typewriter text: one paragraph mark per visual line, paragraphs start indented
        return "\n\n".join(paragraphs_from_lines(lines, indent_breaks=True))
    return "\n\n".join(p for p in (_clean(ln) for ln in lines) if p)


_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"}


def html_to_markdown(data: bytes) -> str:
    """``<pre>`` texts are reflowed by indentation; otherwise one paragraph per block tag."""
    m = re.search(rb"charset=[\"']?([\w-]+)", data[:4096], re.IGNORECASE)
    try:
        text = data.decode(m.group(1).decode()) if m else _decode(data)
    except (LookupError, UnicodeDecodeError):
        text = _decode(data)
    root = lxml_html.fromstring(text)
    for bad in root.xpath("//script|//style|//head"):
        bad.getparent().remove(bad)
    pres = root.xpath("//pre")
    if pres:
        lines = [ln for pre in pres for ln in pre.text_content().splitlines()]
        return "\n\n".join(paragraphs_from_lines(lines, indent_breaks=True))
    blocks = []
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.lower() in _BLOCK_TAGS:
            if any(isinstance(c.tag, str) and c.tag.lower() in _BLOCK_TAGS for c in el):
                continue
            if t := _clean(el.text_content()):
                blocks.append(t)
    if not blocks:
        blocks = paragraphs_from_lines(root.text_content().splitlines())
    return "\n\n".join(blocks)


CONVERTERS: dict[Format, Callable[[bytes], str]] = {
    "docx": docx_to_markdown,
    "pdf": pdf_to_markdown,
    "rtf": rtf_to_markdown,
    "html": html_to_markdown,
    "txt": lambda data: "\n\n".join(paragraphs_from_lines(_decode(data).splitlines())),
}


def to_markdown(data: bytes) -> str | None:
    """Markdown text of a downloaded document; ``None`` for ``.doc``, unknown or empty."""
    fmt = detect(data)
    convert = CONVERTERS.get(fmt) if fmt else None
    if convert is None:
        return None
    try:
        text = convert(data).strip()
    except Exception:  # a broken file from the source: its text is unavailable
        return None
    return text or None


# --- downloading --------------------------------------------------------------------------


# a failed download leaves ``<stem>.failed`` («YYYY-MM-DD reason»): the bill is not asked for
# again until this many days have passed (old convocations' document host can be gone for
# good, and a daily build must not spend minutes on retries for every such bill)
FAILED_SUFFIX = ".failed"
RETRY_FAILED_AFTER = timedelta(days=7)

_UNSAFE_STEM = re.compile(r"[/\\\x00-\x1f\x7f]")


def file_stem(number: str) -> str:
    """File name stem of a bill text in ``<cache>/rada/bills/<conv>/``.

    Path separators and control characters become ``_``, leading dots become ``_`` (no
    hidden files, no ``..``) and an empty number becomes ``_``, so the file always stays
    inside the convocation folder. Ordinary numbers (digits, letters, ``-``) are unchanged:
    existing cache files are named by this stem.
    """
    stem = _UNSAFE_STEM.sub("_", number.strip())
    dots = len(stem) - len(stem.lstrip("."))
    return ("_" * dots + stem[dots:]) or "_"


class BillTexts:
    """``fetch(bill)`` = markdown text of a bill or ``None`` (no document, ``.doc``, failed
    download). Documents of a convocation are looked up lazily from the cached open data."""

    def __init__(
        self,
        cache: Path,
        client: RadaClient | None = None,
        *,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self.cache = Path(cache)
        self.dir = self.cache / "rada" / "bills"
        self._client = client
        self._echo = echo or (lambda _msg: None)
        self._docs: dict[int, dict[str, BillDoc]] = {}
        self._no_docs: set[int] = set()  # convocations whose document list is unavailable
        self._files: dict[int, dict[str, Path]] = {}  # convocation -> stem -> cached file
        self._down: set[str] = set()  # hosts that did not answer in this run
        self.today = kyiv_today()

    def _http(self) -> RadaClient:
        if self._client is None:
            self._client = RadaClient(self.cache, min_interval=BILL_TEXT_INTERVAL)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> BillTexts:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def document(self, bill: BillRef) -> BillDoc | None:
        n = bill.convocation
        if n not in self._docs:
            if n == 9:
                path = cached_file(self.cache, 9, BILLINFO)
                self._docs[n] = billinfo_documents(read_billinfo(path))
            else:
                self._docs[n] = csv_documents(self.cache, n)
        return self._docs[n].get(norm_bill_number(bill.number))

    def _listing(self, convocation: int) -> dict[str, Path]:
        if convocation not in self._files:
            folder = self.dir / str(convocation)
            files: dict[str, Path] = {}
            if folder.is_dir():
                for p in sorted(folder.iterdir()):
                    if p.suffix and p.suffix != FAILED_SUFFIX:
                        files.setdefault(p.stem, p)
            self._files[convocation] = files
        return self._files[convocation]

    def cached(self, bill: BillRef) -> Path | None:
        return self._listing(bill.convocation).get(file_stem(bill.number))

    def _failed_path(self, bill: BillRef) -> Path:
        return self.dir / str(bill.convocation) / f"{file_stem(bill.number)}{FAILED_SUFFIX}"

    def _recently_failed(self, bill: BillRef) -> bool:
        try:
            day = date.fromisoformat(self._failed_path(bill).read_text("utf-8")[:10])
        except (OSError, ValueError):
            return False
        return self.today - day < RETRY_FAILED_AFTER

    def _record_failure(self, bill: BillRef, url: str, error: str) -> None:
        if "transport error" in error or "timeout" in error:
            self._down.add(urlsplit(url).netloc)
        write_atomic(self._failed_path(bill), f"{self.today.isoformat()} {error}\n".encode())

    def download(self, doc: BillDoc) -> bytes:
        http = self._http()
        if doc.url:
            return http.get(doc.url)
        parts: list[bytes] = []
        size = None
        for chunk in range(MAX_CHUNKS):
            headers = {"X-File-Id": doc.file_id, "X-Current-Chunk": str(chunk)}
            resp = http.request(IX_DOWNLOAD_URL, headers=headers)
            if not resp.content:
                break
            parts.append(resp.content)
            try:
                size = int(resp.headers.get("Size", ""))
            except ValueError:
                break  # no size: a single chunk
            if sum(map(len, parts)) >= size:
                break
        data = b"".join(parts)
        if size is not None and len(data) != size:
            raise RadaError(f"file {doc.file_id}: got {len(data)} of {size} bytes")
        return data

    def fetch(self, bill: BillRef) -> str | None:
        path = self.cached(bill)
        if path is None:
            if bill.convocation in self._no_docs:
                return None
            try:
                doc = self.document(bill)
            except RadaError as e:
                self._no_docs.add(bill.convocation)
                self._echo(f"warning: bill texts of convocation {bill.convocation}: {e}")
                return None
            if doc is None or self._recently_failed(bill):
                return None
            url = doc.url or IX_DOWNLOAD_URL
            if urlsplit(url).netloc in self._down:
                return None
            try:
                data = self.download(doc)
            except RadaError as e:
                self._echo(f"warning: bill {bill.convocation}/{bill.number}: {e}")
                self._record_failure(bill, url, str(e))
                return None
            fmt = detect(data)
            if fmt is None:
                self._echo(f"warning: bill {bill.convocation}/{bill.number}: empty document")
                return None
            path = self.dir / str(bill.convocation) / f"{file_stem(bill.number)}.{fmt}"
            write_atomic(path, data)
            self._listing(bill.convocation)[file_stem(bill.number)] = path
            self._failed_path(bill).unlink(missing_ok=True)
        text = to_markdown(path.read_bytes())
        if text is None:
            self._echo(f"note: bill {bill.convocation}/{bill.number}: no text from {path.name}")
        return text
