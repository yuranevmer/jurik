# Ukraine Laws as Git: Конституция + кодексы с полной историей

## Overview

Построить git-репозиторий `laws`, в котором:

- рабочая копия `main` = актуальный официальный текст Конституции и кодексов Украины, по одному markdown-файлу на статью;
- история коммитов = история принятия законов: каждый закон, менявший кодекс, это ветка `bill/<созыв>/<номер>` с коммитом правок и merge в `main`, датированный днём принятия, с инициатором как автором и Верховной Радой как коммиттером;
- отклонённые и зависшие законопроекты это ветки без merge.

Репозиторий порождается инструментами из этого проекта (`jurik`) по данным двух официальных источников: zakon.rada.gov.ua (редакции текстов) и data.rada.gov.ua (законопроекты, голосования). Цели: удобное чтение человеком, `git log`/`git blame` по статье, датасет для LLM, собираемый из `main` на любую дату.

Этот план = этап 1. Этап 2 (все законы), этап 3 (подзаконные акты как submodule `bylaws`) и подход C (парсер текста правок с валидацией на принятых законах) делаются отдельными планами. В этом плане ветки отклонённых законопроектов содержат текст законопроекта как файл, без применения правок к статьям.

## Context (from discovery)

- Директория `/Users/yura/Workspaces/proto/jurik` пуста, git не инициализирован. Этот репозиторий = инструменты (Python-пакет `jurik`). Результат собирается в `build/laws/` (в `.gitignore`), отдельный git-репозиторий.
- Окружение: `uv` и Python 3.14 установлены.
- **zakon.rada.gov.ua** (проверено curl): API нет. Карточка `https://zakon.rada.gov.ua/laws/card/<id>` содержит список редакций: ссылки `/laws/show/<id>/ed<YYYYMMDD>`, рядом дата, «Редакція, підстава - <номер закону>», размер. Текст редакции: `https://zakon.rada.gov.ua/laws/show/<id>/ed<YYYYMMDD>/print`. Нужен браузерный User-Agent, иначе 403. Податковий кодекс: ~240 редакций, print ~8.6 МБ HTML / 3.6 млн символов текста. Сімейний кодекс (2947-14): 62 редакции. Конституція (254к/96-ВР): 9 редакций.
- **Разметка print-версии**: абзацы `<p class=rvps2><a name="nNNN"></a>текст</p>`; заголовок статьи `<span class=rvts9>Стаття 14.</span> Визначення понять` в начале абзаца; розділ/глава в `<p class=rvps7><span class=rvts15>ГЛАВА 1. ...</span>`; индекс статьи «39-1» кодируется как `39<span class=rvts37><span style="font-size:0px">-</span>1</span>`; редакторские примечания `{Розділ I доповнено статтею 39-1 згідно із Законом № 1200-VII ...}` внутри `<em>`; шапка документа содержит `{Із змінами, внесеними згідно із Законами № ... від ...}`. Номера классов `rvtsNN` могут отличаться между редакциями, парсер должен опираться на текстовые паттерны, а классы использовать как подсказку.
- **Известные сложности данных** (из ревью плана): один закон часто вступает в силу поэтапно и даёт несколько редакций с одной підставою в разные даты; одна редакция может иметь несколько підстав («підстава - 71-VIII, 79-VIII», типично для 1 января); у кодексов до шести уровней иерархии (Книга, Частина, Розділ, Підрозділ, Глава, §) и неструктурированные «Прикінцеві та перехідні положення» с нумерованными пунктами вместо статей (Конституція розділ XV, Податковий розділ XX); номера законопроектов повторяются между созывами и бывают с суффиксами (`1234-д`, `1234-1`).
- **data.rada.gov.ua**: каталог `https://data.rada.gov.ua/ogd/list.json`; группы `/ogd/zpr/skl3..skl9` (законопроекты по созывам III–IX, 1998–сейчас), `/ogd/zal/ppz/skl*` (хронология рассмотрения и голосования), `/ogd/zak/laws/docs` (карточки документов). Конкретные URL файлов внутри групп не извлечены (страницы подгружают список динамически), это предмет разведки в Task 2. Тексты законопроектов — docx/pdf на rada.gov.ua или itd.rada.gov.ua.

## Development Approach

