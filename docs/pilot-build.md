# Full build (Task 17)

Date: 2026-10-26. All 25 entries of `codes.yaml` (26 zakon documents: КУпАП has two),
built from `cache/zakon/` (OpenData cards and editions) and `cache/rada/` (open data of
convocations III–IX).

## Commands and timings

| Step | Command | Wall time | Result |
|---|---|---|---|
| Rada open data | `uv run jurik rada` (all convocations, cached) | 8 s | 6932 laws, 38648 law bills |
| Fetch | `uv run jurik fetch --code <slug>` for each code, small codes first, Податковий last | 2 h 29 min | 2847 editions + 26 cards, 0 errors, `cache/zakon/` 520 MB |
| Parse | `uv run jurik parse --code <slug> --all` per code as it arrived | Податковий 2 min (241 editions) | 2847 / 2847 ok after the fixes below |
| Build, no bill texts | `uv run jurik build --bills-limit 0` | 294 s, 1.0 GB max RSS | 0 violations |
| Build, final | `uv run jurik build --bills-limit 500` | 1312 s (≈ 870 s plan incl. ≈ 500 bill downloads at ~1/s), 1.2 GB max RSS | 0 violations |
| Report | `uv run jurik report --repo build/laws` | 6 min 55 s (parses every edition) | `report/coverage.md`, no parse failures |

Order of builds as in the plan: `--code family` (123 commits, 0 violations), `--code
constitution` (17 commits, 0 violations), then growing subsets as codes were fetched (13
codes: 886 commits; 19 codes: 2063; all but Податковий: 3156 commits, 203 s), then all 25.

### Editions per code

| Code | zakon id | Editions in force | Future skipped | Cache |
|---|---|---:|---:|---:|
| constitution | 254к/96-ВР | 10 | 0 | 0.4 MB |
| family | 2947-14 | 62 | 1 | 3.9 MB |
| tax | 2755-17 | 241 | 1 | 174 MB |
| admin-offences | 80731-10 + 80732-10 | 301 + 226 = 527 | 0 | 41 MB |
| civil | 435-15 | 183 | 1 | 48 MB |
| civil-procedure | 1618-15 | 120 | 1 | 19 MB |
| criminal | 2341-14 | 244 | 0 | 32 MB |
| criminal-procedure | 4651-17 | 133 | 0 | 38 MB |
| penal | 1129-15 | 58 | 1 | 4.5 MB |
| admin-procedure | 2747-15 | 144 | 5 | 21 MB |
| commercial-procedure | 1798-12 | 125 | 0 | 12 MB |
| customs | 4495-17 | 104 | 0 | 34 MB |
| budget | 2456-17 | 111 | 2 | 16 MB |
| labour | 322-08 | 144 | 3 | 13 MB |
| land | 2768-14 | 172 | 3 | 18 MB |
| housing | 5464-10 | 39 | 0 | 1.8 MB |
| water | 213/95-ВР | 53 | 1 | 2.4 MB |
| forest | 3852-12 | 41 | 1 | 1.4 MB |
| subsoil | 132/94-ВР | 48 | 1 | 1.3 MB |
| air | 3393-17 | 25 | 0 | 2.1 MB |
| merchant-shipping | 176/95-ВР | 26 | 1 | 2.2 MB |
| electoral | 396-20 | 10 | 0 | 3.1 MB |
| bankruptcy | 2597-19 | 33 | 1 | 4.0 MB |
| civil-protection | 5403-17 | 51 | 2 | 5.1 MB |
| commercial | 436-15 | 143 | 0 | 21 MB |
| **total** | | **2847** | 24 | 520 MB |

Every edition of every code parses and passes the word-multiset completeness check
(`report/parse-failures.md`: «No failures in 2847 edition(s)»).

## Repository `build/laws` (final, `--bills-limit 500`)

- `.git`: 78 MB (`--bills-limit 0`: 72 MB, one 57 MB pack); 33521 blobs; 9589 files in the `main` tree (24 codes; `commercial` deleted on its repeal).
- Commits: 4083 in total; 1808 in `--first-parent main`: 25 `initial`, 1548 `law`,
  227 `multiple`, 4 `court`, 3 `unknown`, 1 `repealed`; 228 merges are a later `Stage:` of a
  law already merged before.
