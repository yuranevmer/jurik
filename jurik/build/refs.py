"""Branch names: ``main`` and the bills' ``bill/<convocation>/<number>``, ``bill/law/<nreg>``."""

from __future__ import annotations

import re

from jurik.errors import JurikError
from jurik.model import BillRef
from jurik.translit import transliterate

MAIN = "main"
BILL_PREFIX = "bill"

# characters git forbids in a ref component (see git-check-ref-format)
_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]+")


class RefError(JurikError):
    """No usable git ref name can be made from a bill or law number."""


def ref_safe(s: str) -> str:
    """One ref component from ``s``: Cyrillic transliterated, lowercase (refs live as files on
    case-insensitive file systems), ``/`` → ``_``, forbidden characters and sequences
    (``..``, ``@{``, spaces, leading/trailing dots, ``.lock``) removed."""
    out = transliterate(s.strip()).lower().replace("/", "_")
    out = _FORBIDDEN.sub("-", out)
    out = re.sub(r"\.{2,}", ".", out).replace("@{", "@")
    while out.endswith(".lock"):
        out = out[: -len(".lock")]
    out = out.strip(".-")
    if not out or out == "@":
        raise RefError(f"cannot make a ref name from {s!r}")
    return out


def bill_ref(bill: BillRef) -> str:
    return f"{BILL_PREFIX}/{bill.convocation}/{ref_safe(bill.number)}"


def law_ref(law_number: str) -> str:
    """Branch of a law whose bill is not in the open data (keyed by its nreg)."""
    return f"{BILL_PREFIX}/law/{ref_safe(law_number)}"
