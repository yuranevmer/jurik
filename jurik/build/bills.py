"""Files of bills that did not become law: ``bills/<conv>/<n>.md`` on their branches."""

from __future__ import annotations

from jurik.build.message import status_label
from jurik.build.refs import ref_safe
from jurik.convocation import label
from jurik.model import BillRef, LawMeta

UNAVAILABLE = "Текст недоступний."


def bill_path(bill: BillRef) -> str:
    """``bills/<conv>/<n>.md`` with the number spelled as in the branch name (ASCII, like
    every other path in the repository): ``1234-д`` → ``bills/4/1234-d.md``."""
    return f"bills/{bill.convocation}/{ref_safe(bill.number)}.md"


def bill_markdown(meta: LawMeta, text: str | None) -> str:
    """``bills/<conv>/<n>.md``: heading, metadata list, then the text or a stub with a link."""
    assert meta.bill is not None
    lines = [f"# Законопроект № {meta.bill.number} ({label(meta.bill.convocation)} скликання)", ""]
    if meta.law_title:
        lines += [" ".join(meta.law_title.split()), ""]
    if meta.registered:
        lines.append(f"- Зареєстровано: {meta.registered.isoformat()}")
    if meta.initiators:
        lines.append(f"- Ініціатори: {', '.join(p.name for p in meta.initiators)}")
    status = status_label(meta.status)
    if meta.phase_date:
        status += f" ({meta.phase_date.isoformat()})"
    lines.append(f"- Статус: {status}")
    if meta.card_url:
        lines.append(f"- Картка: {meta.card_url}")
    lines += ["", "---", ""]
    if text and text.strip():
        lines.append(text.strip())
    else:
        stub = UNAVAILABLE
        if meta.card_url:
            stub += f" Див. картку законопроекту: {meta.card_url}"
        lines.append(stub)
    return "\n".join(lines) + "\n"
