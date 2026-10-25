import gzip

import pytest
from click.testing import CliRunner

from jurik import cli
from jurik.cli import main


@pytest.mark.parametrize("cmd", [["build"], ["report"], ["export", "--out", "x"]])
def test_stub_commands_exist(cmd):
    result = CliRunner().invoke(main, cmd)
    assert result.exit_code == 1
    assert "not implemented" in result.output


def test_help_lists_commands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for name in ("fetch", "rada", "parse", "build", "report", "export"):
        assert name in result.output


class FakeRadaClient:
    def __init__(self):
        self.fetched: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def fetch_convocation(self, n, echo=None):
        self.fetched.append(n)
        return []


def test_cli_rada(rada_cache, fixtures_dir, monkeypatch):
    fake = FakeRadaClient()
    monkeypatch.setattr(cli, "make_rada_client", lambda cache, refetch=False: fake)
    card = rada_cache / "zakon" / "2947-14" / "card.json.gz"
    card.parent.mkdir(parents=True)
    card.write_bytes(gzip.compress((fixtures_dir / "zakon" / "card-family.json").read_bytes()))
    monkeypatch.chdir(rada_cache.parent)
    result = CliRunner().invoke(main, ["rada", "--convocation", "5"])
    assert result.exit_code == 0, result.output
    assert fake.fetched == [5]
    assert "skl5: law bills 4, laws 2, with votes 1/2 (50%)" in result.output
    assert "family: edition law bases found 1/" in result.output  # 524-16 is a basis
    assert "tax:" not in result.output


def test_cli_rada_missing_cache_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "make_rada_client", lambda cache, refetch=False: FakeRadaClient())
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["rada", "--convocation", "5"])
    assert result.exit_code == 1
    assert "not cached" in result.output


def test_cli_rada_bad_convocation():
    result = CliRunner().invoke(main, ["rada", "--convocation", "2"])
    assert result.exit_code == 2


def test_cli_build_linear_from_cache(tmp_path, fixtures_dir, monkeypatch):
    import json
    import subprocess

    from jurik.zakon.http import ZakonClient, write_gz

    monkeypatch.chdir(tmp_path)
    client = ZakonClient(tmp_path / "cache")
    card = {
        "nreg": "2947-14",
        "nazva": "Сімейний кодекс України",
        "orgdat": 20020110,
        "eds": [{"datred": 20020110, "pidstava": ""}, {"datred": 20030101, "pidstava": "407-15"}],
    }
    write_gz(client.card_path("2947-14"), json.dumps(card))
    html = (fixtures_dir / "zakon" / "2947-14_ed20020110_print.html").read_text("utf-8")
    for d in ("20020110", "20030101"):
        write_gz(client.edition_path("2947-14", d), html)
    client.close()

    result = CliRunner().invoke(main, ["build", "--linear", "--code", "family", "--out", "laws"])
    assert result.exit_code == 0, result.output
    assert "2 commits" in result.output
    log = subprocess.run(
        ["git", "-C", "laws", "log", "--format=%s"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert log == [
        "Закон № 407-IV (Сімейний кодекс України)",
        "Сімейний кодекс України: перша редакція",
    ]
    assert (tmp_path / "laws/codes/family/_meta.yaml").is_file()


def test_cli_build_linear_missing_cache_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["build", "--linear", "--code", "family", "--out", "laws"])
    assert result.exit_code == 1
    assert "not cached" in result.output
