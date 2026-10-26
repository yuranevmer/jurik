import json
from datetime import date

import pytest

from jurik.build.history import BuildError, CodeSource
from jurik.build.sources import cached_tree_loader, latest_prints, sources_from_cache
from jurik.config import Code
from jurik.zakon.http import ZakonClient, write_gz

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України")
CARD = {
    "nreg": "2947-14",
    "nazva": "Сімейний кодекс України",
    "orgdat": 20020110,
    "eds": [{"datred": 20020110, "pidstava": ""}, {"datred": 20030101, "pidstava": "407-15"}],
}


def test_sources_trees_and_latest_prints_from_cache(tmp_path, fixtures_dir):
    html = (fixtures_dir / "zakon" / "2947-14_ed20020110_print.html").read_text("utf-8")
    with ZakonClient(tmp_path) as client:
        write_gz(client.card_path("2947-14"), json.dumps(CARD))
        write_gz(client.edition_path("2947-14", "20020110"), html)
        with pytest.raises(BuildError, match=r"1 edition\(s\) not cached \(ed20030101\)"):
            sources_from_cache([FAMILY], client)
        write_gz(client.edition_path("2947-14", "20030101"), html + "<!-- latest -->")
        [src] = sources_from_cache([FAMILY], client)
        assert [e.stamp for e in src.editions["2947-14"]] == ["20020110", "20030101"]
        tree = cached_tree_loader(client)(src.editions["2947-14"][0])
        assert tree.zakon_id == "2947-14" and tree.units
        prints = latest_prints([src], client)
    assert prints == {"family": [html + "<!-- latest -->"]}


def test_latest_prints_skips_repealed_codes(tmp_path):
    repealed = Code("old", ("1-01",), "Старий кодекс", repealed=date(2020, 1, 1))
    with ZakonClient(tmp_path) as client:
        assert latest_prints([CodeSource(repealed, {"1-01": []})], client) == {}