- Branches `bill/*`: 2047 = 1547 merged (one per law; 25 of them `bill/law/<nreg>` for laws
  without a bill in open data) + 500 not adopted bills without merge (211 of convocation III,
  289 of IV: the earliest registered). Merged branches by convocation: III 66, IV 147, V 37,
  VI 364, VII 105, VIII 276, IX 527.
- Merges: 1775 (= 1548 + 227).
- `check_repo`: **0 violations** (a: every edition in exactly one `main` commit; b: `In-force:`
  non-decreasing; c: `main` text of the 24 codes in force matches the latest print word for
  word; d: every merged branch has exactly one path into `main`).
- Bill texts of the 500 branches: 397 with text (384 rtf, 13 html, 1 docx, 1 pdf converted),
  103 with the «Текст недоступний» stub: 77 Word 97 `.doc` (no converter), 3 IX documents
  where `itd.rada.gov.ua/billinfo/api/file/download/` answered HTTP 500 after 4 attempts
  (4/3696, 4/2199, 4/2137), the rest without a text document in open data.

## Coverage (`report/coverage.md`)

| Code | Editions | Parsed | With bill in open data | With vote | Laws without LawMeta | Bill branches (not law) | Not adopted by title |
|---|---:|---:|---:|---:|---:|---:|---:|
| constitution | 10 | 10 | 7 (70%) | 5 (50%) | 0 | 38 | 216 |
| family | 62 | 62 | 61 (98%) | 57 (92%) | 0 | 10 | 90 |
| tax | 241 | 241 | 240 (100%) | 231 (96%) | 0 | 10 | 2072 |
| admin-offences | 527 | 527 | 503 (95%) | 456 (87%) | 14 (4%) | 101 | 919 |
| civil | 183 | 183 | 181 (99%) | 175 (96%) | 1 | 36 | 266 |
| civil-procedure | 120 | 120 | 119 (99%) | 116 (97%) | 0 | 26 | 179 |
| criminal | 244 | 244 | 240 (98%) | 222 (91%) | 1 | 99 | 1065 |
| criminal-procedure | 133 | 133 | 128 (96%) | 120 (90%) | 1 | 0 | 508 |
| penal | 58 | 58 | 57 (98%) | 54 (93%) | 0 | 3 | 44 |
| admin-procedure | 144 | 144 | 143 (99%) | 139 (97%) | 0 | 0 | 150 |
| commercial-procedure | 125 | 125 | 119 (95%) | 113 (90%) | 4 (3%) | 17 | 126 |
| customs | 104 | 104 | 103 (99%) | 98 (94%) | 0 | 20 | 396 |
| budget | 111 | 111 | 110 (99%) | 106 (95%) | 0 | 38 | 923 |
| labour | 144 | 144 | 138 (96%) | 126 (88%) | 7 (5%) | 39 | 185 |
| land | 172 | 172 | 170 (99%) | 165 (96%) | 0 | 45 | 355 |
| housing | 39 | 39 | 37 (95%) | 36 (92%) | 1 | 5 | 52 |
| water | 53 | 53 | 52 (98%) | 49 (92%) | 0 | 2 | 21 |
| forest | 41 | 41 | 40 (98%) | 37 (90%) | 0 | 4 | 17 |
| subsoil | 48 | 48 | 46 (96%) | 41 (85%) | 0 | 3 | 30 |
| air | 25 | 25 | 24 (96%) | 24 (96%) | 0 | 4 | 25 |
| merchant-shipping | 26 | 26 | 24 (92%) | 22 (85%) | 1 | 2 | 7 |
| electoral | 10 | 10 | 9 (90%) | 8 (80%) | 0 | 1 | 38 |
| bankruptcy | 33 | 33 | 32 (97%) | 32 (97%) | 0 | 0 | 22 |
| civil-protection | 51 | 51 | 50 (98%) | 49 (96%) | 0 | 0 | 29 |
| commercial | 143 | 143 | 142 (99%) | 137 (96%) | 0 | 11 | 59 |
| **total** | 2847 | 2847 (100%) | 2775 (97%) | 2618 (92%) | 30 (1%) | 514 | 7794 |

