import pytest
from gitrepo import git_ok

from jurik.build.refs import RefError, bill_ref, law_ref, ref_safe
from jurik.model import BillRef


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("254к/96-ВР", "254k_96-vr"),
        ("1234-д", "1234-d"),
        ("a..b", "a.b"),
        ("x@{y", "x@y"),
        ("a b", "a-b"),
        ("1234.", "1234"),
        ("abc.lock", "abc"),
        ("a~^:?*[\\b", "a-b"),
    ],
)
def test_ref_safe(raw, safe):
    assert ref_safe(raw) == safe


def test_ref_safe_empty_raises():
    with pytest.raises(RefError):
        ref_safe("..")


def test_bill_and_law_refs():
    assert bill_ref(BillRef(9, "1234-д")) == "bill/9/1234-d"
    assert bill_ref(BillRef(4, "1234/П")) == "bill/4/1234_p"
    assert law_ref("2147а-19") == "bill/law/2147a-19"
    assert law_ref("254к/96-вр") == "bill/law/254k_96-vr"


def test_refs_pass_git_check_ref_format(tmp_path):
    for ref in (bill_ref(BillRef(9, "15112-д")), law_ref("254к/96-ВР"), law_ref("a..b")):
        assert git_ok(tmp_path, "check-ref-format", f"refs/heads/{ref}"), ref
