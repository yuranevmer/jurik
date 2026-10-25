from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def write_registry(tmp_path):
    def _write(text: str) -> Path:
        path = tmp_path / "codes.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    return _write