«Bill branches (not law)» counts a branch once per code it concerns (500 branches, 514
code links). «Not adopted by title» counts a bill once per code; unique not adopted bills
over all codes: 7362. Omnibus bills «до деяких законодавчих актів» naming no code: 4166 of
4348 (96%) are not matched (known limitation of title matching, Task 11).

## Problems found and fixed

Each fix has a trimmed fixture `tests/fixtures/zakon/*_build_fragment.html` (15–41 KB) and
tests in `tests/test_build_cases.py`; previous pilots (Сімейний, Конституція, ПК, ЦК) were
re-parsed after every fix.

1. **Виборчий кодекс: quoted articles in the final provisions** («р) статті 26-28 викласти в
   такій редакції: "Стаття 26 …" Стаття 27. …» in розділ XXXXII) raised `duplicate article 27`
   on all 10 editions. A repeated article number inside a level titled
   «прикінцеві/перехідні» (or `final`) is now provisions text.
2. **Виборчий кодекс: additive Roman numerals** «Розділ XXXX», «XXXXII» (40, 42):
   `roman_to_int` accepts `X{0,4}` (`IIII`, `XXXXX` are still rejected).
3. **Митний кодекс: Cyrillic І/Х inside Roman numerals, no dot** («Розділ XVІ МИТНА
   СТАТИСТИКА», «Розділ XІX …»): 4 sections were glued to the preceding article. Section
   (and chapter) numerals accept Cyrillic І/Х and are normalised to Latin, in the number and
   in the heading text.
4. **Земельний кодекс: dotless chapter headings with mixed-case titles** («Глава 5 Землі
   сільськогосподарського призначення», 39 chapters glued to articles). A chapter heading
   without a dot is accepted when the title is capitalised and does not end with
   punctuation («Глава 5 цього Кодексу», «… застосовується.» stay body text).
5. **КЗпП: Roman chapter numbers with letter suffixes** («Глава III-А», «ГЛАВА III-Б»,
   «Глава XVI-А», 23 chapters glued): chapter numbers accept `ROMAN(-LETTER)`; the folder
   prefix transliterates the suffix (`03-a-…`), the heading keeps «Глава III-А».
6. **КЗпП 1996–1998: the enacting act before the code** («Закон УРСР Про затвердження
   Кодексу … Стаття 1. Затвердити Кодекс …», 6 editions failed with `duplicate article 1`):
   when the code's own numbering repeats articles that stand before the first level, those
   lead articles become preamble text.
7. **КАС 2016–2017: a real collision in the source** — two laws both added «Стаття 183-7»
   (one of them 1356-VIII); 8 editions failed. Consecutive articles with one number are
   kept: the second is `Article.variant = 2`, file `art-0183-7_2.md`, heading «Стаття 183-7»
   as in the source. Non-consecutive duplicates still raise `TreeError`.
8. **КК: two-level article index** «Стаття 96 - 10-1 .» was glued to art. 96-10: the
   superscript-index normalisation and `_INDEX` accept `N-N-N`.
9. **КУпАП 2009–2012: an editorial note split over two paragraphs** («… громадян. { Кодекс
   доповнено статтею 186-3 згідно із Законом» + «N 2992-12 від 04.02.93, … }», 66 editions
   with 41 missing words): a body paragraph with an unclosed `{` is joined to the next body
   paragraph that closes it, so the note is stripped as an inline note.
10. **КУпАП: «I. ЗАГАЛЬНА ЧАСТИНА» / «II. ОСОБЛИВА ЧАСТИНА» inside розділ II** («II. ОСОБЛИВА
    ЧАСТИНА» ended art. 40-1): a level of subsection rank (a `part` would swallow later
    sections), folder `02-osoblyva-chastyna/`, heading `# II. ОСОБЛИВА ЧАСТИНА`.

