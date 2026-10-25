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


@pytest.fixture
def rada_cache(tmp_path):
    """Copy tests/fixtures/rada/ into a cache dir laid out like RadaClient writes it."""
    import gzip
    import zipfile

    root = tmp_path / "cache"
    for src in (FIXTURES / "rada").rglob("*"):
        if not src.is_file():
            continue
        dest_dir = root / "rada" / src.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".json":
            with zipfile.ZipFile(dest_dir / (src.stem + ".zip"), "w") as zf:
                zf.write(src, src.name)
        else:
            (dest_dir / (src.name + ".gz")).write_bytes(gzip.compress(src.read_bytes()))
    return root
