from dataclasses import replace
from datetime import date

import pytest
from gitrepo import git

from jurik.build.changeset import ChangeSet
from jurik.build.check import check_repo
from jurik.build.fastimport import Stream, run
from jurik.build.history import CodeSource, plan_full, plan_linear, write_steps
from jurik.build.repo import RepoError
from jurik.build.sources import latest_prints
from jurik.config import Code
from jurik.model import Edition
from jurik.zakon.card import edition_kind
from jurik.zakon.http import ZakonClient, write_gz
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


def plan(planner=plan_full, in_force=None):
    htmls = {e.date: h for e, h in EDITIONS}

    def load(e):
        return build_tree(extract_paragraphs(htmls[e.date]), e)

    src = CodeSource(FAMILY, {"2947-14": [e for e, _ in EDITIONS]}, in_force=in_force or {})
    return planner([src], load)


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


def test_edition_going_back_is_a(tmp_path):
    steps = plan(plan_linear)
    a, b = "Edition: 2947-14/ed20050301", "Edition: 2947-14/ed20100701"
    steps[1].message = steps[1].message.replace(a, b)
    steps[2].message = steps[2].message.replace(b, a)
    repo = build(tmp_path, steps)
    messages = [v.message for v in check_repo(repo, steps, PRINTS)]
    assert any(m.endswith("edition 2947-14/ed20050301 after 2947-14/ed20100701") for m in messages)


def test_corrupted_file_is_c(tmp_path):
    steps = plan()
    last = steps[-1]
    path = next(p for p, c in last.changes.modified.items() if "Третя нова" in c.decode())
    for step in steps[-2:]:  # the branch commit and its merge
        step.changes.modified[path] = step.changes.modified[path].replace(
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


def test_branch_diff_differs_from_merge_is_e(tmp_path):
    steps = plan()
    branch = steps[-2]
    assert branch.kind == "branch"
    path = next(iter(branch.changes.modified))
    branch.changes.modified[path] += b"\nextra line on the branch only\n"
    repo = build(tmp_path, steps)
    violations = check_repo(repo, steps, PRINTS)
    assert checks(violations) == {"e"}
    assert "branch diff differs from the merge diff" in violations[0].message


def test_edition_not_in_plan_is_a(tmp_path):
    steps = plan(plan_linear)
    steps[1].message = steps[1].message.replace(
        "Edition: 2947-14/ed20050301", "Edition: 2947-14/ed20050301\nEdition: 9-99/ed20050301"
    )
    repo = build(tmp_path, steps)
    messages = [v.message for v in check_repo(repo, steps, PRINTS)]
    assert any(m.endswith("edition 9-99/ed20050301 is not in the plan") for m in messages)


def test_branch_merged_twice_is_d(tmp_path):
    steps = plan()
    merge, branch_index = steps[-1], len(steps) - 2
    again = replace(
        merge,
        message=merge.message.replace("Edition: 2947-14/ed20100701\n", ""),
        changes=ChangeSet(),
        parents=(len(steps) - 1, branch_index),
    )
    steps.append(again)
    repo = build(tmp_path, steps)
    messages = [str(v) for v in check_repo(repo, steps, PRINTS)]
    assert f"(d) {merge.branch}: tip is merged into main 2 times" in messages


def test_not_a_git_repo_is_check_error(tmp_path):
    with pytest.raises(RepoError, match="git log"):
        check_repo(tmp_path / "nothing", plan(), PRINTS)


def test_code_without_files_in_main_is_c(tmp_path):
    steps = plan()
    repo = build(tmp_path, steps)
    violations = check_repo(repo, steps, {**PRINTS, "tax": [EDITIONS[0][1]]})
    assert checks(violations) == {"c"}
    assert all(v.message.startswith("tax: ") for v in violations)


def test_entry_into_force_dates_initial_and_earlier_editions(tmp_path):
    # in force from 2006: the 2002 initial text and the 2005 law wait for it
    steps = plan(in_force={"2947-14": date(2006, 1, 1)})
    main = [s for s in steps if s.ref == "main"]
    assert [(s.reason, s.date) for s in main] == [
        ("initial", date(2006, 1, 1)),
        ("law", date(2006, 1, 1)),
        ("law", date(2010, 7, 1)),
    ]
    repo = build(tmp_path, steps)
    assert check_repo(repo, steps, PRINTS) == []
    log = git(repo, "log", "--first-parent", "--format=%(trailers:key=In-force,valueonly)", "main")
    assert log.split() == ["2010-07-01", "2006-01-01", "2006-01-01"]


KUPAP = Code("admin-offences", ("80731-10", "80732-10"), "КУпАП")
COMMERCIAL = Code(
    "commercial", ("436-15",), "Господарський кодекс", repealed=date(2020, 1, 1), repealed_by="1-20"
)
PART1 = page("Розділ I ЗАГАЛЬНІ ПОЛОЖЕННЯ", "Стаття 1. Завдання", "Перша частина.")
PART2 = page("Розділ II ОСОБЛИВА ЧАСТИНА", "Стаття 200. Штраф", "Друга частина.")


def test_prints_of_repealed_and_two_document_codes(tmp_path):
    client = ZakonClient(tmp_path / "cache")
    docs = {"80731-10": PART1, "80732-10": PART2, "436-15": PART1}
    for zakon_id, html in docs.items():
        write_gz(client.edition_path(zakon_id, "20030101"), html)
    eds = {z: Edition(z, date(2003, 1, 1), [], "other", "https://x") for z in docs}
    sources = [
        CodeSource(KUPAP, {"80731-10": [eds["80731-10"]], "80732-10": [eds["80732-10"]]}),
        CodeSource(COMMERCIAL, {"436-15": [eds["436-15"]]}),
    ]

    def load(e):
        return build_tree(extract_paragraphs(docs[e.zakon_id]), e)

    steps = plan_full(sources, load)
    prints = latest_prints(sources, client)
    client.close()
    assert prints == {"admin-offences": [PART1, PART2]}  # repealed code: no print to check
    repo = build(tmp_path, steps)
    assert check_repo(repo, steps, prints) == []
    assert "codes/commercial" not in git(repo, "ls-tree", "-r", "--name-only", "main")
    # a wrong second part is reported for the code
    bad = {"admin-offences": [PART1, PART2.replace("Друга частина", "Інша частина")]}
    violations = check_repo(repo, steps, bad)
    assert checks(violations) == {"c"}
    assert all(v.message.startswith("admin-offences: ") for v in violations)
