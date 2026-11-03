from datetime import date

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


def test_bad_title_pattern_rejected(write_registry):
    path = write_registry(
        "- slug: tax\n  zakon_id: '2755-17'\n  title: Податковий\n  title_patterns: ['(']\n"
    )
    with pytest.raises(ConfigError, match="tax.*title_patterns"):
        load_codes(path)


def test_repealed_fields(write_registry):
    path = write_registry(
        '- slug: commercial\n  zakon_id: "436-15"\n  title: Господарський кодекс України\n'
        "  repealed: 2025-08-28\n  repealed_by: 4196-20\n"
    )
    (code,) = load_codes(path)
    assert code.repealed == date(2025, 8, 28)
    assert code.repealed_by == "4196-20"
    bad = write_registry('- slug: c\n  zakon_id: "1-1"\n  title: T\n  repealed: soon\n')
    with pytest.raises(ConfigError, match="repealed"):
        load_codes(bad)


def test_kind_and_folder(write_registry):
    path = write_registry(
        '- slug: tax\n  zakon_id: "2755-17"\n  title: Податковий кодекс України\n'
        '- slug: defence\n  kind: law\n  zakon_id: "1932-12"\n  title: Закон\n'
    )
    tax, defence = load_codes(path)
    assert (tax.kind, tax.folder) == ("code", "codes/tax")
    assert (defence.kind, defence.folder) == ("law", "laws/defence")
    bad = write_registry('- slug: x\n  kind: decree\n  zakon_id: "1-1"\n  title: T\n')
    with pytest.raises(ConfigError, match="kind"):
        load_codes(bad)


def test_invalid_yaml_is_config_error(write_registry):
    path = write_registry("- slug: x\n  title_patterns: ['обов'язок']\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_codes(path)


def test_real_registry_has_military_laws():
    laws = {c.slug: c for c in load_codes(DEFAULT_REGISTRY) if c.kind == "law"}
    assert len(laws) == 12
    assert laws["military-service"].zakon_ids == ("2232-12",)
    assert laws["military-service"].folder == "laws/military-service"
