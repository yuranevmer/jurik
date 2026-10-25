import pytest

from jurik.translit import roman_to_int, slug, transliterate


@pytest.mark.parametrize(
    ("ua", "lat"),
    [
        # examples from the КМУ № 55 resolution table
        ("Алушта", "Alushta"),
        ("Згорани", "Zghorany"),
        ("Розгон", "Rozghon"),
        ("Єнакієве", "Yenakiieve"),
        ("Гаєвич", "Haievych"),
        ("Короп'є", "Koropie"),
        ("Їжакевич", "Yizhakevych"),
        ("Кадиївка", "Kadyivka"),
        ("Мар’їне", "Marine"),
        ("Йосипівка", "Yosypivka"),
        ("Стрий", "Stryi"),
        ("Олексій", "Oleksii"),
        ("Юрій", "Yurii"),
        ("Корюківка", "Koriukivka"),
        ("Яготин", "Yahotyn"),
        ("Костянтин", "Kostiantyn"),
        ("Знам'янка", "Znamianka"),
        ("Феодосія", "Feodosiia"),
        ("Щербухи", "Shcherbukhy"),
        ("Ґалаґан", "Galagan"),
    ],
)
def test_transliterate_kmu_examples(ua, lat):
    assert transliterate(ua) == lat


def test_word_start_rules_apply_per_word():
    assert transliterate("Юрій Яготин") == "Yurii Yahotyn"


def test_slug_basic():
    assert slug("ПЕРЕХІДНІ ПОЛОЖЕННЯ") == "perekhidni-polozhennia"
    assert slug("Загальні положення") == "zahalni-polozhennia"


def test_slug_apostrophe_does_not_split_word():
    assert slug("СІМ'Я. РЕГУЛЮВАННЯ СІМЕЙНИХ ВІДНОСИН") == "simia-rehuliuvannia-simeinykh-vidnosyn"
    assert slug("Обов’язки") == "oboviazky"


def test_slug_cut_at_word_boundary():
    s = slug("ЗДІЙСНЕННЯ СІМЕЙНИХ ПРАВ ТА ВИКОНАННЯ СІМЕЙНИХ ОБОВ'ЯЗКІВ. ЗАХИСТ СІМЕЙНИХ ПРАВ")
    assert s == "zdiisnennia-simeinykh-prav-ta-vykonannia"
    assert len(s) <= 40
    assert not s.endswith("-")


def test_slug_long_single_word_hard_cut():
    assert slug("а" * 50) == "a" * 40


def test_slug_no_letters():
    assert slug("...") == ""
    assert slug("") == ""


def test_slug_keeps_digits():
    assert slug("Глава 3. Про ПДВ-2") == "hlava-3-pro-pdv-2"


@pytest.mark.parametrize(
    ("roman", "n"),
    [
        ("I", 1),
        ("IV", 4),
        ("IX", 9),
        ("XIV", 14),
        ("XVIII", 18),
        ("XX", 20),
        ("XXXX", 40),
        ("XXXXII", 42),
    ],
)
def test_roman_to_int(roman, n):
    assert roman_to_int(roman) == n


@pytest.mark.parametrize("bad", ["", "IIII", "XXXXX", "ABC", "VX"])
def test_roman_to_int_rejects(bad):
    with pytest.raises(ValueError):
        roman_to_int(bad)