- **testing approach**: Regular (code first, then tests in the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - tests are not optional - they are a required part of the checklist
  - write unit tests for new functions/methods
  - write unit tests for modified functions/methods
  - tests cover both success and error scenarios
- **CRITICAL: all tests must pass before starting next task** - no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change: `uv run pytest`
- сетевые тесты (реальные запросы к rada.gov.ua) помечаются `@pytest.mark.network`, маркер зарегистрирован в pyproject, `addopts = "-m 'not network'"`; всё остальное работает на фикстурах в `tests/fixtures/`
- стек: `uv`, Python ≥3.12, `httpx`, `lxml`, `pyyaml`, `click`, `pytest`; линтер `ruff`

## Testing Strategy

- **unit tests**: required for every task (see Development Approach above)
- **фикстуры**: реальные HTML-страницы zakon (карточки и print-редакции) и JSON открытых данных, сохранённые в `tests/fixtures/`. Крупные print-страницы режутся до репрезентативных фрагментов (шапка + несколько глав), чтобы фикстуры были < 200 КБ; исключение: первая редакция Сімейного кодексу хранится целиком как единственная «полная» фикстура
- **инварианты сборки** (Task 14): каждая редакция отображена ровно в один коммит `main`; даты `In-force:` по `main` не убывают; текст `main` после последней редакции по словам совпадает с print-версией
- **e2e**: нет UI, e2e не применимо. Вместо этого Task 10 (parse-only пилот), Task 17 (полная сборка) и Task 18 (ручная выборка 20 коммитов)

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

Подход A «от редакций»: источник истины для текста это редакции zakon.rada.gov.ua. Каждая редакция парсится в дерево статей, diff соседних редакций становится коммитом ветки закона. Открытые данные Рады только обогащают метаданные (инициаторы, даты, голоса). `main` всегда совпадает с официальным текстом и это проверяется инвариантом.

Четыре компонента с кешем на диске, пайплайн: Fetcher → Parser → дерево на дату; Rada client → метаданные; Builder склеивает в `git fast-import`-поток. Репозиторий `build/laws` пересобирается с нуля при каждом запуске, инкрементальных обновлений нет (YAGNI до публикации истории).

Ключевые решения:
- один репозиторий на все кодексы: закон, правящий несколько кодексов, это один коммит;
- файл на статью, без фронтматтера, вся мета в git и `_meta.yaml`;
- исключённые статьи не удаляются, а становятся файлом-заглушкой;
- **ось времени**: порядок `main` = порядок вступления редакций в силу (это и есть официальная последовательность текстов). Committer date merge-коммита = дата принятия закона (пожелание пользователя), поэтому committer-даты по `main` могут быть немонотонны, git это допускает. Дата вступления в силу всегда пишется трейлером `In-force:`, и всё, что выбирает «состояние на дату» (export, инварианты, приёмка), работает по этому трейлеру, а не по `--before`;
- `fast-import` с change set'ами: коммит содержит только `M`/`D` изменённых путей, blob'ы дедуплицируются по хешу и пишутся один раз. Это делает diff ветки чистым (только правки этого закона) и держит поток в сотнях МБ, а не десятках ГБ;
- ветки отклонённых законопроектов в этом плане содержат только `bills/<созыв>/<номер>.md`, применение правок к статьям отложено до подхода C.

## Technical Details

### Раскладка выходного репозитория `build/laws`

```
README.md
codes/
  constitution/
    _meta.yaml            # title, zakon_id, adopted, source_url, status
    preamble.md
    01-zahalni-zasady/    # Розділ I
      art-0001.md
    15-perekhidni-polozhennya/
      provisions.md       # нумерованные пункты без статей
  civil/
    01-zahalni-polozhennya/          # Книга перша
      01-osnovni-polozhennya/        # Розділ I
        01-tsyvilne-zakonodavstvo/   # Глава 1
          art-0001.md
  tax/
    _meta.yaml
    01-zahalni-polozhennya/
      art-0001.md
      art-0039-1.md       # стаття 39-1
    20-perekhidni-polozhennya/
      01-osoblyvosti-spravliannia-pdv/   # Підрозділ 1
        provisions.md
bills/                    # только в ветках bill/*
  9/
    7101.md
```

Правила имён: slug кодекса из реестра `codes.yaml`; каждый структурный уровень (книга, частина, розділ, підрозділ, глава, §) это папка `NN-<translit>`, где `NN` выводится из номера в источнике (римское → арабское, индекс сохраняется: «Глава 1-1» → `01-1-...`), а не из порядка появления, чтобы вставка главы не переименовывала соседей; транслит по КМУ-2010, не более 40 символов. Статья `art-NNNN.md` (4 цифры, у Податкового > 300 статей, у ЦК > 1300), индекс через дефис `art-0039-1.md`. Текст уровня, не разбитый на статьи (перехідні/прикінцеві положення, вступления к розділам), лежит в `provisions.md` этой папки как есть, с сохранением нумерации пунктов. Уровни, которых в кодексе нет, просто отсутствуют в пути.

Файл статьи:

```markdown
# Стаття 14. Визначення понять

14.1. У цьому Кодексі поняття вживаються в такому значенні:

14.1.1. аваль - вексельне поручительство, ...
```

Редакторские примечания в фигурных скобках `{...}` не включаются в текст (они дублируют то, что показывает git log). Исключённая статья:

```markdown
# Стаття 5. Виключена

Виключена на підставі Закону № 2856-VI від 23.12.2010.
```

Кодекс, утративший силу (Господарський, 2025): коммит на дату утраты силы удаляет `codes/<slug>/` целиком, в сообщении `Reason: repealed`. `main` = действующее право, история кодекса остаётся в git.

КУпАП состоит из двух документов zakon (80731-10 и 80732-10) с независимыми списками редакций; они собираются в одну папку `admin-offences`, и состояние папки на любую дату = объединение последних редакций каждой части на эту дату.

### Реестр кодексов `codes.yaml`

```yaml
- slug: constitution
  zakon_id: "254к/96-ВР"
  title: Конституція України
- slug: family
  zakon_id: "2947-14"
  title: Сімейний кодекс України
- slug: tax
  zakon_id: "2755-17"
  title: Податковий кодекс України
- slug: admin-offences
  zakon_id: ["80731-10", "80732-10"]
  title: Кодекс України про адміністративні правопорушення
# ... civil 435-15, civil-procedure 1618-15, criminal 2341-14,
# criminal-procedure 4651-17, penal 1129-15, admin-procedure 2747-15,
# commercial-procedure 1798-12, customs 4495-17, budget 2456-17, labour 322-08,
# land 2768-14, housing 5464-10, water 213/95-ВР, forest 3852-12,
# subsoil 132/94-ВР, air 3393-17, merchant-shipping 176/95-ВР, electoral 396-20,
# bankruptcy 2597-19, civil-protection 5403-17,
# commercial 436-15 (утратил силу 2025, история сохраняется)
```

Все id проверяются на zakon в Task 1 (ссылка открывается, заголовок совпадает).

### Модель данных (`jurik/model.py`)

```python
@dataclass
class Edition:        # одна редакция документа
    zakon_id: str
    date: date        # дата редакции (= вступление в силу)
    bases: list[str]  # номера підстав, напр. ["2856-VI"], ["71-VIII", "79-VIII"], []
    kind: Literal["law", "court", "other"]   # закон / рішення КСУ / инше
    url: str
    future: bool

UnitKind = Literal["book", "part", "section", "subsection", "chapter", "paragraph"]

@dataclass
class Article:
    number: str       # "14", "39-1"
    title: str
    body: str         # markdown
    excluded: bool

@dataclass
class Unit:
    kind: UnitKind
    number: str       # "I", "1", "1-1"
    title: str
    provisions: str   # текст уровня вне статей, "" если нет
    children: list["Unit | Article"]

@dataclass
class DocTree:
    zakon_id: str
    edition: Edition
    preamble: str
    units: list[Unit | Article]

@dataclass
class BillRef:
    convocation: int
    number: str       # "7101", "1234-д"

@dataclass
class LawMeta:        # из открытых данных Рады
    law_number: str   # "2856-VI"
    law_title: str
    bill: BillRef | None
    initiators: list[Person]   # Person(name, slug)
    registered: date | None
    adopted: date | None
    votes: tuple[int, int, int] | None   # за, проти, утрималися
    signed: date | None
    status: Literal["adopted", "rejected", "withdrawn", "removed", "pending"]
```

### Модель git-истории

Все редакции всех кодексов сортируются по `(date, bases)`. Редакции одной даты с одинаковым набором підстав объединяются в один шаг (закон правит несколько кодексов). Для каждого шага:

| Случай | Что создаётся |
|---|---|
| Первая редакция кодекса | коммит в `main`, author = инициатор или «Верховна Рада України», date = дата принятия кодекса, `Reason: initial` |
| Одна підстава-закон, первый этап | ветка `bill/<созыв>/<номер>` от `main` на дату регистрации; коммит с change set; merge в `main` |
| Та же підстава, следующий этап вступления в силу | ещё один коммит в той же ветке (`Stage: 2`) и повторный merge той же ветки в `main` |
| Несколько підстав в одной редакции | один коммит с несколькими `Law:`/`Bill:` трейлерами и `Reason: multiple`; ветка `bill/<созыв>/<номер>` первого по номеру закона, остальные перечислены в сообщении (diff неразделим в подходе A) |
| Підстава = решение КСУ | коммит в `main`, author «Конституційний Суд України», `Reason: court` |
| Підстава отсутствует | коммит в `main`, author «Апарат Верховної Ради України», `Reason: unknown` |
| Законопроект не найден в открытых данных | ветка `bill/law/<ref_safe(law_number)>`, author «Верховна Рада України», `Bill: unknown` |
| Утрата силы кодекса | коммит в `main`, удаляет папку, `Reason: repealed` |
| Отклонённый/зависший законопроект к кодексу | ветка `bill/<созыв>/<номер>` от `main` на дату регистрации; коммит добавляет `bills/<созыв>/<номер>.md`; без merge |

Имена ссылок проходят `ref_safe()`: `/` в номере закона → `_`, кириллица транслитерируется, запрещённые git'ом последовательности (`..`, `@{`, пробелы, завершающая точка) убираются.

Поля коммитов:
- коммит ветки: author = первый инициатор, `Ім'я Прізвище <translit-slug@deputies.invalid>` (slug стабилен для человека, домен не настоящий); остальные инициаторы `Co-authored-by:`; author date = committer date = дата регистрации, committer = тот же автор;
- merge: author = committer = `Верховна Рада України <rada@laws.invalid>`, author date = committer date = дата принятия;
- если дата регистрации неизвестна, ветка отходит от родителя merge, даты = дата принятия;
- линейные коммиты (`initial`, `court`, `unknown`, `repealed`): author = committer по таблице, date = дата редакции;
- timestamp = 12:00 по `zoneinfo("Europe/Kyiv")` на соответствующую дату, смещение вычисляется, не хардкодится.

Сообщение merge-коммита:

```
Закон № 2856-VI: Про внесення змін до Податкового кодексу України

Law: 2856-VI
Bill: 9/7101
Adopted: 2010-12-23, за 268 / проти 2 / утрималися 1
Signed: 2010-12-30
In-force: 2011-01-01
Stage: 1
Reason: law
Source: https://zakon.rada.gov.ua/laws/show/2856-VI
```

Трейлер `In-force:` обязателен на каждом коммите `main` и является осью «состояние на дату». Будущие редакции (date > today) не берутся. Переименование статьи = delete + add, rename не угадывается.

### fast-import

Builder пишет один поток в `git fast-import`:
- каждый уникальный файл (по sha256 содержимого) один раз как `blob` + `mark`;
- коммит ветки: `commit refs/heads/bill/<c>/<n>`, `from :main_at_registered`, затем только `M 100644 :blob path` / `D path` из change set (diff рендера предыдущей и новой редакции затронутых кодексов). Дерево = main на регистрацию + правки этого закона, `git diff bill^ bill` показывает только правки закона;
- merge: `commit refs/heads/main`, `from :main_head`, `merge :branch_commit`, и тот же change set. Дерево = текущий main + правки = новый официальный текст по построению;
- линейный коммит: `from :main_head` + change set;
- `main_at_registered` = последний коммит `main`, у которого `In-force:` ≤ дата регистрации (ось времени та же, что везде).

### CLI (`jurik` через click)

```
jurik fetch   [--code SLUG] [--refetch]      # карточки + редакции в cache/zakon/
jurik rada    [--convocation N]              # открытые данные в cache/rada/
jurik parse   [--code SLUG] [--edition YYYYMMDD] [--all]   # парсинг + проверка полноты
jurik build   [--code SLUG] [--linear] [--bills-limit N] [--out build/laws]
jurik report                                 # report/coverage.md, report/parse-failures.md
jurik export  [--at DATE] [--with-bills] --out build/dataset/
```

Кеш никогда не очищается автоматически; `--refetch` принудительно перекачивает. HTML в кеше хранится gzip-сжатым (Податковий один даёт ~2 ГБ сырого HTML).

## What Goes Where

- **Implementation Steps**: всё, что делается в этом репозитории: код, тесты, `codes.yaml`, README, заметки разведки в `docs/`.
- **Post-Completion**: публикация `build/laws` на GitHub, согласование условий использования данных (указание источника обязательно), последующие планы.

## Implementation Steps

### Task 1: Каркас проекта и реестр кодексов

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `codes.yaml`
- Create: `jurik/__init__.py`, `jurik/cli.py`, `jurik/config.py`, `jurik/model.py`
- Create: `tests/conftest.py`, `tests/test_config.py`

- [x] `git init`, `uv init --package jurik`, зависимости: httpx, lxml, pyyaml, click; dev: pytest, ruff; в pyproject зарегистрировать маркер `network` и `addopts = "-m 'not network'"`
- [x] `jurik/model.py` с dataclass'ами из Technical Details (`Edition`, `Article`, `Unit`, `DocTree`, `BillRef`, `Person`, `LawMeta`)
- [x] `jurik/config.py`: загрузка `codes.yaml` (zakon_id строка или список), пути `cache/`, `build/`, `report/`; валидация уникальности slug и zakon_id
- [x] `codes.yaml` со всеми кодексами из реестра; для каждого id вручную проверить, что карточка на zakon открывается и заголовок совпадает (однократно, результат зафиксировать в комментарии к yaml)
- [x] `jurik/cli.py`: группа `jurik` с заглушками команд `fetch`, `rada`, `parse`, `build`, `report`, `export`
- [x] `.gitignore`: `cache/`, `build/`, `report/`
- [x] write tests: загрузка `codes.yaml`, список zakon_id для КУпАП, ошибка на дублирующемся slug, ошибка на отсутствующем поле
- [x] run tests - must pass before next task

### Task 2: Разведка источников (spike, ограничен одним днём)

**Files:**
- Create: `docs/spike-sources.md`
- Create: `scripts/spike_rada.py` (одноразовый, не часть пакета)
- Create: `tests/fixtures/zakon/card-{tax,constitution,family,labour}.html`, `card-family.json`

- [x] открытые данные: получить реальные списки файлов групп `zpr/skl9`, `zpr/skl5`, `zal/ppz/skl9`, `zak/laws/docs` (через JSON-манифесты или сетевую вкладку браузера); записать URL и имена полей, где лежат: номер законопроекта и созыв, название, инициаторы, дата регистрации, этапы прохождения, итог голосования «в цілому», номер принятого закона, ссылки на файлы текста
- [x] подтвердить, где именно связь «законопроект ↔ номер закона» (в zpr или в карточке закона из `zak/laws/docs`); проверить покрытие для созывов III–V
- [x] подтвердить, что ссылки на docx/pdf текста законопроекта есть в открытых данных, и какой из документов карточки (текст, пояснювальна записка, порівняльна таблиця) как помечен
- [x] zakon: для старых кодексов (labour 322-08, housing 5464-10, water 213/95-ВР) проверить, что такое «перша редакція» в карточке: исходный текст или более поздний; записать дату самой ранней доступной редакции по каждому кодексу
- [x] zakon: найти и сохранить в `tests/fixtures/zakon/` фрагменты карточек с (а) несколькими підставами в одной редакции, (б) одним законом в нескольких редакциях (этапы), (в) підставою-КСУ
- [x] итоги в `docs/spike-sources.md`; если связь законопроект↔закон не находится, ⚠️ в плане и решение, как сузить модель веток
- [x] run tests - must pass before next task (новых тестов нет, существующие зелёные)
- ✅ итог: связь законопроект↔закон найдена (`bills_acts-skl<N>.csv` + `billinfo-skl9.json`, обратная `projs` в JSON-карточке закона), покрытие III–IX ≈100%; сужать модель веток не нужно. Подробности и ⚠️ к задачам 3–6, 11, 12, 15 ниже и в `docs/spike-sources.md`

### Task 3: HTTP-клиент zakon с кешем и rate limit

**Files:**
- Create: `jurik/zakon/__init__.py`, `jurik/zakon/http.py`
- Create: `tests/test_zakon_http.py`

- [x] `ZakonClient.get(url) -> str`: браузерный User-Agent, таймаут 60 с, retry 3 раза с экспоненциальной паузой на 5xx/таймаут, минимальный интервал между запросами 2.5 с
- [x] кеш на диске gzip: `cache/zakon/<safe_id>/card.html.gz`, `cache/zakon/<safe_id>/ed<YYYYMMDD>.html.gz`; `safe_id` = zakon_id с `/` → `_`; при наличии файла сеть не трогается, `refetch=True` перекачивает
- [x] распознавать страницу-ошибку zakon (403, «Документ не знайдено», капча) и не класть её в кеш
- ⚠️ (Task 2) есть официальный API «Законодавства України»: `https://data.rada.gov.ua/laws/card/<nreg>.json` (список редакций с підставами в виде nreg, `datred`, `podid`) и `https://data.rada.gov.ua/laws/show/<nreg>/ed<YYYYMMDD>` (HTML редакции), требует `User-Agent: OpenData`, без него 302; слэш в nreg не кодировать (`%2F` → 404). Решить в начале Task 3, брать ли его вместо скрейпинга zakon (рекомендуется: стабильнее разметка и формат). `.txt`-версия теряет индексы статей (`39-1` → `391`), не использовать
- [x] write tests с `httpx.MockTransport`: попадание в кеш, промах, retry на 503, не кешируется 403, интервал соблюдается (замокать sleep), gzip-файл читается обратно
- [x] run tests - must pass before next task
- ✅ решение (Task 3): основной источник = OpenData API (`ZakonClient(source="opendata")` по умолчанию: `User-Agent: OpenData`, карточка `card.json.gz`, редакция `ed<YYYYMMDD>.html.gz` с data.rada.gov.ua). Скрейпинг zakon оставлен как `source="zakon"` (браузерный UA, `card.html.gz`, `ed<YYYYMMDD>.print.html.gz`). Редиректы не следуются (302 без UA = ошибка), 403/404/302 не ретраятся и не кешируются

### Task 4: Парсер карточки документа: список редакций

**Files:**
- Create: `jurik/zakon/card.py`
- Create: `tests/fixtures/zakon/2755-17_card.html`, `tests/fixtures/zakon/2947-14_card.html`, `tests/fixtures/zakon/254k_96-vr_card.html` (+ фрагменты из Task 2)
- Create: `tests/test_zakon_card.py`

- [x] `parse_card(html, zakon_id) -> CardInfo(title, adopted, status, editions: list[Edition])`: из блока истории выдернуть все `ed<YYYYMMDD>` с датой, типом («перша редакція», «Редакція, підстава - N, M»), списком підстав; нормализовать номера законов в вид `2856-VI`; `kind` = `court` для `18-рп/2011`-подобных, `law` для законов, `other` для остального
- [x] редакции с датой в будущем помечаются `future=True`
- ⚠️ (Task 3) источник = OpenData API: карточка приходит JSON (`ZakonClient.card()` → `card.json.gz`, поля `eds[]{datred, pidstava (nreg через запятую), comment}`, `nazva`, `orgdat`, `status`), поэтому `parse_card` разбирает JSON (фикстура `card-family.json`, добавить JSON-карточки Податкового и Конституції), HTML-фикстуры zakon остаются как сверка. Підстави уже в виде nreg
- ⚠️ (Task 2) підстава-КСУ на карточке выглядит как zakon id решения `v020p710-10` (href `/laws/show/v020p710-10`), а не `18-рп/2011`; `kind=court` определять по `^v\w+p710-\d\d$`. События «Визнання неконституційними…», «Тлумачення» без ссылки `ed…` редакций не дают
- ⚠️ (Task 2) условные редакции без даты: под заголовком «пізніше», дата текстом, ссылка `ed30000101`/`ed30000102` (в JSON `datred=30000101`) → `future=True`. Текущая редакция ссылки на `ed…` не имеет (метка «відкрито зараз»), её дату брать из строки
- ⚠️ (Task 2) номер закона нормализовать в nreg из href (`4834-17`), а не в отображаемый `4834-VI`: суффикс `VIII` неоднозначен (`322-08` 1971 vs `-19` 2014), III созыв бывает `-XIV` и `-III`; бывают буквенные суффиксы `2147<sup>а</sup>-VIII` → `2147а-19`. Отображаемый номер хранить отдельно для сообщений
- ⚠️ (Task 2) фикстуры уже есть: `tests/fixtures/zakon/card-{tax,constitution,family,labour}.html`. Актуальные числа: Сімейний 63 ссылки-редакции (62 датированные + 1 условная), Конституція 10, Податковий 242, КЗпП 147 (из них 2 условные). У КЗпП первая редакция ed19960723 с підставою 256/96-ВР, у Житлового ed19840101 «Введення в дію» с підставою 5465-X и ed19930101 без підстави
- [x] write tests на фикстурах: количество редакций (Податковий ≥ 200, Сімейний 62, Конституція 9), первая редакция с `bases=[]`, одна підстава, несколько підстав, підстава-КСУ, два этапа одного закона дают две `Edition` с одинаковым `bases`, future-редакция, порядок по дате
- [x] write tests на ошибки: карточка без блока истории → `CardParseError`
- [x] run tests - must pass before next task
- ✅ итог (Task 4): `jurik/zakon/card.py` разбирает JSON-карточку OpenData: `parse_card(text, zakon_id, today=None) -> CardInfo(zakon_id, title, number=orgnum, adopted=orgdat, status=текст по справочнику `stan`, editions)`. `Edition.bases` = nreg в нижнем регистре (`4834-17`, `256/96-вр`, `v020p710-10`); `display_number(nreg)` даёт отображаемый номер best-effort (`4834-VI`) только для сообщений. `kind`: `law`/`court`, если все підстави такого вида, иначе `other` (в т.ч. пустые). В `Edition` добавлено поле `comment` (условие условной редакции). Фактические числа: Податковий 242 (ed20261101 future на 2026-10-25), Сімейний 63 (62 + условная), Конституція 10, КЗпП 147 (3 future). Фикстуры: `card-{tax,constitution,labour}.json` рядом с HTML; HTML-карточка Сімейного используется как сверка дат

### Task 5: Команда `jurik fetch`

**Files:**
- Modify: `jurik/cli.py`
- Create: `jurik/zakon/fetch.py`, `tests/test_fetch.py`

- ⚠️ (Task 4) карточка: `parse_card(client.card(zakon_id), zakon_id)` из `jurik.zakon.card`; качать `CardInfo.current()` (не future); дата редакции для `client.edition()` = `ed.date.strftime('%Y%m%d')`; ошибка карточки = `CardParseError`
- [x] `fetch_code(code, client)`: скачать карточку, распарсить, скачать все не-будущие редакции всех zakon_id кодекса; прогресс в stdout; ошибки по одной редакции не останавливают остальные, собираются в итог
- [x] `jurik fetch [--code SLUG] [--refetch]`
- ⚠️ (Task 3) использовать `ZakonClient(cache, refetch=...)` из `jurik/zakon/http.py`: `client.card(id)`, `client.edition(id, "YYYYMMDD")`, `client.edition_path(...)`/`is_cached(...)` для проверки кеша; ошибки = `ZakonError`
- [x] write tests с MockTransport: качаются только отсутствующие в кеше редакции; future пропускается; ошибка одной редакции попадает в итог, остальные скачаны
- [x] run tests - must pass before next task
- ✅ итог (Task 5): `jurik/zakon/fetch.py`: `fetch_code(code, client, echo=print, today=None) -> FetchResult(slug, downloaded, cached, future, errors: list[FetchError(zakon_id, edition|"card", message)])`. Ошибка карточки одного zakon_id тоже не останавливает остальные id. `jurik fetch` пишет в `./cache` (кеш относительно cwd через `Paths(Path.cwd())`), при ошибках печатает их и выходит с кодом 1; клиент создаётся через `cli.make_zakon_client` (точка подмены в тестах). Смоук: Конституція 10 редакций скачана (`cache/zakon/254к_96-ВР/`)

### Task 6: Извлечение абзацев из print-редакции

**Files:**
- Create: `jurik/zakon/paragraphs.py`
- Create: `tests/fixtures/zakon/2947-14_ed20020110_print.html` (полная первая редакция Сімейного), `tests/fixtures/zakon/2755-17_ed20240101_print_fragment.html` (шапка + розділ I + глава 1 розділу XIV + начало розділу XX), `tests/fixtures/zakon/435-15_print_fragment.html` (Книга перша, розділ I, глава 1 с §), `tests/fixtures/zakon/254k_96-vr_print_fragment.html` (розділ XV перехідні положення)
- Create: `tests/test_paragraphs.py`

- [x] `extract_paragraphs(html) -> list[Para]`, где `Para(text, kind, anchor)`; `kind ∈ {heading(unit_kind, number, title), article_start(number, title), body, note, header}`; заголовки распознаются текстовыми паттернами: `Книга (перша|друга|...)`, `(Загальна|Особлива) частина`, `РОЗДІЛ [IVXLC]+`/`Розділ ...`, `Підрозділ \d+`, `ГЛАВА \d+(-\d+)?`/`Глава ...`, `§ \d+`; статья: `^Стаття \d+(-\d+)?\.`; примечания `^\{.*\}$`; класс `rvps7` только подсказка
- [x] склейка индекса статьи/главы: `39<span ...>-</span>1` → `39-1`; удаление `<a name>`, нормализация пробелов и неразрывных пробелов; апострофы как в источнике
- [x] вырезать служебные блоки страницы (панель печати, «Це не поточна редакція», футер) и шапку документа до первого заголовка/статьи в `header`
- [x] таблицы внутри статей конвертировать в markdown-таблицу, если прямоугольные, иначе в построчный текст с `|`
- ⚠️ (Task 3) редакции качаются с OpenData API (`data.rada.gov.ua/laws/show/<nreg>/ed<date>`): абзацы `<p><a name="nNNN"></a>…</p>` внутри `<div id=article>` без классов `rvps`, индекс статьи текстом `Стаття 39 - 1 .` (с пробелами), шапка с `<meta name="Nreg">`, `<h1>` названия. Фикстуры print-редакций брать оттуда же (`client.edition`), паттерны должны принимать `39 - 1` и `39-1`
- [x] write tests: число статей в Сімейному первой редакции (зафиксировать по факту, сверить с оглавлением на zakon); статья 39-1 → `article_start("39-1")`; `Книга перша` и `§ 1` → `heading` с правильным kind; нумерованные пункты розділу XX и XV Конституции → `body` после `heading`, не `article_start`; примечание → `note`; ни один `body` не пуст
- [x] write tests на ошибки: HTML без `rvps`-абзацев → пустой список + предупреждение, а не исключение (OpenData: HTML без `<div id=article>` или без абзацев → `[]` + `ParagraphWarning`)
- [x] run tests - must pass before next task
- ✅ итог (Task 6): `jurik/zakon/paragraphs.py`: `extract_paragraphs(html, *, titled_articles=None) -> list[Para]`, `Para(text, kind, anchor, unit_kind, number, title)` (frozen dataclass, поля `unit_kind/number/title` заполнены у `heading`, `number/title` у `article_start`). Берутся только дети `<div id=article>` (служебных блоков OpenData вне него нет). Индексы `39 - 1 .` склеиваются в `39-1.` во всём тексте (в т.ч. пункты `39-1.1.`), но `1 - 3` без точки (диапазоны) не трогаются. Номер книги = порядковое слово → `"1"`, частини: Загальна `"1"`, Особлива `"2"`; розділ римский (`XVIII-1`). Заголовок старой редакции в двух `<p>` («Розділ I» + «ЗАГАЛЬНІ ПОЛОЖЕННЯ») склеивается. `header` = в префиксе до первого заголовка/статьи: таблица-шапка, `[ image ]`, абзац = названию документа, абзацы в `(...)`; прочий текст префикса = `body` (преамбула Конституції). Примечания `{...}` бывают многоабзацные («{У тексті Кодексу:» … «…}»), также `/- {Зміни …}`. Таблицы: одноколоночные → по абзацу на ячейку, прямоугольные → markdown, иначе строки `a | b`. Факт: Сімейний ed20020110 = статьи 1..292 без пропусков, 7 розділів, 22 глави; Податковий ed20240101 = 308 статей; ЦК ed20260805 = 1329; Конституція ed20200101 = 166

### Task 7: Сборка дерева документа

**Files:**
- Create: `jurik/zakon/tree.py`
- Create: `tests/test_tree.py`

- ⚠️ (Task 6) вход = `extract_paragraphs()` из `jurik/zakon/paragraphs.py`; `header` выбрасывать (как `note`), `Para.number/title/unit_kind` уже разобраны. Конституція: статьи без названий, `article_start(number, title="")` + отдельный `body` с текстом первого абзаца (автоопределение по названию документа, `titled_articles=` переопределяет) → `Article.title=""`, рендер `# Стаття N.`. Исключённая статья в ЦК: `article_start("196", title="{Статтю 196 виключено на підставі Закону № 3585-IX від 22.02.2024 }")`. В Податковому розділ XIV содержит `heading(chapter, "1", "виключена.")` и сразу `heading(chapter, "1", "СПРОЩЕНА …")` (дубль номера уровня: исключённый пустой уровень выбрасывать, а не падать). В ЦК внутри § бывают ненумерованные подзаголовки «1. Загальні положення» → приходят как `body` до первой статьи (попадут в `provisions`). Подпись в конце («Президент України Л.КУЧМА», «м. Київ, … N 2947-III») приходит как `body` последнего уровня — решить, выбрасывать ли
- [x] `build_tree(paragraphs, edition) -> DocTree`: стек открытых `Unit` по рангу уровней (book > part > section > subsection > chapter > paragraph); заголовок закрывает все уровни не выше своего и открывает новый; статья собирает `body` до следующей статьи/заголовка; `body` вне статьи после заголовка уровня копится в `Unit.provisions`; `body` до первого заголовка/статьи = `preamble`; `note` выбрасывается
- [x] исключённая статья: заголовок вида `Стаття 5. Виключена` или тело из одного примечания `{Статтю 5 виключено на підставі Закону № ...}` → `Article(excluded=True)` с текстом підстави
- [x] проверка целостности: номера статей внутри кодекса уникальны; нарушение → `TreeError` с номером и позицией
- [x] write tests: дерево Сімейного (уровни и статьи сосчитаны); ЦК с Книга/Розділ/Глава/§; Конституция без глав, розділ XV в `provisions`; розділ XX Податкового с підрозділами и `provisions`; исключённая статья; дубликат номера → `TreeError`
- [x] run tests - must pass before next task
- ✅ итог (Task 7): `jurik/zakon/tree.py`: `build_tree(paragraphs, edition) -> DocTree`, `TreeError`, `RANK`. Исключённая статья → `title="Виключена"`, `body="Виключена на підставі Закону № … від …."` (из примечания `{Статтю N виключено …}` в названии или в примечаниях статьи; без підстави → `body=""`). Пустой уровень с названием «виключена.» удаляется, если следом идёт уровень того же вида и номера; другой дубль уровня среди соседей → `TreeError` (как и дубль статьи). Подпись в конце документа (с первого `body` «Президент України…»/«| Президент України |»/«***» до конца, если после нет заголовков/статей) → новое поле `DocTree.closing` (`""` если нет), в дерево не попадает. Факт: Сімейний ed20020110 = 7 розділів, 22 глави, 292 статьи; розділ VI без глав (статьи прямо в розділі)

### Task 8: Рендер дерева в файлы markdown

**Files:**
- Create: `jurik/render.py`, `jurik/translit.py`
- Create: `tests/test_render.py`, `tests/test_translit.py`

- ⚠️ (Task 7) `DocTree.closing` (подпись «Президент України Л.КУЧМА» / «м. Київ, … N 2947-III», у Конституції «***» + «Конституція України прийнята …») не входит в `units`; решить, куда рендерить (напр. в `_meta.yaml`/`closing.md` или выбросить). `Unit.provisions` может идти перед статьями уровня (ЦК: «1. Загальні положення» внутри § до первой статьи); у Конституции статьи без названия (`Article.title == ""`); исключённая статья уже имеет `title="Виключена"` и готовый `body`
- [x] `translit.slug(text, max_len=40)`: транслитерация по постановлению КМУ № 55 от 27.01.2010, нижний регистр, не-буквы → `-`, обрезка по границе слова; `translit.roman_to_int`
- [x] `render(tree, slug) -> dict[path, bytes]`: пути по правилам раскладки (`NN-slug/` из номера уровня с индексом, `art-NNNN.md`, `art-NNNN-M.md`, `provisions.md`, `preamble.md`, `_meta.yaml`); для кодекса из двух zakon_id (КУпАП) рендер объединяет два дерева в одну папку
- [x] содержимое статьи: `# Стаття N. Назва`, пустая строка, абзацы через пустую строку, файл заканчивается `\n`; `provisions.md` начинается заголовком уровня
- [x] `_meta.yaml`: title, zakon_id(s), adopted, source_url, status, edition_date
- [x] `render` детерминирован: один и тот же tree → побайтно одинаковые файлы
- [x] write tests: пути для статей 1, 39-1, 1000, 1308; `Глава 1-1` → `01-1-...`; `Розділ XIV` → `14-...`; slug с апострофом и длинным названием; `provisions.md`; детерминизм; `_meta.yaml` парсится обратно; КУпАП две части в одной папке
- [x] run tests - must pass before next task
- ✅ итог (Task 8): `jurik/translit.py`: `transliterate`, `slug(text, max_len=40)` (апострофы `'’ʼ` выбрасываются без разрыва слова, «зг» → `zgh`, `є/ї/й/ю/я` в начале слова → `ye/yi/y/yu/ya`), `roman_to_int` (`ValueError` на невалидном). `jurik/render.py`: `render(trees: DocTree | Sequence[DocTree], code: Code, *, adopted=None, status="in_force") -> dict[str, bytes]` (принимает `Code` из реестра, а не slug: нужен title для `_meta.yaml`; `adopted` = `CardInfo.adopted`). Пути от корня репозитория: `codes/<slug>/...`, словарь отсортирован по пути. Папка уровня `NN[-idx]-<slug(title)>` (без title → `NN`). `provisions.md` = `# <Розділ|Підрозділ|Глава|§|Книга> N. TITLE` + текст (у `part` с «частина» в названии = `# TITLE`); заголовки уровней без provisions нигде не рендерятся. `DocTree.closing` → `codes/<slug>/closing.md`; `preamble.md` только если преамбула непуста. Несколько деревьев (КУпАП): preamble/closing объединяются без дублей, `_meta.yaml` с `zakon_id`/`source_url` списками и `edition_date` = max; совпадение пути файла → `RenderError`. `RenderError`/`ValueError` на номер, не разбираемый как арабский/римский

### Task 9: Инвариант полноты текста

**Files:**
- Create: `jurik/verify.py`
- Create: `tests/test_verify.py`

- ⚠️ (Task 6) текст абзацев уже нормализован `paragraphs.normalize_text` (пробелы, `39 - 1 .` → `39-1.`); сторону html для сравнения брать из `extract_paragraphs` (kind `body`/`heading`/`article_start`), а не из сырого HTML, иначе разойдутся склеенные индексы; `header` и `note` не считаются
- ⚠️ (Task 7) `build_tree` выбрасывает `header` (шапка: название, «(Відомості …)», `[ image ]`) и `note`; подпись уходит в `DocTree.closing` — проверка полноты должна сравнивать с html без header-абзацев и учитывать closing так, как решит Task 8. Исключённая статья получает синтетический текст «Виключена на підставі …» (из примечания) → эти слова будут `extra`, если примечания вырезаются из html
- ⚠️ (Task 8) в рендере есть слова, которых нет в теле html-текста как абзацев, и наоборот: заголовки уровней попадают в файлы только через `provisions.md` (`# Розділ XV. TITLE`), названия уровней без provisions живут лишь в именах папок; `header` (название документа, шапка) не рендерится; подпись = `closing.md`. Сравнивать слова с учётом этого (напр. сравнивать с тем же набором абзацев, что попал в дерево, или исключать заголовки уровней с обеих сторон)
- [x] `normalize_words(text) -> list[str]`: убрать примечания `{...}`, пунктуацию, регистр; одна функция и для html-текста, и для markdown
- [x] `verify_edition(files, html) -> VerifyResult`: сначала сравнение мультимножеств слов (быстро), при расхождении поиск по статьям/единицам, чтобы назвать конкретный файл; `missing`, `extra`, первые 5 расхождений с контекстом; на Податковому укладывается в секунды, не минуты
- [x] `jurik parse [--all]` запускает проверку и печатает результат; расхождения пишутся в `report/parse-failures.md`
- [x] write tests: идентичный текст → ok; потерянный абзац → `missing` с именем файла; примечание в html не считается `missing`; тест на время для фикстуры в 100k слов (< 2 с)
- [x] run tests - must pass before next task
- ✅ итог (Task 9): `jurik/verify.py`: `normalize_words` (вырезает `{…}`, апострофы `'’ʼ` без разрыва слова, слова = `[^\W_]+` в casefold); `verify_paragraphs(paras, files)` и `verify_edition(files, html | [html…]) -> VerifyResult(words, files, missing: Counter, extra: Counter, diffs[≤5], ok, summary())`. Сторона html = абзацы `body/heading/article_start` из `extract_paragraphs`; сторона рендера = все `.md` (статьи, `provisions.md`, `preamble.md`, `closing.md`). Допуски: слова заглушки исключённой статьи (`# Стаття N. Виключена`) не считаются `extra`, заголовок пустого исключённого уровня («Глава 1 виключена.») не считается `missing`. При расхождении абзацы сопоставляются с блоками файлов (по кортежу слов), `Discrepancy(kind, file, text, context)`. **Рендер изменён**: каждый уровень всегда имеет `provisions.md` с заголовком (`# Глава 3. Шлюб`), даже без текста уровня; книга рендерится порядковым словом `# Книга перша. …`; КУпАП: одинаковый заголовок уровня из двух документов не конфликт. `jurik/parse.py`: `parse_code(code, client, edition=, all_editions=)` только из кеша (нет файла → ошибка «not cached, run `jurik fetch`»), по умолчанию последняя редакция каждого документа, КУпАП по документам; `jurik parse` пишет `report/parse-failures.md` всегда (и при успехе) и выходит с кодом 1 при сбоях. Фикстуры Сімейного/Податкового/ЦК/Конституції проходят без расхождений. Inline-примечания `{…}` внутри абзацев рендер пока оставляет в тексте (verify их игнорирует с обеих сторон)

### Task 10: Parse-only пилот

**Files:**
- Create: `docs/pilot-parse.md`
- Modify: парсер/рендер по найденным проблемам; новые фикстуры в `tests/fixtures/`

- ⚠️ (Task 9) `jurik parse` без `--all` проверяет только последнюю редакцию; читает только кеш; итог в `report/parse-failures.md` (перезаписывается). Каждый уровень теперь имеет `provisions.md` (минимум заголовок), это меняет число файлов. Inline-примечания `{…}` внутри абзацев остаются в рендере — если решать «примечания не включаются в текст», чистить в рендере/дереве (verify уже их игнорирует)
- [x] `jurik fetch --code family && jurik parse --code family --all`: все 62 редакции без `missing`
- [x] `jurik parse --code constitution --all` (10 ok)
- [x] по одной редакции Податкового (ed20240101) и Цивільного (последняя) без `missing` (+ последняя Податкового ed20260917, скачана одна через `client.edition`)
- [x] каждую найденную проблему парсера зафиксировать фрагментом-фикстурой и тестом (➕ подзадачи сюда)
  - [x] ➕ inline-примечания `{…}` убираются из текста статей/заголовков/provisions/preamble/closing (`tree.strip_notes`)
  - [x] ➕ ЦК: ненумерованные «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ» → новый `UnitKind` `final` (number `""`, закрывает все уровни, папка `prykintsevi-ta-perekhidni-polozhennia/`)
  - [x] ➕ заголовок исключённого уровня (`Розділ XVIII - 2 . {… виключено …}`, `§ 2. {Параграф 2 … виключено …}`) → `note`
  - [x] ➕ примечания об исключении с опечатками источника (`{С таттю 115 …}`, `{Cтаттю 990 …}` латинская C) и утратившая силу статья (`{Стаття 209 втратила чинність …}` → `# Стаття 209. Втратила чинність`)
  - [x] ➕ ЦК: `§ 2 Правові наслідки …` без точки распознаётся как заголовок (был приклеен к ст. 214)
  - [x] ➕ ЦК § 1 «Господарські товариства»: подзаголовки `1. Загальні положення` … `5. Акціонерне товариство` → новый `UnitKind` `group` (ниже §; были приклеены к концу предыдущей статьи)
  - фикстуры `tests/fixtures/zakon/435-15_ed20260805_pilot_fragment.html`, `2755-17_ed20260917_pilot_fragment.html`, тесты `tests/test_pilot_cases.py`
- [x] результаты, время парсинга и список нерешённых случаев в `docs/pilot-parse.md`
- [x] run tests - must pass before next task
- ✅ итог (Task 10): все проверенные редакции проходят (Сімейний 62/62, Конституція 10/10, ПК ed20240101 + ed20260917, ЦК ed20260805), ~50 мс на редакцию Сімейного, ~0.7 с на ПК. Проверка слов проходила и до исправлений: все найденные проблемы структурные (текст не в том файле/уровне), их ловили только ручной просмотр и сканы рендера. `model.UnitKind` теперь `final | book | part | section | subsection | chapter | paragraph | group`; `tree.RANK`: final=-1, group=6; `render.unit_heading`: final → `# TITLE`, group → `# N. TITLE`. Нерешённое: одиночные метки пунктов после удаления примечаний (`174.5.`, 5 случаев в ПК) оставлены намеренно

### Task 11: Клиент открытых данных Рады и индекс законов

**Files:**
- Create: `jurik/rada/__init__.py`, `jurik/rada/client.py`, `jurik/rada/index.py`, `jurik/rada/match.py`
- Create: `tests/fixtures/rada/` (обрезанные JSON по 2–3 законопроектам с голосованием, один отклонённый)
- Create: `tests/test_rada_client.py`, `tests/test_rada_index.py`, `tests/test_match.py`

- [x] `RadaClient.fetch_convocation(n)`: скачать файлы, найденные в Task 2, в `cache/rada/skl<n>/`, тот же кеш-подход, что в Task 3
- ⚠️ (Task 2) файлы: `https://data.rada.gov.ua/ogd/zpr/skl<n>/bills_{main,acts,initiators,passings,documents}-skl<n>.csv` (III–VIII полны; IX CSV заморожены с 2025-01, для IX брать `https://data.rada.gov.ua/ogd/zpr/skl9/billinfo-skl9.zip` → JSON 142 МБ, парсить `strict=False`); голосования `https://data.rada.gov.ua/ogd/zal/ppz/skl<n>/plenary_{agenda,event_question,result_event}-skl<n>.csv`. Поля и способ стыковки в `docs/spike-sources.md`
- ⚠️ (Task 2) ключ закона в индексе = nreg (`act_number` `229-IX`/`229-ІХ` кириллицей/`650-XIV` → `229-20`, `650-14`); голосование «в цілому» искать по номеру законопроекта (`plenary_agenda.number_question` или `№NNNN` в `name_event`) и дате = дата принятия (IX: 96% законов находятся); id законопроекта IX в `billinfo` (`itd …/Bills/Card/<id>`) не совпадает с `bill_id`/`pf3511` из CSV, стыковать по (созыв, `registrationNumber`)
- [x] `build_index(cache) -> LawIndex` с `by_law[law_number] -> LawMeta` и `bills[BillRef] -> LawMeta`; при нескольких голосованиях брать «прийнято в цілому»; `Person.slug` = транслит ФИО
- [x] `match.py`: для каждого кодекса набор регулярок по названию законопроекта в разных падежах (`Податков(ого|ому|ий) кодекс(у|і)`), таблица в `codes.yaml` поле `title_patterns`; `bills_for_code(slug) -> list[LawMeta]` всех статусов. Известное ограничение: законопроекты «до деяких законодавчих актів» без имени кодекса не находятся; доля записывается в отчёт
- [x] для принятых законов связь кодекс↔закон берётся из підстав редакций, а не из названий
- [x] `jurik rada [--convocation N]` качает, строит индекс, печатает статистику покрытия
- [x] write tests на фикстурах: `LawMeta` собран полностью; закон без голосования → `votes=None`; отклонённый → `status=rejected`; таблица реальных названий → правильный slug, «до деяких законодавчих актів» → не матчится; битый JSON → ошибка с именем файла
- [x] run tests - must pass before next task
- ✅ итог (Task 11): `jurik rada` (все созывы, ~30 МБ кеша, ~9 с на индекс): законов 6932, с голосованием 5944 (86%: III 50% — данные голосований III только 1999-09…2002-03; IV 70%; V–IX 92–99%), с инициаторами 6910 (~100%); підстави редакций кэшированных кодексов находятся 100% кроме `2147а-19` (Сімейний 63/64, Цивільний 201/202, Податковий 238/238, Конституція 7/7). Не принятые законопроекты «до деяких законодавчих актів» без имени кодекса: 4166/4348 (96%) не матчатся
- ✅ API (Task 11): `RadaClient(cache, refetch=...)` из `jurik/rada/client.py` (`fetch_convocation(n)` → `cache/rada/skl<n>/*.csv.gz` и `billinfo-skl9.zip`); `build_index(cache, convocations) -> LawIndex` из `jurik/rada/index.py`, `index.law(nreg)`, `index.by_law[nreg]`, `index.bills[BillRef]` (законопроекты-законы всех статусов, постанови не индексируются); `LawMeta.law_number` = nreg (`524-16`, `""` если не закон), новое поле `LawMeta.display_number` (`524-V`) для сообщений; `Person.name` = «Ім'я Прізвище», `Person.slug` = транслит «прізвище-імя-по-батькові»; `jurik/rada/match.py`: `bills_for_code(index, code, bases)` (принятые = из `bases`, остальные по `title_patterns`), `match_codes`, `omnibus_unmatched`, `coverage_report`

### Task 12: Builder: change set'ы и линейная история в `main`

**Files:**
- Create: `jurik/build/__init__.py`, `jurik/build/changeset.py`, `jurik/build/fastimport.py`, `jurik/build/history.py`, `jurik/build/message.py`
- Create: `tests/test_changeset.py`, `tests/test_fastimport.py`, `tests/test_history.py`, `tests/test_message.py`

- ⚠️ (Task 10) у уровней есть два новых вида: `final` (ненумерованные «ПРИКІНЦЕВІ ТА ПЕРЕХІДНІ ПОЛОЖЕННЯ» ЦК, папка без числового префикса) и `group` (подзаголовки `N. Назва` внутри § ЦК); inline-примечания `{…}` из текста уже вырезаны деревом, так что правки одних примечаний не меняют файлы
- ⚠️ (Task 11) для подписей `initial`-коммитов и `Law:` трейлеров: ключ закона = nreg (`Edition.bases` тоже nreg), `index.law(nreg)` → `LawMeta`, человекочитаемый номер `LawMeta.display_number` (или `jurik.zakon.card.display_number(nreg)`, если закона нет в индексе)
- ⚠️ (Task 8) `render(trees, code, adopted=..., status=...)` уже отдаёт пути от корня репозитория (`codes/<slug>/...`), для КУпАП передавать список из двух деревьев (последние редакции каждой части на дату); repealed = удалить все пути с префиксом `codes/<slug>/`
- [x] `changeset.diff(prev_files, new_files) -> ChangeSet(modified: dict[path, bytes], deleted: set[path])`
- [x] `fastimport.py`: `Stream` с дедупликацией blob'ов по sha256 (mark на содержимое), `commit(ref, parents, author, committer, message, changeset)`; `run(stream, repo_path)` создаёт репозиторий и прогоняет `git fast-import`; timestamp через `zoneinfo("Europe/Kyiv")` 12:00
- [x] `history.plan_linear(editions_by_code, trees) -> list[Step]`: сортировка по `(date, bases)`, объединение шагов с одинаковыми `(date, bases)` между кодексами; КУпАП: состояние папки = объединение последних редакций обеих частей; шаг утраты силы кодекса (`Reason: repealed`, удаление папки)
- [x] авторы линейных шагов по таблице (`initial`, `court`, `unknown`, `repealed`); `message.py` форматирует сообщения с обязательным `In-force:`
- ⚠️ (Task 2) у старых кодексов первая доступная редакция не текст при принятии (КЗпП: ed19960723, підстава 256/96-ВР; Житловий: ed19840101, підстава 5465-X), коммит `initial` датировать датой этой редакции и указывать её підставу; Житловий 15.02.2026 «Втрата чинності крім окремих положень» с текстом редакции: это не `repealed` с удалением папки
- [x] `jurik build --linear` собирает `build/laws` только линейно
- [x] write tests: change set между двумя рендерами содержит ровно изменённые/удалённые пути; поток для трёх коммитов создаёт репозиторий с правильными деревьями, датами и `In-force:` (проверка `git log --format` и `git show --stat`); blob один раз при одинаковом содержимом; объединение редакций с общими підставами; КУпАП-объединение; repealed удаляет папку; ненулевой код `git fast-import` → исключение с stderr
- [x] run tests - must pass before next task
- ✅ итог (Task 12): `jurik/build/changeset.py`: `ChangeSet(modified, deleted)` с `update()` (поздние правки побеждают) и `apply()`, `diff(prev, new)`. `jurik/build/fastimport.py`: `Ident(name, email, when: date)` (12:00 Europe/Kyiv, смещение из tzdata), `Stream(out=None)` пишет во временный файл, `blob()` дедуп по sha256, `commit(ref, parents, author, committer, message, changeset) -> mark` (`parents[0]` = `from`, остальные `merge`, без родителей = root), `run(stream, repo)` пересоздаёт репозиторий (удаляет `--out` только если он пуст или git-репо), `git fast-import --quiet`, затем `reset --hard main`; ошибка git → `FastImportError` со stderr. `jurik/build/message.py`: `format_message` (без `In-force:` → `MessageError`), `parse_trailers`, `law_trailers` (`Law:`+`Bill:` на каждый закон, `Basis:` для не-законов, `Adopted:`/`Signed:` только при одном законе), `step_message`. `jurik/build/history.py`: **сигнатура изменена** на `plan_linear(sources: list[CodeSource], load_tree: Callable[[Edition], DocTree], *, index: LawIndex | None = None) -> list[Step]` (деревья грузятся лениво, в памяти только последнее дерево каждого документа и последний рендер каждого кодекса); `Step(date, reason, bases, editions, codes, changes, author, committer, message)`; `write_linear(steps, stream)`; `sources_from_cache(codes, client)` (все in-force редакции должны быть в кеше, иначе `BuildError`), `cached_tree_loader(client)`. Группировка: `(date, sorted bases)` для непустых підстав; редакции без підстав не объединяются между документами; первая редакция каждого документа = `initial` (обе части КУпАП одного дня = один шаг); порядок в один день: initial → редакции (по bases, затем zakon_id) → repealed. Reasons: `initial|law|multiple|court|unknown|other|repealed` (`other` = одна не-закон/не-КСУ підстава, автор Апарат). Каждый коммит несёт трейлеры `Edition: <zakon_id>/edYYYYMMDD` (по одному на редакцию) — для проверки (a). `Code.repealed`/`repealed_by` в `codes.yaml` (дату утраты силы карточка явно не даёт). `jurik build --linear [--code A --code B]` (`--code` повторяемый), индекс Рады опционален (нет кеша → предупреждение). Смоук: Сімейний+Конституція 72 коммита (62+10), 1023 blob'а, 7.2 с (из них ~5 с индекс Рады), `.git` 812 КБ; рабочее дерево Сімейного проходит `verify_edition` против ed20260805


### Task 13: Builder: ветки законов и merge-коммиты

**Files:**
- Modify: `jurik/build/history.py`, `jurik/build/fastimport.py`, `jurik/build/message.py`
- Create: `jurik/build/refs.py`
- Modify: `tests/test_history.py`, `tests/test_message.py`; Create: `tests/test_refs.py`

- ⚠️ (Task 11) `LawIndex` из `jurik.rada.index.build_index(paths.cache)` (читает только кеш, `RadaError` если `jurik rada` не запускался); `index.law(nreg)` по підставі редакции; `LawMeta.bill` = `BillRef(conv, number)`, номер как в источнике (`0903`, `15112-д`, `1234/П`), `votes=None` у ~14% законов (в трейлере `Adopted:` писать без голосов), `registered`/`signed` бывают `None`; `initiators` может быть пустым (22 закона) → author «Верховна Рада України»; у внешних инициаторов (КМУ, Президент) Person = подписавший чиновник (`Віктор Янукович`), если ФИО нет — организация
- ⚠️ (Task 12) `plan_linear(sources, load_tree, *, index=None)` возвращает `Step` с готовым `message`, `author`/`committer` = `Ident(name, email, when)`; `plan_full` может переиспользовать группировку `_events`/`groups` и рендер; `Stream.commit(ref, parents, ...)` уже поддерживает merge (`parents[1:]`). Трейлеры `Edition:` есть на каждом коммите `main` — сохранить их в merge. В линейном режиме `Law:`-шаги авторствует Рада с датой вступления в силу. Заголовок закона из индекса бывает «Пропозиції Президента України до Закону …» (напр. 2541-IX) — квирк индекса Task 11
- [x] `refs.ref_safe(s)` и `refs.bill_ref(BillRef) -> "bill/<conv>/<number>"`, `refs.law_ref(law_number) -> "bill/law/<safe>"`
- [x] `plan_full(..., law_index)`: для шага с підставами-законами найти `LawMeta` первого закона; `Step(branch)` от `main_at_registered` (последний коммит `main` с `In-force:` ≤ регистрация, иначе родитель merge) с change set; `Step(merge)` с тем же change set; несколько підстав → трейлеры `Law:`/`Bill:` на каждый, `Reason: multiple`; второй этап того же закона → новый коммит в той же ветке `Stage: N` и повторный merge
- [x] author/committer/даты по таблице из Technical Details; email инициатора `<slug>@deputies.invalid`
- [x] `jurik build` без `--linear` использует `plan_full`
- [x] write tests: ветка отходит от правильного коммита; merge имеет двух родителей и дерево = новый рендер; `git diff bill^ bill` затрагивает только файлы закона, когда между регистрацией и принятием влился другой закон; staged-закон даёт два merge одной ветки; multi-basis даёт один merge с двумя `Law:`; закон без LawMeta → `bill/law/…`; `ref_safe` на `254к/96-ВР`, `1234-д`, `a..b`; author/committer и даты через `git log --format=%an|%ae|%ad|%cn|%cd`
- [x] run tests - must pass before next task
- ✅ итог (Task 13): `jurik/build/refs.py`: `ref_safe` (транслит, **lowercase** — refs лежат файлами на регистронезависимых ФС, `/`→`_`, запрещённые git символы → `-`, `..`→`.`, `@{`→`@`, срез `.`/`-` по краям и `.lock`), `bill_ref(BillRef)`, `law_ref(nreg)` (ключ = nreg: `bill/law/2147a-19`). `Step` получил `ref`, `parents` (индексы шагов плана, `[0]` = first parent), `kind` (`commit|branch|merge`), `stage`, `branch`; `write_steps(steps, stream)` (`write_linear` = алиас). `plan_full(sources, load_tree, *, index=None)`: шаги с хотя бы одной підставою-законом (кроме `initial`/`repealed`) = ветка + merge, ветку называет первый закон по nreg; база ветки = последний коммит `main` с `In-force:` ≤ `registered`, но не раньше `initial`-коммита затронутых кодексов; нет `registered`/LawMeta → от родителя merge; следующий этап → коммит поверх прошлого коммита ветки. Коммит ветки: автор = первый инициатор (`<slug>@deputies.invalid`, нет инициаторов → Рада), дата = регистрация (иначе принятие, иначе вступление в силу), сообщение `Законопроект № N: …` с `Law:`/`Bill:`/`Registered:`/`Stage:`/`Co-authored-by:` **без `In-force:` и `Edition:`** (`format_message(..., require_in_force=False)`). Merge: Рада, дата = `adopted` (иначе вступление в силу), трейлеры как в примере + `Stage:` + `Edition:`, `Bill: unknown` для законов без законопроекта. `message.law_title(meta)` снимает префикс «Пропозиції Президента України до Закону "…"». `jurik build` без `--linear` = `plan_full`, печатает число веток/merge. Смоук Сімейний+Конституція: 140 коммитов (72 в `--first-parent main`: 2 initial, 65 law, 3 multiple, 1 court, 1 unknown), 68 веток / 68 merge, 7.4 с; дерево `main` побайтно совпадает с `--linear` сборкой


### Task 14: Инварианты собранного репозитория

**Files:**
- Create: `jurik/build/check.py`
- Create: `tests/test_check.py`

- ⚠️ (Task 9) для (c) использовать `jurik.verify.verify_edition(files, html)` (html может быть списком для КУпАП); `files` = `.md` из рабочего дерева кодекса
- ⚠️ (Task 12) для (a) использовать трейлеры `Edition: <zakon_id>/edYYYYMMDD` (zakon_id может содержать `/`, напр. `254к/96-ВР/ed19960628` — резать по последнему `/ed`); `message.parse_trailers(text)` разбирает трейлеры; коммиты с пустым change set существуют (редакция без видимых изменений текста)
- ⚠️ (Task 13) коммиты веток `bill/*` достижимы из `main` через второго родителя, но не несут `In-force:`/`Edition:`: проверки (a)/(b) делать по `git log --first-parent main`; у `Step` есть `kind`/`ref`/`parents`/`branch` для (d)
- [x] `check_repo(repo_path, steps, trees) -> list[Violation]`: (a) каждая редакция отображена ровно в один коммит `main` (по `In-force:` + `Law:`); (b) `In-force:` по `--first-parent main` не убывает; (c) рабочее дерево `main` по словам совпадает с print последней редакции каждого кодекса (переиспользует Task 9); (d) у каждой ветки `bill/*` с merge ровно один путь в `main`
- [x] `jurik build` запускает `check_repo` в конце; ненулевой exit code при нарушениях
- [x] write tests: мини-репозиторий из 3 синтетических редакций без нарушений; подменить `In-force:` → (b); пропустить редакцию → (a); испортить файл → (c)
- [x] run tests - must pass before next task
- ✅ итог (Task 14): `jurik/build/check.py`: `check_repo(repo_path, steps, prints) -> list[Violation]` (`Violation(check="a"|"b"|"c"|"d", message)`); третий аргумент = `prints: {slug: [print html последней редакции каждого документа]}` (не деревья: (c) идёт через `verify_edition`, которому нужен html), отменённые кодексы не передаются. (a) по трейлерам `Edition:` в `--first-parent main`: каждая редакция из шагов `main` ровно в одном коммите и `In-force:` коммита = дата редакции (`Law:` не сверяется — там display-номер, а не nreg); (b) `In-force:` есть и не убывает; (c) `.md` под `codes/<slug>/` читаются из дерева `main` через git (не с диска); (d) для каждой ветки, которую план мержит, её tip — второй родитель ровно одного коммита `--first-parent main`. Также `check_editions`/`check_in_force`/`check_text`/`check_branches` по отдельности. `jurik build` печатает `check_repo: 0 violations` или `VIOLATION (x) …` в stderr и exit 1; `cli.latest_prints(sources, client)`. Смоук Сімейний+Конституція (full и `--linear`): 0 нарушений

### Task 15: Тексты законопроектов и ветки отклонённых

**Files:**
- Create: `jurik/rada/billtext.py`
- Modify: `jurik/build/history.py`
- Create: `tests/fixtures/rada/bill.docx`, `tests/fixtures/rada/bill.pdf`
- Create: `tests/test_billtext.py`; Modify: `tests/test_history.py`

- [x] `billtext.fetch(bill) -> str | None`: выбрать документ «текст законопроекту» по признаку из Task 2; скачать в `cache/rada/bills/<conv>/<n>.<ext>`; docx → markdown через `python-docx`, pdf → текст через `pypdf` (добавить зависимости); нет файла/ссылки → `None`
- ⚠️ (Task 2) признак: III–VIII `bills_documents.document_type == «Проект Закону»` (также «Проект Закону (на заміну)», «Проект Кодексу»), ссылка `webproc34?...pf35401=<id>` отдаёт **html (cp1251), rtf или doc (Word 97)**, не только docx/pdf: нужна конвертация rtf/doc (например через `striprtf`, `antiword`/LibreOffice) или заглушка «Текст недоступний» для них. IX: `billinfo.documents.source[kind=«Проєкт Закону»].docFiles[type=«Проєкт Закону»]` (pdf/docx); `docFiles.url` даёт HTML-страницу, сам файл `GET https://itd.rada.gov.ua/billinfo/api/file/download/` с заголовками `X-File-Id`, `X-Current-Chunk` (ответ: `Size`, `ChunkSize`, `Type`)
- ⚠️ (Task 11) `bills_for_code(index, code, bases)` из `jurik/rada/match.py` (сигнатура отличается от `bills_for_code(slug)`: нужны индекс, `Code` и підстави редакций); не принятые законопроекты по названию: Податковий ~2072, Конституція ~216, ЦК ~266, Сімейний ~90 → `--bills-limit` реально нужен. Ссылки на документы в `LawMeta` не хранятся: `bills_documents-skl<n>.csv.gz` (III–VIII) и `billinfo-skl9.zip` (IX) уже в `cache/rada/skl<n>/`, читать через `jurik.rada.index.read_csv`/`read_billinfo`
- ⚠️ (Task 13) `plan_full` строит `Step(kind="branch", ref=..., parents=(индекс шага main,))`; база по той же логике, что `main_at_registered` в `plan_full` (список `(In-force, index)` коммитов `main`, `bisect_right`), сообщение ветки через `message.branch_message`/`format_message(require_in_force=False)`; `--bills-limit` в CLI объявлен, но пока не используется
- [x] `plan_full`: для каждого `LawMeta` из `bills_for_code` со статусом ≠ adopted создать `Step(branch, ref=bill_ref)` с одним файлом `bills/<conv>/<n>.md` (текст или заглушка «Текст недоступний» + ссылка на карточку), author = инициатор, date = регистрация, сообщение со статусом и датой исхода; без merge
- [x] `--bills-limit N` для отладки и для ограничения полной сборки
- [x] write tests: docx-фикстура → markdown с абзацами; pdf-фикстура → текст; нет ссылки → `None`; ветка без merge создана, `main` не изменился; ref с суффиксом `-д`
- [x] run tests - must pass before next task
- ✅ итог (Task 15): `jurik/rada/billtext.py`: `BillTexts(cache, client=None, echo=)` с методом `fetch(bill: BillRef) -> str | None` (не модульная функция: нужен кеш и http-клиент); документ = последний по дате `source`-документ типа «Про(е|є)кт Закону|Кодексу» (+«(на заміну)», «(доопрацьований)»), у IX docx предпочтительнее pdf; формат определяется по сигнатуре (имя файла `doc_N.rtf` бывает Word 97). Конвертеры: docx (`python-docx`, заголовки `#`, таблицы `| a | b |`), pdf (`pypdf` + склейка строк в абзацы), rtf (`striprtf`, «машинописные» rtf/`<pre>` склеиваются по отступам), html (lxml, cp1251); `.doc` (Word 97) → `None` + заглушка (кешируется, повторно не качается). Кеш `cache/rada/bills/<conv>/<n>.<ext>` (`/` → `_`). `RadaClient.request(url, headers)` отдаёт ответ с заголовками (chunked API IX). `LawMeta` получил `card_url` и `phase_date` (дата текущей фазы = дата исхода). `plan_full(..., bill_texts=, bills_limit=)`: ветки не принятых законопроектов (`rejected_bills(sources, index)`, по дате регистрации, первые `bills_limit`) идут в конце плана, `Step(kind="branch", reason="bill", stage=1)`, файл `bills/<conv>/<ref_safe(n)>.md` (ASCII, `1234-д` → `bills/4/1234-d.md`, как имя ветки), сообщение `message.rejected_bill_message` (трейлеры `Bill`, `Registered`, `Status`, `Status-date`, `Source` = карточка, `Co-authored-by`). Смоук Сімейний+Конституція `--bills-limit 5`: 5 веток `bill/3/*` (все rtf), `main` побайтно тот же, что при `--bills-limit 0`, 0 нарушений


### Task 16: Отчёты и экспорт датасета

**Files:**
- Create: `jurik/report.py`, `jurik/export.py`
- Create: `tests/test_report.py`, `tests/test_export.py`

- ⚠️ (Task 11) долю не найденных законопроектов «до деяких законодавчих актів» брать из `jurik.rada.match.omnibus_unmatched(index, codes)`; готовые строки покрытия open data: `coverage_report(index, codes, bases_by_code)`, підстави из кеша карточек: `jurik.cli.cached_code_bases(codes, cache)`
- [ ] `jurik report`: `report/coverage.md` (по кодексу: редакций всего / распарсено / с законопроектом в open data / с голосованием; веток отклонённых; доля законов без LawMeta), `report/parse-failures.md`
- [ ] `export.commit_at(repo, date)`: последний коммит `--first-parent main` с `In-force:` ≤ date (не `--before`)
- [ ] `jurik export --at DATE --out DIR`: `git archive` этого коммита → папка markdown + `manifest.json` (дата, коммит, список кодексов)
- ⚠️ (Task 15) ветки не принятых законопроектов: шаги `Step(kind="branch", reason="bill")` (в отчёте «веток отклонённых» считать их), в git — ветки `bill/*` с одним коммитом, которые не достижимы из `main`; файл `bills/<conv>/<ref_safe(n)>.md` (ASCII-имя, `1234-д` → `1234-d.md`); статус бывает не только `rejected`, но и `withdrawn`/`removed`/`pending` (трейлер `Status:`, строка `- Статус: …` в файле) — пометку `--with-bills` строить из фактического статуса, а не всегда «відхилено»
- [ ] `--with-bills`: дополнительно выгрузить `bills/**/*.md` из веток без merge, добавив в начало каждого файла строку `> Законопроект № N (созыв C), статус: відхилено. Не є чинним законом.`
- [ ] write tests: отчёт на синтетических данных содержит правильные числа; `commit_at` между двумя `In-force:` даёт более ранний даже если committer-дата позднего раньше; `--with-bills` добавляет пометку
- [ ] run tests - must pass before next task

### Task 17: Полная сборка

**Files:**
- Create: `docs/pilot-build.md`
- Modify: `codes.yaml` при необходимости

- ⚠️ (Task 12) заполнить `repealed: YYYY-MM-DD` и `repealed_by: <nreg>` у `commercial` в `codes.yaml` по карточке 436-15 (иначе папка не удаляется); `jurik build` пересоздаёт `--out` целиком и требует в кеше все in-force редакции выбранных кодексов
- [ ] `jurik rada` для всех созывов; `jurik build --code family`, затем `--code constitution`, затем все; Податковий последним; проблемы фиксируются фикстурой + тестом (➕ подзадачи сюда)
- [ ] зафиксировать время сборки, размер `.git`, число коммитов/веток, `report/coverage.md` в `docs/pilot-build.md`
- [ ] run full test suite: `uv run pytest`
- [ ] run tests - must pass before next task

### Task 18: Verify acceptance criteria

- [ ] `build/laws`: число коммитов `--first-parent main` = число шагов из отчёта; число веток `bill/*` = число законов с ветками + отклонённых
- [ ] `git log --follow codes/tax/.../art-0014.md` показывает историю статьи с осмысленными сообщениями
- ⚠️ (Task 14) `check.check_text`/`_main_files` читают дерево `main` (константа); для сверки на дату использовать `verify_edition` на файлах экспорта или параметризовать rev
- [ ] `jurik export --at 2015-01-01` даёт текст, совпадающий по словам с zakon на эту дату для Сімейного и Податкового (инвариант (c) на произвольной дате)
- [ ] ручная выборка: 20 случайных merge-коммитов сверены глазами с текстом закона «Про внесення змін» на zakon; результат в `docs/pilot-build.md`
- [ ] `git diff` случайной ветки против её базы содержит только статьи, упомянутые в законе
- [ ] run full test suite: `uv run pytest`; `uv run ruff check`

### Task 19: [Final] Update documentation

- [ ] `README.md`: назначение, команды CLI, как клонировать `laws` и читать историю (`git log --first-parent`, `git log --follow`), объяснение осей времени (committer date = принятие, `In-force:` = действие), пометка об источниках и обязательном указании источника данных
- [ ] `build/laws/README.md` генерируется builder'ом: структура, модель веток, поля сообщений, предупреждение про ветки `bill/*`
- [ ] создать `CLAUDE.md` с конвенциями проекта (uv, pytest, фикстуры, сетевые тесты выключены)
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

**Manual verification**:
- публикация `build/laws` на GitHub как отдельного репозитория; проверить, что GitHub показывает merge-коммиты и список веток без тормозов при тысячах веток; если тормозит, перенести ветки отклонённых в `refs/bills/*` (отложено как YAGNI)
- прогон экспорта через реальный LLM-пайплайн, проверить, что markdown статей читается без постобработки

**External / follow-up plans**:
- этап 2: все законы Украины (расширение `codes.yaml` до реестра законов, тот же пайплайн)
- этап 3: репозиторий `bylaws` и подключение submodule
- подход C: парсер текста правок «статтю N викласти в такій редакції», валидация на принятых законах, применение к отклонённым законопроектам вместо `bills/<n>.md`; разделение multi-basis редакций по законам
- живой режим: периодический `fetch` новых редакций и инкрементальное добавление коммитов без пересборки, настоящие PR на GitHub для законопроектов на рассмотрении
- условия использования: zakon.rada.gov.ua и data.rada.gov.ua требуют указания источника; проверить, нет ли ограничений на массовое скачивание