`codes.yaml`: `commercial` got `repealed: 2025-08-28`, `repealed_by: "4196-20"` from card
436-15 (status «Втратив чинність», edition entry `podid 3` = loss of force, dated 20250828,
basis 4196-20 = Закон № 4196-IX від 09.01.2025). The build commits the 2025-08-28 edition
(law 4196-IX, Stage 2) and then the `repealed` commit that deletes `codes/commercial/`.

## Decisions

- **Житловий кодекс (5464-10) stays in force.** The card status is 9 «Втратив чинність крім
  окремих положень», but the card has no edition for that event (last edition 2022-11-19,
  basis 2010-IX). Some provisions still apply and the plan says a partial loss of force is
  not a `repealed` deletion, so `main` keeps the text of the last edition; no `repealed`
  date in `codes.yaml`.
- **Bill branches capped at 500** (`--bills-limit 500`, earliest registered first). There are
  7362 unique not adopted bills; texts download at ≈ 1 bill/s for III–VIII and need several
  chunked requests per IX document, i.e. 2–3 more hours, and the itd download API answers
  HTTP 500 intermittently. The capped build validates the mechanism (500 branches without
  merge, `main` with the same 1808 commits / 1775 merges as the `--bills-limit 0` build,
  0 violations); a full run is
  `uv run jurik build` without `--bills-limit` (resumable through the bill cache).
- A parse failure would still stop the build of that code (tree/render exceptions), but none
  is left; the completeness check runs in `jurik parse`/`jurik report`, not in `build`.

## Unresolved / accepted cases

- **Laws without LawMeta**: 30 of 2745 law bases (1%): mostly pre-1998 laws (II convocation,
  `…/96-вр`, `…-12`) absent from open data — КУпАП 14, КЗпП 7, ГПК 4, and single cases in
  ЦК (`2147а-19`), КК, КПК, Житловий, КТМ. They get branches `bill/law/<nreg>` (25 refs).
- **Bill texts unavailable**: 77 Word 97 `.doc` files (no converter), 3 HTTP 500 from the
  itd API; these branches carry the «Текст недоступний» stub with the card link.
- **Votes missing**: 8% of editions have a law without a «в цілому» vote in open data
  (convocation III voting data covers only 1999-09…2002-03).
- **Кодекс України про надра**: the document title «КОДЕКС УКРАЇНИ ПРО НАДРА» is not
  recognised as a header (the card title is «Про надра») and stays in `preamble.md`.
  Cosmetic, words are complete.
- **Structural scans** (body paragraphs starting with a structural word or all caps, outside
  final provisions) are clean on the latest edition of every code; older editions are covered
  only by the word check, which cannot see text placed at the wrong level.
- The variant file name `art-NNNN_2.md` is new in the layout; it appears only in КАС
  editions 2016-06-18 … 2017-08-03.

# Acceptance checks (Task 18)

Date: 2026-10-26, on the final `build/laws` (`--bills-limit 500`) described above.

| Check | Result |
|---|---|
| Commit and branch counts | **pass** |
| `git log --follow` on Податковий ст. 14 | **pass** |
| `export --at 2015-01-01` vs. zakon text (Сімейний, Податковий) | **pass** |
| Manual sample of 20 merge commits | 13 match, 6 partial, **1 mismatch** (attribution, see below) |
| Branch diff vs. its base / vs. its merge | **pass** (5 sampled by hand; all 1547 by script) |
| `uv run pytest`, `uv run ruff check` | **pass** (402 passed, 1 deselected; ruff clean) |

## Counts

- `git rev-list --first-parent --count main` = **1808** = plan steps on `main` (25 initial +
  1548 law + 227 multiple + 4 court + 3 unknown + 1 repealed), same as `report/coverage.md`
  («1808 commits in `--first-parent main`»).
- Merges on `--first-parent main`: 1775 = 1548 + 227 law/multiple steps; 1775 − 228 later
  `Stage:` re-merges = 1547 distinct merged branches.
- `bill/*`: **2047** = 1547 merged (`for-each-ref --merged=main`, one per law or multi-basis
  group, 25 of them `bill/law/*`) + 500 without merge (`--no-merged=main`, the capped not
  adopted bills); `report/coverage.md`: «2047 (1547 merged, 500 without merge)».
