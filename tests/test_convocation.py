import pytest

from jurik.convocation import (
    NREG_SUFFIX_ROMAN,
    ROMAN,
    ROMAN_CONVOCATION,
    ROMAN_TO_NREG,
    label,
    roman,
)


@pytest.mark.parametrize(("n", "r"), [(1, "I"), (4, "IV"), (9, "IX"), (12, "XII"), (39, "XXXIX")])
def test_roman(n, r):
    assert roman(n) == r


def test_tables_agree():
    assert ROMAN == {3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX"}
    assert ROMAN_CONVOCATION["VIII"] == 8
    assert {n + 11: r for n, r in ROMAN.items()} == NREG_SUFFIX_ROMAN
    # III convocation laws are displayed both -III and -XIV
    assert ROMAN_TO_NREG["XIV"] == ROMAN_TO_NREG["III"] == 14
    assert ROMAN_TO_NREG["IX"] == 20


def test_label():
    assert label(4) == "IV"
    assert label(2) == "2"  # outside III–IX: the number
