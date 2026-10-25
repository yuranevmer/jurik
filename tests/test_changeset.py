from jurik.build.changeset import ChangeSet, diff


def test_diff_exact_modified_and_deleted():
    prev = {"a.md": b"1", "b.md": b"2", "c.md": b"3"}
    new = {"a.md": b"1", "b.md": b"22", "d.md": b"4"}
    cs = diff(prev, new)
    assert cs.modified == {"b.md": b"22", "d.md": b"4"}
    assert cs.deleted == {"c.md"}
    assert cs.apply(prev) == new


def test_diff_identical_is_empty():
    files = {"a.md": b"1"}
    cs = diff(files, dict(files))
    assert not cs
    assert cs.modified == {} and cs.deleted == set()


def test_diff_from_nothing_adds_everything():
    cs = diff({}, {"x/a.md": b"1", "x/b.md": b"2"})
    assert set(cs.modified) == {"x/a.md", "x/b.md"}
    assert not cs.deleted


def test_update_later_changes_win():
    cs = ChangeSet(modified={"a": b"1"}, deleted={"b"})
    cs.update(ChangeSet(modified={"b": b"2"}, deleted={"a"}))
    assert cs.modified == {"b": b"2"}
    assert cs.deleted == {"a"}
