"""Ukrainian → Latin transliteration (КМУ resolution № 55 of 27.01.2010) and slugs."""

from __future__ import annotations

import re

# letter → (at word start, elsewhere)
_TABLE: dict[str, tuple[str, str]] = {
    "а": ("a", "a"),
    "б": ("b", "b"),
    "в": ("v", "v"),
    "г": ("h", "h"),
    "ґ": ("g", "g"),
    "д": ("d", "d"),
    "е": ("e", "e"),
    "є": ("ye", "ie"),
    "ж": ("zh", "zh"),
    "з": ("z", "z"),
    "и": ("y", "y"),
    "і": ("i", "i"),
    "ї": ("yi", "i"),
    "й": ("y", "i"),
    "к": ("k", "k"),
    "л": ("l", "l"),
    "м": ("m", "m"),
    "н": ("n", "n"),
    "о": ("o", "o"),
    "п": ("p", "p"),
    "р": ("r", "r"),
    "с": ("s", "s"),
    "т": ("t", "t"),
    "у": ("u", "u"),
    "ф": ("f", "f"),
    "х": ("kh", "kh"),
    "ц": ("ts", "ts"),
    "ч": ("ch", "ch"),
    "ш": ("sh", "sh"),
    "щ": ("shch", "shch"),
    "ь": ("", ""),
    "ю": ("yu", "iu"),
    "я": ("ya", "ia"),
}
# apostrophes are dropped, not treated as word separators
_APOSTROPHES = "'’ʼ`‘"

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_RE = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def transliterate(text: str) -> str:
    """Transliterate Ukrainian text; non-Cyrillic characters pass through unchanged."""
    out: list[str] = []
    prev_letter = False
    prev = ""
    for ch in text:
        if ch in _APOSTROPHES:
            continue  # apostrophe does not break a word
        low = ch.lower()
        if low in _TABLE:
            start, mid = _TABLE[low]
            # «зг» → «zgh» to distinguish from «ж»
            if low == "г" and prev == "з":
                mid = start = "gh"
            lat = mid if prev_letter else start
            if ch != low and lat:
                lat = lat[0].upper() + lat[1:]
            out.append(lat)
            prev_letter = True
        else:
            out.append(ch)
            prev_letter = ch.isalpha()
        prev = low
    return "".join(out)


def slug(text: str, max_len: int = 40) -> str:
    """Lowercase transliterated slug: non-alphanumerics → '-', cut at a word boundary."""
    s = re.sub(r"[^a-z0-9]+", "-", transliterate(text).lower()).strip("-")
    if len(s) <= max_len:
        return s
    cut = s[: max_len + 1]
    if "-" in cut:
        return cut[: cut.rindex("-")].strip("-")
    return s[:max_len]


def roman_to_int(s: str) -> int:
    s = s.strip().upper()
    if not s or not _ROMAN_RE.fullmatch(s):
        raise ValueError(f"not a roman numeral: {s!r}")
    total = 0
    for i, ch in enumerate(s):
        value = _ROMAN[ch]
        if i + 1 < len(s) and _ROMAN[s[i + 1]] > value:
            total -= value
        else:
            total += value
    return total