- All commits: 4083.

## `git log --follow`

`codes/tax/01-zahalni-polozhennia/art-0014.md`: 72 commits with `--follow`, 72 on
`--first-parent`. Each `main` entry is a merge titled «Закон № N-IX: <title>» or «Закони
№ …, № … (Податковий кодекс України)» with `Law:`/`Bill:`/`In-force:`/`Stage:`/`Reason:`/
`Edition:`/`Source:` trailers; without `--first-parent` the branch commits show «Законопроект
№ N: <title>». Note that `%ad` (adoption date) is not monotonic along `--first-parent`
(e.g. a 2024-06-18 law merged after one adopted 2024-12-04): `main` is ordered by `In-force:`,
as designed.

## Export on 2015-01-01

`uv run jurik export --at 2015-01-01 --repo build/laws --out <scratch>` → commit `96f7e52247`
(In-force 2015-01-01), 8678 files of 23 codes (no Виборчий 2019 or КУзПБ 2018 yet;
`commercial` is present). Check (c) reads `main` only, so the exported files were checked with
`jurik.verify.verify_edition` against the cached edition in force on that date:

| Code | Edition in force on 2015-01-01 | Files | Result |
|---|---|---:|---|
| Сімейний | `2947-14/ed20141026` | 318 | ok, 25879 words |
| Податковий | `2755-17/ed20150101` | 322 | ok, 204514 words |

Negative control: the same files against the next editions (`ed20150926`, `ed20150117`) fail
(59 and 1179 words missing), so the check does tell editions apart.

## Manual sample: 20 merge commits

`random.seed(20261026); random.sample(git rev-list --first-parent --merges main, 20)`. For
each, the merge's change set (`git diff M^1 M`) was compared with the text of the amending law
from `https://data.rada.gov.ua/laws/show/<nreg>.txt` (`User-Agent: OpenData`, 2 s between
requests). «Editorial» = a file whose only change is zakon's own editing, not the law:
inserted cross-reference links «( 1103-2011-п )», typo fixes, leftover note fragments.

