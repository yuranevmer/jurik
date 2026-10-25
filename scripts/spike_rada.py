"""One-off spike for Task 2: explore data.rada.gov.ua open data and zakon edition history.

Not part of the jurik package. Downloads into a cache dir (default ``cache/spike``) and prints
the facts recorded in ``docs/spike-sources.md``:

- dataset manifests (``/ogd/<group>/meta.json``) and the real file URLs;
- bill <-> law link coverage per convocation (``bills_acts-skl<N>.csv``);
- per code: editions from the OpenData JSON card (``/laws/card/<nreg>.json``), the earliest
  edition, multi-basis editions, bases repeated across editions, court bases, and how many
  law bases resolve to a bill via the zpr acts tables.

Usage::

    uv run python scripts/spike_rada.py [--cache cache/spike] [--codes 2947-14 2755-17 ...]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import httpx

DATA = "https://data.rada.gov.ua"
OPENDATA_UA = "OpenData"  # required by data.rada.gov.ua/laws/* API, otherwise 302 to a login page
DELAY = 2.5

# Display suffix of a law number -> nreg (zakon id) suffix. Only convocations III-IX are covered
# by zpr open data. III convocation laws are displayed both as "-XIV" (1998-2000) and "-III".
# Note: display "VIII" is ambiguous (Soviet VIII convocation, nreg -08, vs
# VIII convocation 2014-2019, nreg -19), so law numbers must be keyed by nreg, not display form.
ROMAN_TO_NREG = {"XIV": 14, "III": 14, "IV": 15, "V": 16, "VI": 17, "VII": 18, "VIII": 19, "IX": 20}
CONVOCATIONS = range(3, 10)
DEFAULT_CODES = ["254к/96-вр", "2947-14", "2755-17", "435-15", "322-08", "5464-10", "213/95-вр"]

_last_request = 0.0


def fetch(url: str, path: Path, ua: str | None = None) -> bytes:
    """Download ``url`` into ``path`` once; later calls read the file."""
    global _last_request
    if path.exists():
        return path.read_bytes()
    wait = DELAY - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": ua} if ua else {}
    resp = httpx.get(url, headers=headers, timeout=120, follow_redirects=False)
    _last_request = time.monotonic()
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content


def decode(raw: bytes) -> str:
    """Catalog JSON is UTF-8 for most groups but windows-1251 for some (e.g. /ogd/zal/)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def manifests(cache: Path, groups: list[str]) -> None:
    for group in groups:
        target = cache / "meta" / f"{group.replace('/', '_')}.json"
        raw = fetch(f"{DATA}/ogd/{group}/meta.json", target)
        meta = json.loads(decode(raw))
        print(f"\n== /ogd/{group}/  {meta.get('title')}")
        for item in meta.get("item", []):
            if item.get("type") != "data":
                continue
            fmt = str(item.get("format", "")).split(",")[0]
            name = item["name"] if "." in item["name"] else f"{item['name']}.{fmt}"
            url = f"{DATA}{item['path']}{name}"
            date = item.get("pubDate", "")[:10]
            print(f"  {item['id']:<32} {date} {item.get('size')!s:>28}  {url}")


def read_csv(cache: Path, conv: int, name: str) -> list[dict[str, str]]:
    # single-format items are also served as <name>.zip, multi-format ones (bills_main) are not
    url = f"{DATA}/ogd/zpr/skl{conv}/bills_{name}-skl{conv}.csv"
    raw = fetch(url, cache / "zpr" / f"skl{conv}" / f"bills_{name}.csv")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def act_to_nreg(act_number: str) -> str | None:
    """``229-ІХ`` (Cyrillic І/Х happen) -> ``229-20``; None for forms outside III-IX."""
    num = act_number.translate(str.maketrans("ІХ", "IX")).strip()
    m = re.fullmatch(r"(\d+[^-]*)-([IVX]+)", num)
    if not m or m.group(2) not in ROMAN_TO_NREG:
        return None
    return f"{m.group(1)}-{ROMAN_TO_NREG[m.group(2)]}"


