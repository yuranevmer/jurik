import gzip
import zipfile
from datetime import date

import pytest

from jurik.model import BillRef, Person
from jurik.rada.client import RadaError
from jurik.rada.index import (
    Vote,
    act_to_nreg,
    bill_law_title,
    build_index,
    display_act,
    is_law_bill,
    person_from_fio,
    pick_vote,
    status_of,
)


@pytest.fixture
def index(rada_cache):
    return build_index(rada_cache, [5, 9])


def test_law_meta_complete_from_csv(index):
    meta = index.law("524-16")
    assert meta is index.by_law["524-16"]
    assert meta.law_number == "524-16"
    assert meta.display_number == "524-V"
    assert meta.law_title.startswith("Про внесення змін до Сімейного кодексу України")
    assert meta.bill == BillRef(5, "0903")
    assert meta.initiators == [Person("Віктор Янукович", "yanukovych-viktor-fedorovych")]
    assert meta.registered == date(2006, 5, 25)
    assert meta.adopted == date(2006, 12, 22)
    # «у другому читанні і в цілому», amendments and the re-vote are ignored
    assert meta.votes == (321, 0, 0)
    assert meta.signed is not None and meta.signed > meta.adopted
    assert meta.status == "adopted"
    assert index.bills[BillRef(5, "0903")] is meta


def test_law_meta_complete_from_billinfo(index):
    meta = index.law("5013-20")
    assert meta.display_number == "5013-IX"
    assert meta.bill == BillRef(9, "15532")
    assert meta.law_title.startswith("Про внесення змін до Податкового кодексу України")
    assert meta.initiators[0] == Person("Данило Гетманцев", "hetmantsev-danylo-oleksandrovych")
    assert len(meta.initiators) == 8
    assert meta.registered == date(2026, 8, 21)
    assert meta.adopted == date(2026, 10, 13)
    assert meta.votes == (252, 8, 20)
    assert meta.signed == date(2026, 10, 23)
    assert meta.status == "adopted"


def test_law_without_vote(index):
    meta = index.law("549-16")
    assert meta.bill == BillRef(5, "2417")
    assert meta.status == "adopted"
    assert meta.votes is None


def test_rejected_and_other_statuses(index):
    rejected = index.bills[BillRef(5, "2221")]
    assert rejected.status == "rejected"
    assert rejected.law_number == "" and rejected.adopted is None
    assert rejected.initiators == [Person("Анатолій Семинога", "semynoha-anatolii-ivanovych")]
    assert index.bills[BillRef(9, "15112-д")].status == "rejected"
    assert index.bills[BillRef(5, "1067")].status in {"removed", "pending", "withdrawn"}
    assert set(index.by_law) == {"524-16", "549-16", "5013-20"}


def test_missing_cache_file(rada_cache):
    (rada_cache / "rada" / "skl5" / "bills_acts-skl5.csv.gz").unlink()
    with pytest.raises(RadaError, match="bills_acts-skl5.*jurik rada --convocation 5"):
        build_index(rada_cache, [5])


def test_broken_json_names_file(rada_cache):
    path = rada_cache / "rada" / "skl9" / "billinfo-skl9.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("billinfo-skl9.json", '[{"id": 1,')
    with pytest.raises(RadaError, match=r"billinfo-skl9\.zip: invalid JSON"):
        build_index(rada_cache, [9])


def test_missing_csv_column_names_file(rada_cache):
    path = rada_cache / "rada" / "skl5" / "bills_acts-skl5.csv.gz"
    path.write_bytes(gzip.compress(b"bill_id,foo\n1,2\n"))
    with pytest.raises(RadaError, match=r"bills_acts-skl5\.csv\.gz: missing columns"):
        build_index(rada_cache, [5])


def test_bad_convocation(rada_cache):
    with pytest.raises(RadaError):
        build_index(rada_cache, [2])


