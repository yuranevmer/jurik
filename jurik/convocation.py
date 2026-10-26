"""Convocations of the Verkhovna Rada: numbers, Roman numerals and nreg suffixes.

A law adopted by convocation ``n`` (III–IX) has the nreg suffix ``n + 11`` (``229-20`` is
``229-IX``; III convocation laws are displayed both ``-III`` and ``-XIV``). Older acts with
a suffix up to 12 are displayed with that suffix in Roman numerals (``1234-12`` → ``-XII``).
"""

from __future__ import annotations

FIRST, LAST = 3, 9  # convocations with open data (III–IX)
NREG_OFFSET = 11  # nreg suffix of a law = its convocation + 11

_NUMERALS = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman(n: int) -> str:
    """``9`` → ``IX`` (1..39)."""
    out = ""
    for value, sym in _NUMERALS:
        while n >= value:
            out += sym
            n -= value
    return out


# convocation number → Roman numeral, III–IX
ROMAN = {n: roman(n) for n in range(FIRST, LAST + 1)}
# Roman numeral → convocation number, III–IX
ROMAN_CONVOCATION = {r: n for n, r in ROMAN.items()}
# nreg suffix of a law → displayed Roman suffix: 14 → III … 20 → IX
NREG_SUFFIX_ROMAN = {n + NREG_OFFSET: r for n, r in ROMAN.items()}
# displayed Roman suffix of an act number → nreg suffix (III laws appear as -XIV and -III)
ROMAN_TO_NREG = {"XIV": FIRST + NREG_OFFSET} | {r: n + NREG_OFFSET for n, r in ROMAN.items()}


def label(n: int) -> str:
    """Roman numeral of convocation ``n`` for Ukrainian text, the number if outside III–IX."""
    return ROMAN.get(n, str(n))
