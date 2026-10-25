from datetime import date

import pytest

from jurik.build.message import (
    MessageError,
    format_message,
    law_trailers,
    parse_trailers,
    step_message,
    subject_for,
)
from jurik.model import BillRef, Edition, LawMeta, Person


def meta(**kw):
    base = dict(
        law_number="2856-17",
        law_title="Про внесення змін до Податкового кодексу України",
        bill=BillRef(6, "7101"),
        initiators=[Person("Микола Азаров", "azarov-mykola-yanovych")],
        registered=date(2010, 10, 1),
        adopted=date(2010, 12, 23),
        votes=(268, 2, 1),
        signed=date(2010, 12, 30),
        status="adopted",
        display_number="2856-VI",
    )
    base.update(kw)
    return LawMeta(**base)


def edition(zakon_id="2755-17", d=date(2011, 1, 1), bases=("2856-17",)):
    return Edition(zakon_id, d, list(bases), "law", f"https://x/{zakon_id}/ed{d:%Y%m%d}")


def test_format_message_requires_in_force():
    with pytest.raises(MessageError):
        format_message("subject", [("Reason", "law")])
    text = format_message("Subj", [("In-force", "2011-01-01"), ("Reason", "law")], "body")
    assert text == "Subj\n\nbody\n\nIn-force: 2011-01-01\nReason: law\n"
    assert parse_trailers(text) == [("In-force", "2011-01-01"), ("Reason", "law")]


def test_law_message_like_plan_example():
    text = step_message(
        reason="law",
        in_force=date(2011, 1, 1),
        bases=["2856-17"],
        titles=["Податковий кодекс України"],
        editions=[edition()],
        metas={"2856-17": meta()},
    )
    lines = text.splitlines()
    assert lines[0] == "Закон № 2856-VI: Про внесення змін до Податкового кодексу України"
    trailers = dict(parse_trailers(text))
    assert trailers["Law"] == "2856-VI"
    assert trailers["Bill"] == "6/7101"
    assert trailers["Adopted"] == "2010-12-23, за 268 / проти 2 / утрималися 1"
    assert trailers["Signed"] == "2010-12-30"
    assert trailers["In-force"] == "2011-01-01"
    assert trailers["Reason"] == "law"
    assert trailers["Edition"] == "2755-17/ed20110101"
    assert trailers["Source"] == "https://zakon.rada.gov.ua/laws/show/2856-17"


def test_adopted_without_votes_and_unknown_law():
    out = dict(law_trailers(["2856-17"], {"2856-17": meta(votes=None, signed=None)}))
    assert out["Adopted"] == "2010-12-23"
    assert "Signed" not in out
    # not in the index: displayed number from nreg, no Bill
    assert law_trailers(["4834-17"], {}) == [("Law", "4834-VI")]


def test_multiple_laws_one_trailer_each_no_adopted():
    metas = {"71-19": meta(display_number="71-VIII", law_title="A"), "79-19": None}
    trailers = law_trailers(["71-19", "79-19"], metas)
    assert [k for k, _ in trailers] == ["Law", "Bill", "Law"]
    subject = subject_for("multiple", ["71-19", "79-19"], ["Податковий кодекс України"], metas)
    assert subject == "Закони № 71-VIII, № 79-VIII (Податковий кодекс України)"


def test_subjects_of_linear_reasons():
    titles = ["Конституція України"]
    assert subject_for("initial", [], titles, {}) == "Конституція України: перша редакція"
    assert subject_for("unknown", [], titles, {}) == "Конституція України: редакція без підстави"
    assert subject_for("repealed", [], titles, {}).endswith("втрата чинності")
    assert "Конституційного Суду" in subject_for("court", ["v020p710-10"], titles, {})
    court = dict(law_trailers(["v020p710-10"], {}))
    assert court == {"Basis": "v020p710-10"}