@pytest.mark.parametrize(
    ("act", "nreg"),
    [
        ("229-IX", "229-20"),
        ("229-ІХ", "229-20"),  # Cyrillic І and Х
        ("650-XIV", "650-14"),
        ("2947-III", "2947-14"),
        ("4834-VI", "4834-17"),
        ("1-VIII", "1-19"),
        ("2147а-VIII", "2147а-19"),
        ("256/96-ВР", None),
        ("", None),
    ],
)
def test_act_to_nreg(act, nreg):
    assert act_to_nreg(act) == nreg


def test_display_act():
    assert display_act("229-ІХ") == "229-IX"
    assert display_act("650-XIV") == "650-XIV"


def test_bill_law_title():
    assert bill_law_title("Проєкт Закону про внесення змін") == "Про внесення змін"
    assert bill_law_title("про ратифікацію  Угоди") == "Про ратифікацію Угоди"
    unclosed = '"Про внесення змін до Закону "Про X"'
    assert bill_law_title(unclosed) == 'Про внесення змін до Закону "Про X"'


def test_is_law_bill():
    assert is_law_bill("Проект Закону")
    assert is_law_bill("Проєкт Трудового кодексу України")
    assert is_law_bill("Пропозиції Президента до Закону")
    assert not is_law_bill("Проект Постанови")
    assert not is_law_bill("Проєкт Постанови про …")
    assert not is_law_bill("")


def test_status_of():
    assert status_of("Закон підписано", False) == "adopted"
    assert status_of("Закон про ратифікацію підписано", False) == "adopted"
    assert status_of("Опрацьовується в комітеті", True) == "adopted"
    assert status_of("Відхилено та знято з розгляду", False) == "rejected"
    assert status_of("Проект відкликано", False) == "withdrawn"
    assert status_of("Заслухано та знято з розгляду", False) == "removed"
    assert status_of("Очікує розгляду", False) == "pending"


def test_person_from_fio():
    assert person_from_fio("Кучма Леонід Данилович") == Person(
        "Леонід Кучма", "kuchma-leonid-danylovych"
    )
    assert person_from_fio("  ") is None


def test_pick_vote_priorities():
    d = date(2020, 1, 10)
    first = Vote(date(2020, 1, 1), (230, 0, 0), True, True)
    failed = Vote(d, (200, 10, 0), False, True)
    stageless = Vote(d, (300, 0, 0), True, False)
    final = Vote(d, (280, 1, 1), True, True)
    later = Vote(date(2020, 2, 1), (350, 0, 0), True, True)
    assert pick_vote([first, failed, stageless, final, later], d) == (280, 1, 1)
    assert pick_vote([first, failed, stageless], d) == (300, 0, 0)
    assert pick_vote([first, failed], d) == (230, 0, 0)
    assert pick_vote([later], d) is None
    assert pick_vote([], d) is None
    # not a law: last «в цілому» vote, whatever the result
    assert pick_vote([first, failed, stageless], None) == (200, 10, 0)


def test_csv_neither_utf8_nor_cp1251(rada_cache):
    path = rada_cache / "rada" / "skl5" / "bills_acts-skl5.csv.gz"
    path.write_bytes(gzip.compress(b"bill_id,foo\n\x98\xff,2\n"))  # 0x98 is not in cp1251
    with pytest.raises(RadaError, match=r"bills_acts-skl5\.csv\.gz: neither UTF-8 nor cp1251"):
        build_index(rada_cache, [5])


def test_corrupt_deflate_member(rada_cache):
    path = rada_cache / "rada" / "skl9" / "billinfo-skl9.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("billinfo-skl9.json", "[" + ",".join(['{"id": 1}'] * 500) + "]")
    raw = bytearray(path.read_bytes())
    start = raw.index(b"billinfo-skl9.json") + len("billinfo-skl9.json")  # local header name
    raw[start : start + 8] = b"\xff" * 8  # garbage at the start of the deflate stream
    path.write_bytes(bytes(raw))
    with pytest.raises(RadaError, match=r"billinfo-skl9\.zip: not a zip archive"):
        build_index(rada_cache, [9])
