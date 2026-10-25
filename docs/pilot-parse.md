# Parse-only pilot (Task 10)

Date: 2026-10-25. Source: OpenData editions in `cache/zakon/` (`jurik fetch`), checked with
`jurik parse` (extract → tree → render → word-multiset completeness check), plus a manual
read of rendered files.

## Results

| Code | Editions | Result | Wall time (`uv run jurik parse`, incl. ~0.2 s start-up) |
|---|---|---|---|
| Сімейний (`family`, 2947-14) | all 62 in force (ed20020110 … ed20260805; 1 future edition skipped) | 62 ok, 0 failed | 3.2 s total (~50 ms per edition) |
| Конституція (`constitution`, 254к/96-ВР) | all 10 | 10 ok, 0 failed | 0.5 s total |
| Податковий (`tax`, 2755-17) | ed20240101 (plan) and ed20260917 (latest) | 2 ok | 0.9 s per edition (4.9 MB HTML, 449 k words, 351 files) |
| Цивільний (`civil`, 435-15) | ed20260805 (latest) | 1 ok | 0.45 s (122 k words, ~1490 files) |

Only single editions of the tax and civil codes are cached (`client.edition(...)`); all their
editions were not fetched (multi-GB). `report/parse-failures.md`: no failures.

Word-count verification passed even before the fixes below: every problem found was
structural (text in the wrong file/level) or cosmetic, which the multiset check cannot see.
That is why the manual read and the structural scans matter.

## Problems found and fixed

Each fix has a trimmed fixture (`tests/fixtures/zakon/435-15_ed20260805_pilot_fragment.html`,
22 KB; `tests/fixtures/zakon/2755-17_ed20260917_pilot_fragment.html`, 26 KB) and tests in
`tests/test_pilot_cases.py`.

1. **Inline `{…}` notes stayed in the article text** (e.g. `174.5. {Абзац перший пункту 174.5
   … виключено …}`). Now `tree.strip_notes` removes them from article bodies, titles,
   level titles, provisions, preamble and closing. No rendered file of any checked edition
   contains `{`.
2. **ЦК «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ» has no number** and was glued to the last
   chapter of book six. New unit kind `final` (number `""`, rank above book): it closes every
   open level and renders as `codes/civil/prykintsevi-ta-perekhidni-polozhennia/provisions.md`
   with heading `# ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ`.
3. **Excluded level headings** (`Розділ XVIII - 2 . {Розділ XVIII - 2 виключено …}`,
   `§ 2. {Параграф 2 глави 74 виключено …}`) created empty levels. Now they are `note`s
   (`paragraphs.is_excluded_heading`).
4. **Excluded/lapsed article notes with source typos**: `{С таттю 115 виключено …}` (split
   word), `{Cтаттю 990 …}` (Latin C), and `{Стаття 209 втратила чинність з 1 січня 2017
   року - див. …}`. Excluded → `# Стаття N. Виключена`; lapsed → `# Стаття 209. Втратила
   чинність` + «Втратила чинність з … - див. …». `verify` tolerates both stubs.
5. **Dotless `§ 2 Правові наслідки недодержання …` (ЦК)** was not recognised and was appended
   to article 214. `§` headings now accept a title without the dot (a body sentence never
   starts with `§ N`).
6. **Unlabelled sub-headings inside ЦК § 1 «Господарські товариства»** (`1. Загальні
   положення`, `2. Повне товариство` … `5. Акціонерне товариство`) were appended to the
   preceding article (e.g. art. 118 ended with «2. Повне товариство»). New unit kind `group`
   (rank below §): recognised only when a § (or group) is open, the paragraph matches
   `N. Title` without sentence punctuation, and the next non-note paragraph starts an
   article. Folder `…/01-hospodarski-tovarystva/02-povne-tovarystvo/`, heading
   `# 2. Повне товариство`. Across all cached editions it fires only for these 5 headings.

## Manual spot check

Read rendered files of ЦК ed20260805 (art. 196, 209, 214, § 2 provisions, final provisions,
closing), ПК ed20260917 (art. 1, 14, 115, 174, 209, 215 with tables, розділ XX), Сімейний
ed20020110 / ed20260805 (art. 60, chapters, closing), Конституція ed20060101 / ed20200101
(art. 5, 83, розділ XV, closing). Scans over the rendered trees: paragraphs starting with a
structural word inside articles (only the § 2 case, fixed), all-caps paragraphs inside
articles (none), first line of every `provisions.md` matches `# <Label> N. TITLE` (all, except
the intentional `# ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ`), empty article bodies (none), `{` in
any file (none). Tables in ПК (art. 215 rates) render as markdown tables; signatures go to
`closing.md`.

## Unresolved / accepted cases

- **Bare item labels after note removal**: when the source excludes the first paragraph of an
  item, the label stays alone: `174.5.`, `141.4.9.`, `201.4.`, `53.1.` (ПК articles) and
  `6.` in ПК розділ XX підрозділ 5 (its whole text is a note). Kept on purpose: the following
  paragraphs belong to that item, and dropping the label would attach them to the previous
  one. 5 cases in ПК ed20260917, none in the other codes.
- **Excluded levels vanish entirely**: ПК розділи VII, X, XI, XIII, XV–XVII, XVIII-2,
  Конституція розділ VII «Прокуратура» (since 2016) and excluded items like Конституція XV
  п. 14 leave no file (their only text is a note). Excluded *articles* keep a stub file.
- **`group` heuristic is ЦК-specific**: if another code has unlabelled numbered sub-headings
  outside a §, they will still be glued to the preceding article. Not seen in the four
  codes checked; worth re-running the structural scans when other codes are added.
- **Folder order**: `09-1-…` sorts before `09-…`, `18-1-…` before `18-…` (plain lexical order
  of names); harmless for git, noted for readers.
- Older editions of ПК and ЦК (and all other codes) are not checked by this pilot.
