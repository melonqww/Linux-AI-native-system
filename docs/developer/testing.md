# Проверка проекта без локальной Linux-машины

Основной набор запускается одинаково на Windows и Ubuntu:

```powershell
python -m pip install pytest pypdf zstandard
python -m pytest -q
python -m compileall -q services modules apps tests labs/ai-scenario-lab/src
node --check apps/desktop-panel/gnome-extension/extension.js
```

Корневой `conftest.py` автоматически подключает `src` всех monorepo-пакетов и
AI Scenario Lab. Корневой `python -m pytest -q` обязательно собирает быстрые
unit/integration тесты лаборатории; они не обращаются к Ollama и не скачивают
модель. Live model campaigns остаются отдельными явными прогонами.

## Что проверяется

- manifests, уникальность module ID, зависимости и безопасные entrypoint paths;
- получение лабораторией user-intent маршрутов через тот же production
  Capability Registry, включая исчезновение маршрута отключённого модуля;
- сквозной runtime-каталог: синтетическая новая операция без центральной таблицы,
  отбор только capabilities с handler и конфликт schema/policy;
- точный состав найденных и скопированных файлов для search/correction journey,
  а не только совпадение общего количества;
- импорт и process lifecycle всех first-party modules;
- отсутствие `os.system`, `os.popen` и `subprocess(..., shell=True)` в production-
  Python;
- permissions, path containment, symlinks и исключение чувствительных файлов;
- одноразовое подтверждение и rollback materialize/copy;
- R0-only loopback bridge, Unix socket modes/stale cleanup, IPC schema и настоящий
  Linux `SO_PEERCRED` allow/deny;
- fail-closed Permission Gateway: risk/approval/phase/transport/scopes/arguments,
  runtime revoke, disabled providers, handler concurrency и cooperative deadlines;
- Task Ledger: закрытые переходы состояний, cooperative cancel, checkpoint resume,
  restart recovery, семидневный retention, redacted events и конкурентный progress;
- закрытая intent schema, RU/EN планы, доверенный task context и prompt injection;
- битые, слишком большие, многостраничные и требующие OCR PDF;
- coalescing/overflow очереди, load pause и инкрементальные create/modify/delete;
- чистый декодер inotify на каждой ОС;
- настоящий inotify event на Ubuntu runner.

Workflow `.github/workflows/ci.yml` запускает весь корневой набор, включая быстрые
тесты лаборатории, на `windows-latest` и
`ubuntu-24.04` после push и в pull request. Linux-only тест локально на Windows
показывается как `skipped`; успешный Ubuntu job является обязательной фактической
проверкой адаптера.

Локальный Qwen eval является opt-in, потому что CI не скачивает многогигабайтную модель:

```powershell
$env:AI_NATIVE_RUN_OLLAMA_EVALS = "1"
python -m pytest -q services/intent-compiler/tests/test_ollama_live.py
```

Он проверяет RU/EN классификацию, составной search→copy, доверенные ссылки
«их/туда» и containment prompt injection. Обычные mock-тесты Ollama API всегда
остаются в CI.

Для проверки не отдельных model-вызовов, а полного пользовательского сценария
используется самостоятельный `labs/ai-scenario-lab`:

```powershell
cd labs/ai-scenario-lab
python run.py prepare
python run.py run smoke
python run.py run full --repeat 3 --min-pass-rate 0.9
python run.py campaign full --repeat 3 --persona-set all --max-journey-cases 40 --seed 7
```

После изменений продолжения copy-операции можно проверить только две связанные
многошаговые цепочки, не запуская весь набор:

```powershell
python run.py campaign regression --skip-scenarios `
  --journey ru-correct-and-approve `
  --journey en-deny-and-follow-up `
  --persona-set all --max-journey-cases 14 --repeat 2 --seed 23
```

Это отдельный 28-case прогон с новым отчётом: семь типов поведения на каждом
языке и два повтора. Он проверяет ответ о destination, обязательный R1 approval,
grant/deny и отсутствие записи до подтверждения. Полный foundation-профиль эта
команда не заменяет.

Для автономной проверки фундамента без ожидания со стороны AI-агента:

```powershell
python run.py foundation prepare
# Запуск выполняется отдельно, когда принято решение о длительном прогоне:
python run.py foundation start
python run.py foundation status
```

Подготовка не обращается к модели. Фоновый запуск сначала проверяет обычные
тесты проекта и лаборатории, затем точный tag/digest Ollama, после чего запускает
матрицу contract/journey cases с таймаутами. Все промежуточные результаты доступны
через `labs/ai-scenario-lab/reports/latest.json`; предыдущие папки сохраняются.
Код во время кампании менять не следует: fingerprint drift делает результат
неполным и требует нового `prepare`. Общий default budget — 4 часа, без обещания
успеть за час. `not_run`, crashes и skips не являются успешными live-проверками.
Полный профиль, критерии, известные пробелы и формат файлов описаны в README
лаборатории и ADR-020. Успех профиля не заменяет Linux-smoke и не выпускает 0.1.

Лаборатория создаёт собственные временные диски, workspace, индекс и Task Ledger.
Она подключает настоящий `qwen3.5:2b`, но search/copy выполняются только внутри
виртуального ПК. Решения `grant`, `deny` и `timeout` задаются сценарием, поэтому
R1-операции проверяются как при подтверждении, так и при отказе. Подробный отчёт
сохраняется в игнорируемом каталоге `labs/ai-scenario-lab/reports`. Safety-наборы
denial/timeout/prompt-injection всегда требуют 100% независимо от общего
`--min-pass-rate`. Lab-only fault injection воспроизводит malformed JSON, timeout
модели и отказ executor без тестовых веток в production runtime. Отчёт также
содержит latency, Ollama token counters, containment canaries и матрицу покрытия
capability по success/denial/timeout/fault.

Команда `campaign` объединяет contract suite с адаптивными User Journeys и не
останавливается после отдельного провала. Persona-матрица всегда ограничивается
`--max-journey-cases`; seed делает языковые мутации воспроизводимыми. Отчёт
разделяет язык, поведение, режим, глубину памяти, capability, решение, тип отказа
и модальность, а неподтверждённые причины оставляет в отдельном `UNKNOWN`.

Тесты значительно снижают риск регрессий, но не доказывают абсолютную безопасность.
Перед привилегированными модулями дополнительно потребуются threat model, sandbox-
профили и отдельные security tests.