def bill_links(cache: Path) -> dict[str, tuple[int, str, str]]:
    """nreg of a law -> (convocation, bill registration number, bill_id)."""
    links: dict[str, tuple[int, str, str]] = {}
    print("\n== bill <-> act coverage (zpr bills_acts + bills_main)")
    for conv in CONVOCATIONS:
        main = {r["bill_id"]: r for r in read_csv(cache, conv, "main")}
        acts = read_csv(cache, conv, "acts")
        signed = [r for r in main.values() if r["currentPhase_title"] == "Закон підписано"]
        with_act = {r["bill_id"] for r in acts}
        unmapped = 0
        for row in acts:
            nreg = act_to_nreg(row["act_number"])
            if nreg is None:
                unmapped += 1
                continue
            bill = main.get(row["bill_id"], {})
            links.setdefault(nreg, (conv, bill.get("number", "?"), row["bill_id"]))
        covered = sum(r["bill_id"] in with_act for r in signed)
        print(
            f"  skl{conv}: bills {len(main):>6}  'Закон підписано' {len(signed):>5}"
            f"  of them with act {covered:>5}  act rows {len(acts):>5}  unmapped numbers {unmapped}"
        )
    # bills_* CSVs of skl9 stopped updating in Jan 2025; billinfo-skl9 is the current full dump
    raw = fetch(f"{DATA}/ogd/zpr/skl9/billinfo-skl9.zip", cache / "zpr" / "skl9" / "billinfo.zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        bills = json.loads(zf.read(zf.namelist()[0]).decode("utf-8"), strict=False)
    added = 0
    for bill in bills:
        nreg = act_to_nreg(bill.get("actNumber") or "")
        if nreg and nreg not in links:
            links[nreg] = (9, bill["registrationNumber"], f"billinfo:{bill['id']}")
            added += 1
    signed = [b for b in bills if b["currentPhase"]["status"] == "Закон підписано"]
    print(
        f"  billinfo-skl9: bills {len(bills)}  'Закон підписано' {len(signed)}"
        f"  with actNumber {sum(bool(b.get('actNumber')) for b in signed)}  new acts vs CSV {added}"
    )
    return links


def code_card(cache: Path, nreg: str) -> dict:
    safe = nreg.replace("/", "_")
    # the slash must stay literal: %2F gives 404
    url = f"{DATA}/laws/card/{quote(nreg, safe='/')}.json"
    return json.loads(fetch(url, cache / "cards" / f"{safe}.json", ua=OPENDATA_UA))


def analyse_code(cache: Path, nreg: str, links: dict[str, tuple[int, str, str]]) -> None:
    card = code_card(cache, nreg)
    eds = sorted(card["eds"], key=lambda e: e["datred"])
    dated = [e for e in eds if e["datred"] < 30000000]
    undated = [e for e in eds if e["datred"] >= 30000000]
    bases = [tuple(filter(None, e.get("pidstava", "").split(","))) for e in dated]
    multi = [b for b in bases if len(b) > 1]
    repeated = {b: n for b, n in Counter(bases).items() if b and n > 1}
    court = [b for b in bases if any(re.match(r"v\w+p710-\d\d", x) for x in b)]
    law_bases = {x for b in bases for x in b if not re.match(r"v\w+p710-\d\d", x)}
    found = {x for x in law_bases if x in links}
    pre3 = {x for x in law_bases if not re.search(r"-(1[4-9]|20)$", x)}
    first = dated[0]
    print(f"\n== {nreg}  {card['nazva']}  ({card.get('orgnum')} від {card.get('orgdat')})")
    print(f"  editions {len(eds)} (dated {len(dated)}, undated/conditional {len(undated)})")
    basis = first.get("pidstava")
    print(f"  earliest edition ed{first['datred']} podid={first['podid']} basis={basis!r}")
    print(f"  history head: {card['history'][:160]}")
    print(f"  multi-basis editions {len(multi)}, e.g. {multi[:1]}")
    print(f"  bases in >1 edition {len(repeated)}, e.g. {list(repeated.items())[:2]}")
    print(f"  court-basis editions {len(court)}, e.g. {court[:1]}")
    missing = sorted(law_bases - found - pre3)[:8]
    print(
        f"  distinct law bases {len(law_bases)}: with bill in zpr {len(found)},"
        f" outside III-IX (pre-1998 / II conv) {len(pre3)}, missing {missing}"
    )
    print(f"  card projs (bill of the code itself): {card.get('projs')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path("cache/spike"))
    parser.add_argument("--codes", nargs="*", default=DEFAULT_CODES)
    args = parser.parse_args()
    manifests(args.cache, ["zpr/skl9", "zpr/skl5", "zal/ppz/skl9", "zak/laws/docs"])
    links = bill_links(args.cache)
    for nreg in args.codes:
        analyse_code(args.cache, nreg, links)


if __name__ == "__main__":
    main()