| Commit | Law(s) | Codes / files | What the law amends | Verdict |
|---|---|---|---|---|
| `f921d2368` | 3219-IX | tax, 9 | ст. 102, 165, 170, 172, 186; XX підр. 4, 5, 8, 10 | match |
| `5e029f982` | 1344-IV (budget 2004) | forest, subsoil, water, 6 | suspends ЛК ст. 91, КпН ст. 16, 31, 33, 34, ВК ст. 32 (ст. 80 of the law) | match |
| `f7480c326` | 1423-IX + 552-IX, Stage 3 | land, 10 | 552: ст. 37-1, 130, 131, 143, 145, перехідні; 1423: ст. 22, 135, 137, … | partial: multi-basis + staged (approach A, by design) |
| `e8f4b3064` | 2328-VI | civil, 1 | ст. 988 | match |
| `fa5d8aec2` | 2849-IX (Про медіа) | 10 codes, 35 | КАС 19, 20, 272, 275, +289-4, перехідні; ЗК 92; ВК 57; ПК 96; text-wide «засоби масової інформації» → «медіа» in ЦК, ЗК, ГПК, ЦПК, КВК, КЦЗ, КАС, ГК | match |
| `13dd47560` | 177-IV | admin-offences, 1 | ст. 203 | match |
| `2ba9ee3e6` | 1561-VII | tax, 1 | XX підр. 2 п. 32 | match |
| `526399cfc` | 922-VIII, Stage 1 | 5 codes, only `_meta.yaml` | law in force 2016-02-19, «вводиться в дію» 6 months later; text changes are in Stage 2/3 (`793fa6dc6` 2016-04-01, `674b99001` 2016-08-01) | partial: staged law, stage 1 has no text change (by design) |
| `d8d432823` | 1878-VI + 5042-VI, Stage 2 | civil, 21 | 1878: ст. 182, 191, 331, 334, 364, 640, 657, 732, 745, 794, 1031; 5042: ст. 110, 198 | partial: multi-basis (by design); plus 7 editorial files (ст. 159–163, 180, 195: links «( 514-17 )») and ст. 338 typo «своє»→«свої» |
| `a2485cce5` | 3035-IX + 3278-IX + 3460-IX, Stage 2 | budget, 14 | three laws; e.g. +ст. 24-6 from 3035-IX | partial: multi-basis + staged (by design) |
| `a4b687da3` | 4824-IX | civil, family, 5 | ЦК ст. 31, 32, 71; СК ст. 65, 177 | match |
| `160b65454` | 2350-III | admin-offences, 170 (13 deleted, 2 added) | ст. 80, 81, 121–133, 221, 222, … (≈ 20 articles) | **mismatch** (attribution): the diff also carries 2342-III, 2359-III, 2360-III, 2362-III, 2372-III (card `hist` event 2001-04-05 with **no edition**; e.g. ст. 1 «в редакції Закону N 2342-III»), which have no commit of their own; the message names only 2350-III |
| `7b5de5f5e` | 887-VIII | admin-offences, 1 | ст. 212-3 | match |
| `3a42d6721` | 4711-VI | labour, 23 | ст. 36, 41 (п. 2–3 of the law later repealed) | partial: 18 editorial files (links), ст. 65 / preamble stale note fragments removed, ст. 252-8 typo fixes |
| `df4afe3c2` | 1555-VII | admin-procedure, commercial, 2 | КАС ст. 19; ГК ст. 26 (п. 4.1, now marked repealed by 4196-IX) | match |
| `50d068643` | 2507-VIII | criminal-procedure, 1 | XI перехідні п. 20-3 | match |
| `ee6601a44` | 2249-VIII | family, labour, 23 | КЗпП 42, 77-2, 83, 182-1 + text-wide «інвалід»; СК text-wide «інвалід» | match |
| `0e9d7c30d` | 2136-IX | labour, 1 | XIX прикінцеві п. 2 | match |
| `fa14f9dfa` | 2914-VI | labour, 1 | ст. 67 | match |
| `33604f859` | 3741-VI | tax, 2 | XX підр. 2 п. 22 | partial: ст. 172 editorial (link «( 1103-2011-п )» inserted) |

Summary: **13 match, 6 partial** (4 by design: multi-basis/staged — `f7480c326`,
`526399cfc`, `d8d432823`, `a2485cce5`; 2 editorial noise from zakon re-editing —
`3a42d6721`, `33604f859`), **1 mismatch** (`160b65454`, attribution). No commit touched an
article the law does not amend except through editorial edits.

### Finding: amendment events without an edition (mismatch `160b65454`)

zakon cards list amendment events (`hist`) for which no edition was published (`eds`). Their
text changes appear in the next edition, so the edition-to-edition diff is correct, but the
commit names only the next edition's basis. Across all cards, 11 such events between the first
and last edition carry 12 laws that appear in no `Source:` trailer of `main` (КУпАП 80731-10
and 80732-10: 3 events each, КпН, КТМ, ГПК, ВК, ЛК: 1 each). Not fixed here: attributing the
folded laws means turning such commits into multi-basis steps (`Law:` trailers, branches,
merges), a change to the plan builder rather than a contained bug. Follow-up: in `plan_full`,
add the laws of edition-less `hist` events before an edition to that edition's step.

## Branch diffs

`random.seed(18); random.sample(merged bill/* branches, 5)`; `git diff <branch>^ <branch>`
compared with the law text:

| Branch | Law | Files | Verdict |
|---|---|---|---|
| `bill/6/2503` | 2430-VI | КУпАП ст. 218, 255 | match |
| `bill/6/0866` | 1878-VI + 5042-VI, Stage 2 | ЦК, 21 files | = merge `d8d432823` above (multi-basis, partial by design) |
| `bill/9/6173` | 2801-IX | ЦК, ГПК, ЦПК, КАС, КУзПБ, Виборчий (75 paths incl. `_meta.yaml`) | match (items 1–6 of the law, text-wide replacement in КАС) |
| `bill/8/5181` | 1800-VIII | Бюджетний ст. 17, 22, 23, 33, 37, 38 | match |
| `bill/7/4448a` | 1689-VII | КК 15 paths, КПК 25 paths incl. `_meta.yaml` (глава 24-1 = ст. 297-1…297-5) | match |

