# jurik: конвенции проекта

Python-пакет `jurik`: собирает git-репозиторий `build/laws` с Конституцией и кодексами Украины
и историей законов (см. `README.md`). План и решения: `docs/plans/`, факты об источниках:
`docs/spike-sources.md`, отчёты пилотов: `docs/pilot-parse.md`, `docs/pilot-build.md`.

## Окружение и команды

- Только `uv`: `uv sync`, `uv run jurik …`, `uv run pytest`, `uv run ruff check`,
  `uv run ruff format`. Python ≥ 3.12, нужен `git` в `PATH`.
- Перед коммитом: `uv run pytest` и `uv run ruff check` зелёные (ruff: line-length 100,
  правила `E F W I UP B SIM`).
- Не коммитить `cache/`, `build/`, `report/` (в `.gitignore`). Кеш никогда не чистится
  автоматически; полный `fetch` всех кодексов ≈ 2,5 ч, не перекачивать без нужды.

## Тесты

- Каждое изменение кода сопровождается тестами в `tests/test_<модуль>.py` (успех и ошибки).
- Сетевые тесты (реальные запросы к rada.gov.ua) помечаются `@pytest.mark.network` и
  выключены по умолчанию (`addopts = "-m 'not network'"`); включить: `uv run pytest -m network`.
  Остальные тесты работают офлайн на фикстурах.
- Фикстуры: `tests/fixtures/zakon/` (карточки, print/OpenData HTML редакций) и
  `tests/fixtures/rada/` (открытые данные, документы законопроектов). Крупные страницы
  обрезаются до репрезентативного фрагмента < 200 КБ (`*_fragment.html`); целиком хранится
  только первая редакция Сімейного кодексу. Найденный на реальных данных сбой парсера =
  обрезанная фикстура `*_build_fragment.html` + тест (`tests/test_build_cases.py`,
  `tests/test_pilot_cases.py`).
- Хелперы для проверки собранных репозиториев: `tests/gitrepo.py` (`git`, `tree_files`);
  фикстуры `fixtures_dir`, `rada_cache`, `write_registry` в `tests/conftest.py`.

## Код

- Модули: `jurik/zakon/` (HTTP-клиент, карточка, абзацы, дерево), `jurik/rada/` (открытые
  данные, индекс законов, сопоставление законопроектов, тексты), `jurik/render.py`
  (раскладка файлов), `jurik/verify.py` (полнота по словам), `jurik/build/` (план истории,
  сообщения, `fast-import`, инварианты, README собранного репозитория), `jurik/report.py`,
  `jurik/export.py`, `jurik/cli.py` (click).
- Парсер опирается на текстовые паттерны, классы `rvts*`/`rvps*` только подсказка.
- Содержимое собранного репозитория (сообщения коммитов, заголовки, `build/laws/README.md`)
  на украинском; пути и имена веток только ASCII (`translit.slug`, `refs.ref_safe`).
- Ось «состояние на дату» = трейлер `In-force:` по `--first-parent main`, никогда не
  committer date (она = дата принятия закона). Каждая проверка/запрос истории идёт по
  `--first-parent main`. Коммиты веток `bill/*` не несут `In-force:`/`Edition:`
  (`format_message(require_in_force=False)`).
- `In-force:` редакции = дата редакции или вступление документа в силу (событие карточки
  `podid` 1), если оно позже; инвариант (a) сверяет с датой из плана, не из `Edition:`.
- Коммит ветки закона и его merge несут один change set, база ветки выбирается так, чтобы
  `git diff M^2^ M^2` == `git diff M^1 M` (инвариант (e)); не добавлять в отрендеренные
  файлы данные, меняющиеся в каждой редакции (поэтому в `_meta.yaml` нет даты редакции).
- `ref_safe` переводит в нижний регистр (case-insensitive FS); коллизии имён веток
  решаются суффиксом `-2` (`_unique_ref`).
- Сгенерированный `build/laws/README.md` входит в change set первого коммита `main`, чтобы
  число коммитов `main` = шагам плана (инвариант (a)).
- Даты `repealed`/`repealed_by` в `codes.yaml` заполняются вручную по карточке.
- `jurik build` удаляет `--out`, только если это репозиторий с `jurik.built` в
  `.git/config`; «сегодня» = `kyiv_today()`.
- Ошибки доменных шагов = собственные исключения (`BuildError`, `RenderError`, `ZakonError`,
  …), CLI превращает их в `click.ClickException`.
- Код, docstring и отчёты пилотов (`docs/pilot-*.md`) на английском; `README.md`, план и
  `docs/spike-sources.md` на русском.
