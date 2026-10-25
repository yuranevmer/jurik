import pytest

from jurik.config import DEFAULT_REGISTRY, ConfigError, Paths, get_code, load_codes


def test_load_real_registry():
    codes = load_codes(DEFAULT_REGISTRY)
    slugs = [c.slug for c in codes]
    assert len(slugs) == len(set(slugs))
    assert {"constitution", "family", "tax", "admin-offences", "civil", "commercial"} <= set(slugs)
    constitution = get_code(codes, "constitution")
    assert constitution.zakon_ids == ("254к/96-ВР",)
    assert constitution.title == "Конституція України"


def test_admin_offences_has_two_zakon_ids():
    code = get_code(load_codes(DEFAULT_REGISTRY), "admin-offences")
    assert code.zakon_ids == ("80731-10", "80732-10")


def test_single_string_zakon_id_becomes_tuple(write_registry):
    path = write_registry(
        '- slug: family\n  zakon_id: "2947-14"\n  title: Сімейний кодекс України\n'
    )
    (code,) = load_codes(path)
    assert code.zakon_ids == ("2947-14",)


def test_duplicate_slug_rejected(write_registry):
    path = write_registry(
        "- {slug: tax, zakon_id: '2755-17', title: A}\n"
        "- {slug: tax, zakon_id: '2947-14', title: B}\n"
    )
    with pytest.raises(ConfigError, match="duplicate slug 'tax'"):
        load_codes(path)


def test_duplicate_zakon_id_rejected(write_registry):
    path = write_registry(
        "- {slug: a, zakon_id: '2755-17', title: A}\n"
        "- {slug: b, zakon_id: ['1-1', '2755-17'], title: B}\n"
    )
    with pytest.raises(ConfigError, match="duplicate zakon_id"):
        load_codes(path)


@pytest.mark.parametrize("missing", ["slug", "zakon_id", "title"])
def test_missing_field_rejected(write_registry, missing):
    entry = {"slug": "tax", "zakon_id": "'2755-17'", "title": "A"}
    del entry[missing]
    body = ", ".join(f"{k}: {v}" for k, v in entry.items())
    path = write_registry(f"- {{{body}}}\n")
    with pytest.raises(ConfigError, match=f"missing field '{missing}'"):
        load_codes(path)


def test_non_list_registry_rejected(write_registry):
    with pytest.raises(ConfigError):
        load_codes(write_registry("slug: tax\n"))


def test_unknown_code():
    with pytest.raises(ConfigError, match="unknown code"):
        get_code(load_codes(DEFAULT_REGISTRY), "nope")


def test_paths(tmp_path):
    p = Paths(tmp_path)
    assert p.cache == tmp_path / "cache"
    assert p.build == tmp_path / "build"
    assert p.report == tmp_path / "report"
