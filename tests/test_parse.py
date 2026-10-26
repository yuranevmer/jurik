import json

from jurik.config import Code
from jurik.parse import EditionCheck, failures_report, parse_code
from jurik.zakon.http import ZakonClient, write_gz

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України")
CARD = {
    "nreg": "2947-14",
    "nazva": "Сімейний кодекс України",
    "orgdat": 20020110,
    "eds": [{"datred": 20020110, "pidstava": ""}, {"datred": 20030101, "pidstava": "407-15"}],
}


def family_cache(tmp_path, fixtures_dir, editions=("20020110", "20030101")):
    client = ZakonClient(tmp_path)
    write_gz(client.card_path("2947-14"), json.dumps(CARD))
    html = (fixtures_dir / "zakon" / "2947-14_ed20020110_print.html").read_text("utf-8")
    for d in editions:
        write_gz(client.edition_path("2947-14", d), html)
    return client


def test_parse_latest_edition_ok(tmp_path, fixtures_dir):
    with family_cache(tmp_path, fixtures_dir) as client:
        lines = []
        [check] = parse_code(FAMILY, client, echo=lines.append)
    assert (check.edition, check.ok, check.not_cached) == ("20030101", True, False)
    assert lines[0].startswith("family 2947-14 ed20030101: ")
    assert failures_report([check]) == "# Parse failures\n\nNo failures in 1 edition(s).\n"


def test_parse_uncached_edition_is_not_cached(tmp_path, fixtures_dir):
    with family_cache(tmp_path, fixtures_dir, editions=("20020110",)) as client:
        checks = parse_code(FAMILY, client, all_editions=True, echo=lambda _: None)
    assert [(c.edition, c.ok, c.not_cached) for c in checks] == [
        ("20020110", True, False),
        ("20030101", False, True),
    ]
    assert checks[1].error.startswith("NotCachedError: 2947-14 ed20030101 not cached")


def test_parse_unreadable_edition_is_a_failure_not_uncached(tmp_path, fixtures_dir):
    with family_cache(tmp_path, fixtures_dir) as client:
        client.edition_path("2947-14", "20030101").write_bytes(b"not gzip")
        [check] = parse_code(FAMILY, client, edition="20030101", echo=lambda _: None)
    assert check.error.startswith("ZakonError: unreadable cache")
    assert not check.not_cached
    report = failures_report([check])
    assert "1 of 1 edition(s) failed." in report
    assert "## family 2947-14 ed20030101" in report


def test_parse_missing_card_and_unknown_edition(tmp_path, fixtures_dir):
    with ZakonClient(tmp_path) as client:
        [check] = parse_code(FAMILY, client, echo=lambda _: None)
    assert check.edition == "card" and "not cached" in check.error
    with family_cache(tmp_path, fixtures_dir) as client:
        [check] = parse_code(FAMILY, client, edition="19990101", echo=lambda _: None)
    assert (check.edition, check.error) == ("19990101", "no such edition")


def test_failures_report_lists_diffs():
    check = EditionCheck("family", "2947-14", "20020110", error="TreeError: duplicate")
    assert "- error: TreeError: duplicate" in failures_report([check])
