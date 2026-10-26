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
