import gzip

from click.testing import CliRunner

from jurik import cli
from jurik.cli import main


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
    assert "check_repo: 0 violations" in result.output
    log = subprocess.run(
        ["git", "-C", "laws", "log", "--format=%s"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert log == [
        "Закон № 407-IV (Сімейний кодекс України)",
        "Сімейний кодекс України: перша редакція",
    ]
    assert (tmp_path / "laws/codes/family/_meta.yaml").is_file()
    assert "Сімейний кодекс України" in (tmp_path / "laws/README.md").read_text("utf-8")


def test_cli_build_linear_missing_cache_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["build", "--linear", "--code", "family", "--out", "laws"])
    assert result.exit_code == 1
    assert "not cached" in result.output


def test_cli_build_full_creates_bill_branch(tmp_path, fixtures_dir, monkeypatch):
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

    result = CliRunner().invoke(main, ["build", "--code", "family", "--out", "laws"])
    assert result.exit_code == 0, result.output
    assert "3 commits, 1 branches, 1 merges" in result.output
    assert "check_repo: 0 violations" in result.output
    refs = subprocess.run(
        ["git", "-C", "laws", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(refs) == ["bill/law/407-15", "main"]


def test_cli_build_rejected_bills_limit(tmp_path, fixtures_dir, monkeypatch):
    import json
    import subprocess
    from datetime import date

    from jurik.model import BillRef, LawMeta
    from jurik.rada.index import LawIndex
    from jurik.zakon.http import ZakonClient, write_gz

    monkeypatch.chdir(tmp_path)
    client = ZakonClient(tmp_path / "cache")
    card = {
        "nreg": "2947-14",
        "nazva": "Сімейний кодекс України",
        "orgdat": 20020110,
        "eds": [{"datred": 20020110, "pidstava": ""}],
    }
    write_gz(client.card_path("2947-14"), json.dumps(card))
    html = (fixtures_dir / "zakon" / "2947-14_ed20020110_print.html").read_text("utf-8")
    write_gz(client.edition_path("2947-14", "20020110"), html)
    client.close()

    def bill(n, registered):
        title = "Про внесення змін до Сімейного кодексу України"
        return LawMeta("", title, BillRef(9, n), [], registered, None, None, None, "rejected")

    index = LawIndex(
        bills={
            BillRef(9, "2-д"): bill("2-д", date(2021, 1, 1)),
            BillRef(9, "1"): bill("1", date(2020, 1, 1)),
        }
    )

    class Texts:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def fetch(self, b):
            return f"Текст {b.number}"

    monkeypatch.setattr(cli, "build_index", lambda cache: index)
    monkeypatch.setattr(cli, "make_bill_texts", lambda cache: Texts())
    result = CliRunner().invoke(
        main, ["build", "--code", "family", "--bills-limit", "1", "--out", "laws"]
    )
    assert result.exit_code == 0, result.output
    assert "2 commits, 1 branches, 0 merges" in result.output
    assert "check_repo: 0 violations" in result.output
    show = subprocess.run(
        ["git", "-C", "laws", "show", "bill/9/1:bills/9/1.md"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert show.endswith("---\n\nТекст 1\n")