Equality with the merge's own change set, for **all 1547** merged branch tips (script,
comparing `(path, new blob)` of `git diff tip^ tip` and `git diff M^1 M`): 1516 identical;
31 differ only by paths that are no-ops at the older branch base (the base already had the
new blob, or the deleted path did not exist yet), so the branch diff is the merge's change
set minus no-ops in every case. The branch base equals the merge's first parent in only 40
cases (branches start at `main` on the registration date, as designed).

## Review fixes: history model rebuild (2026-10-26)

Code review found that a branch commit started from `main` on the registration date but
carried the whole-file change set relative to `main` head. When another law changed the
same file in between, the branch diff carried that law's text too, and since the merge
result equals the branch tip on those paths, `git log`/`git blame` of the file followed the
branch and skipped the `main` commits in between. Example: `codes/labour/03-trudovyi-dohovir/
art-0041.md`, point «3-1) … домашнім працівником» (law 3680-IX) was blamed on bill 10392
(4742-IX) and `git log` of the file never listed 3680-IX.

Fixes (see the plan, Task 13 review note): the branch base is the latest of `main` on the
registration date, the initial commit of each touched code and the last `main` commit that
changed any path of the change set; `_meta.yaml` lost `edition_date` (it changed with every
edition and would pin every base to the head); later stages continue on the previous tip
only while it equals `main`; editions of one In-force date sharing a basis are one step;
the initial commit's `In-force:` is the card's entry into force (hist `podid` 1); new
invariant (e) `git diff M^2^ M^2` == `git diff M^1 M`. Rebuilt with
`uv run jurik build --bills-limit 500` (490 s inside the build, all bill texts cached,
1.0 GB max RSS); `check_repo`: **0 violations** (a–e). Superscript indices are joined in
the same rebuild («3-1)», «статтями 166-10»).

| Metric (`build/laws`) | Before | After |
|---|---:|---:|
| merges where `git diff P2^ P2` ≠ `git diff P1 M` (raw `diff-tree`, blobs compared) | 1524 / 1775 | **0 / 1593** |
| … the same ignoring `_meta.yaml` | 939 / 1775 | **0 / 1593** |
| branches merged more than once / of them with two merges on the same `In-force:` | 168 / **77** | 98 / **0** |
| branch base = merge's first parent | 40 | 117 |
| `--first-parent main` commits | 1808 | 1626 (25 initial, 1409 law, 184 multiple, 4 court, 3 unknown, 1 repealed) |
| merges / merged branches | 1775 / 1547 | 1593 / 1459 |
| all commits / `bill/*` branches / blobs | 4083 / 2047 / 33521 | 3719 / 1959 / 30840 |
| `.git` | 78 MB | 76 MB |
| `git log main -- codes/labour/…/art-0041.md` | 14 commits, no 3680-IX | 20 commits, 3680-IX included |
| `git blame` of «3-1) … домашнім працівником» | bill 10392 (4742-IX) | bill 5695 (3680-IX) |

182 fewer merges: same-date editions of several codes sharing a law (e.g. 4952-IX for
bankruptcy and 4952-IX + 4963-IX for land on 2026-10-01) are now one step instead of two
«stages» of one branch. The "before" counts were measured with a script on the old build
(raw `git diff-tree` output for both sides, so 1524 includes the old `_meta.yaml`
`edition_date` line; the reviewer's 896 compared paths only).

`In-force:` of the initial commits now: Податковий, Бюджетний 2011-01-01; ЦК, ГК, КВК,
Сімейний 2004-01-01; КК 2001-09-01; Земельний 2002-01-01; КАС, ЦПК 2005-09-01; Повітряний
2011-09-16; Митний 2012-06-01; КПК 2012-11-19; КЦЗ 2012-11-21; КУзПБ 2019-04-21; Виборчий
2020-01-01; Лісовий 1994-04-13. Codes without an entry-into-force event after their first
edition keep the edition date.
