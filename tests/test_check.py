from datetime import date

import pytest
from gitrepo import git

from jurik.build.check import check_repo
from jurik.build.fastimport import Stream, run
from jurik.build.history import CodeSource, plan_full, plan_linear, write_steps
from jurik.config import Code
from jurik.model import Edition
from jurik.zakon.card import edition_kind
from jurik.zakon.paragraphs import extract_paragraphs
from jurik.zakon.tree import build_tree

FAMILY = Code("family", ("2947-14",), "Сімейний кодекс України")


def page(*paras: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    head = "<html><head><title>КОДЕКС</title></head>"
    return f"{head}<body><div id=article>{body}</div></body></html>"


def html_for(text2: str, text3: str = "Третя стаття.") -> str:
    return page(
        "Розділ I ЗАГАЛЬНІ ПОЛОЖЕННЯ",
        "Стаття 1. Мета",
        "Перша стаття.",
        "Стаття 2. Права",
        text2,
        "Стаття 3. Обов'язки",
        text3,
    )


def ed(d: date, *bases: str) -> Edition:
    return Edition("2947-14", d, list(bases), edition_kind(list(bases)), "https://x/2947-14")


EDITIONS = [
    (ed(date(2002, 1, 10)), html_for("Друга стаття.")),
    (ed(date(2005, 3, 1), "100-15"), html_for("Друга стаття змінена.")),
    (ed(date(2010, 7, 1), "200-17"), html_for("Друга стаття змінена.", "Третя нова.")),
]


def plan(planner=plan_full):
    htmls = {e.date: h for e, h in EDITIONS}

    def load(e):
        return build_tree(extract_paragraphs(htmls[e.date]), e)

    return planner([CodeSource(FAMILY, {"2947-14": [e for e, _ in EDITIONS]})], load)


def build(tmp_path, steps):
    stream = Stream()
    write_steps(steps, stream)
    repo = tmp_path / "laws"
    run(stream, repo)
    return repo


PRINTS = {"family": [EDITIONS[-1][1]]}


def checks(violations):
    return {v.check for v in violations}


@pytest.mark.parametrize("planner", [plan_full, plan_linear])
def test_clean_repo_has_no_violations(tmp_path, planner):
    steps = plan(planner)
    repo = build(tmp_path, steps)
    assert check_repo(repo, steps, PRINTS) == []


def test_clean_repo_has_branches_and_merges(tmp_path):
    steps = plan()
    assert sum(s.kind == "merge" for s in steps) == 2
    repo = build(tmp_path, steps)
    assert check_repo(repo, steps, PRINTS) == []


def test_decreasing_in_force_is_b(tmp_path):
    steps = plan()
    last = steps[-1]
    assert last.kind == "merge"
    last.message = last.message.replace("In-force: 2010-07-01", "In-force: 2003-01-01")
    repo = build(tmp_path, steps)
    violations = check_repo(repo, steps, PRINTS)
    assert "b" in checks(violations)
    assert any("2003-01-01 < 2005-03-01" in v.message for v in violations)


def test_missing_in_force_is_b(tmp_path):
    steps = plan(plan_linear)
    steps[1].message = steps[1].message.replace("In-force: 2005-03-01\n", "")
    repo = build(tmp_path, steps)
    assert "b" in checks(check_repo(repo, steps, PRINTS))


def test_skipped_edition_is_a(tmp_path):
    steps = plan()
    merge = steps[-1]
    merge.message = merge.message.replace("Edition: 2947-14/ed20100701\n", "")
    repo = build(tmp_path, steps)
    violations = check_repo(repo, steps, PRINTS)
    assert checks(violations) == {"a"}
    assert "edition 2947-14/ed20100701 has no commit in main" in [v.message for v in violations]


def test_duplicate_edition_is_a(tmp_path):
    steps = plan(plan_linear)
    steps[1].message = steps[1].message.replace(
        "Edition: 2947-14/ed20050301", "Edition: 2947-14/ed20050301\nEdition: 2947-14/ed20020110"
    )
    repo = build(tmp_path, steps)
    messages = [v.message for v in check_repo(repo, steps, PRINTS)]
    assert "edition 2947-14/ed20020110 is in 2 commits of main" in messages
    assert any("ed20020110 has In-force 2005-03-01" in m for m in messages)


def test_corrupted_file_is_c(tmp_path):
    steps = plan()
    last = steps[-1]
    path = next(p for p, c in last.changes.modified.items() if "Третя нова" in c.decode())
    last.changes.modified[path] = last.changes.modified[path].replace(
        "Третя нова".encode(), "Третя зіпсована".encode()
    )
    repo = build(tmp_path, steps)
    violations = check_repo(repo, steps, PRINTS)
    assert checks(violations) == {"c"}
    assert any("зіпсована" in v.message and path in v.message for v in violations)


def test_stale_print_is_c(tmp_path):
    steps = plan()
    repo = build(tmp_path, steps)
    assert checks(check_repo(repo, steps, {"family": [EDITIONS[0][1]]})) == {"c"}


def test_missing_branch_is_d(tmp_path):
    steps = plan()
    repo = build(tmp_path, steps)
    ref = steps[-1].branch
    git(repo, "update-ref", "-d", f"refs/heads/{ref}")
    violations = check_repo(repo, steps, PRINTS)
    assert [str(v) for v in violations] == [f"(d) {ref}: branch is missing"]


def test_unmerged_branch_tip_is_d(tmp_path):
    steps = plan()
    repo = build(tmp_path, steps)
    ref = steps[-1].branch
    git(repo, "update-ref", f"refs/heads/{ref}", "main")  # tip is no longer a merged commit
    violations = check_repo(repo, steps, PRINTS)
    assert [str(v) for v in violations] == [f"(d) {ref}: tip is merged into main 0 times"]
