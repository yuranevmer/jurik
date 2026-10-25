import pytest
from click.testing import CliRunner

from jurik.cli import main


@pytest.mark.parametrize(
    "cmd", [["rada"], ["parse"], ["build"], ["report"], ["export", "--out", "x"]]
)
def test_stub_commands_exist(cmd):
    result = CliRunner().invoke(main, cmd)
    assert result.exit_code == 1
    assert "not implemented" in result.output


def test_help_lists_commands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for name in ("fetch", "rada", "parse", "build", "report", "export"):
        assert name in result.output
