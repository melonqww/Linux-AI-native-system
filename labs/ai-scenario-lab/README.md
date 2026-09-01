# AI Scenario Lab

`AI Scenario Lab` — отдельный interface-free мини-проект для повторяемой
проверки всей AI-цепочки системы. Он отправляет запросы той же локальной Qwen,
которая используется runtime, пропускает результат через настоящие production
компоненты и разрешает системные эффекты только внутри одноразового виртуального
ПК.

Лаборатория нужна не для подмены unit-тестов и не для отдельной реализации
ассистента. Её задача — ответить на три разных вопроса:

1. правильно ли модель поняла chat, action или mixed-запрос;
2. правильно ли ядро проверило, разрешило и выполнило полученный план;
3. остались ли реальные изменения строго внутри тестовой файловой системы.

Архитектурное решение зафиксировано в
[`ADR-017`](../../Architecture/decisions/ADR-017-isolated-ai-scenario-lab.md).

## Что запускается на самом деле

Стенд не копирует prompts и routing rules. Он импортирует production-код:

- `WorkspaceRuntime` и `WorkspaceStore`;
- `OllamaModelProvider` с контекстом 8192 токена;
- `IntentCompiler`;
- `CapabilityCandidateRouter` и module-owned intent descriptors;
- `VolumeRegistry`, `IndexScheduler` и `QueryService`;
- Permission Gateway через production `ExecutionOrchestrator`;
- `MaterializeService` и настоящий approval flow для копирования;
- `TaskContextStore` и `TaskLedger`.

Поверх них лаборатория добавляет только наблюдение и изоляцию: записывает границы
вызовов модели, планы и результаты исполнителя, создаёт виртуальные диски и
проверяет структурные ожидания сценария. GNOME, systemd, Snap, браузер и пути
пользователя в этом процессе не используются.

```text
scenario JSON
    ↓
одноразовый VirtualComputer + отдельные SQLite базы
    ↓
WorkspaceRuntime → Qwen/Ollama → IntentCompiler
    ↓                         ↘ chat response
Capability router → Permission Gateway → Orchestrator
    ↓
index/search/copy только внутри VirtualComputer
    ↓
структурные checks + JSON/Markdown report
```

## Граница виртуального ПК

Каждый сценарий получает отдельный каталог:

```text
labs/ai-scenario-lab/.runtime/<run-id>/<scenario-id>/
├── virtual-pc/
│   ├── .ai-native-scenario-lab
│   └── volumes/
│       ├── system/
│       ├── archive/
│       └── locked/
├── workspace.sqlite3
├── task-context.sqlite3
├── task-ledger.sqlite3
├── storage.sqlite3
└── index.sqlite3
```

Физические каталоги отображаются в виртуальные Linux-пути:

| Диск | Виртуальный путь | Доступ | Назначение |
|---|---|---|---|
| `system` | `/` | `content` | домашний каталог, Desktop, Documents и Downloads |
| `archive` | `/mnt/archive` | `metadata` | подключённый диск без чтения содержимого |
| `locked` | `/mnt/locked` | `none` | диск, который система не должна индексировать |

Fixture обязан объявить ровно один системный диск и уникальные mount points.
Абсолютные пути, `..`, неизвестные диски и выход за корень volume отклоняются.
Более конкретный mount point, например `/mnt/archive`, разрешается раньше `/`,
поэтому он не может случайно попасть в системный диск.

Перед повторным созданием ПК проверяется служебный marker. Каталог без marker,
сам корень лаборатории или слишком широкий путь никогда не удаляются. Завершённый
прогон остаётся на диске для разбора отчёта; следующий прогон безопасно создаёт
свою отдельную среду. `.runtime` и `reports` исключены из Git.

## Тестовые данные

Базовый fixture находится в `fixtures/base-desktop.json`. Он создаёт:

- корректный PDF по математике;
- повреждённый PDF;
- обычные текстовые документы;
- файл больше 1 МиБ;
- PDF на диске с metadata-only доступом;
- файл на полностью закрытом диске;
- стандартные роли Desktop, Documents, Downloads и Pictures.

Файлы могут иметь типы `text`, `pdf`, `broken_pdf` и `large_text`. Это
позволяет проверять не только счастливый путь, но и пропуски, ограничения доступа
и большие документы без обращения к настоящим данным.

## Жизненный цикл одного сценария

1. JSON загружается строгим parser: лишние поля и неизвестные значения запрещены.
2. Создаётся новый виртуальный ПК и отдельный набор баз данных.
3. Виртуальные диски регистрируются production `VolumeRegistry` с указанным
   уровнем доступа, затем production-индексатор выполняет первичное сканирование.
4. Проверяется доступность точного model tag в Ollama.
5. Каждый пользовательский turn передаётся в `WorkspaceRuntime` через внутренний
   transport context.
6. Runner ждёт терминального состояния: `completed`, `failed`, `cancelled` или
   `awaiting_approval`.
7. Для изменяющей операции сценарий явно выбирает `grant`, `deny` или `timeout`.
   При `none` runner не имитирует согласие пользователя.
8. Проверяются сообщения, capability, количество найденных/скопированных файлов,
   approval state и физическое наличие путей внутри виртуального ПК.
9. Model traces, audit events, execution records и результаты checks попадают в
   отчёт; базы и файлы остаются доступными для ручного расследования.

Один turn может выполняться до 180 секунд. Ollama provider использует timeout 90
секунд, `keep_alive=10m` и максимум 768 output tokens. У runtime один worker и
очередь до четырёх запросов, чтобы результат оставался воспроизводимым.

## Сценарии первой версии

| ID | Что проверяет |
|---|---|
| `chat-memory` | обычный разговор и использование предыдущего сообщения |
| `search-pdf` | поиск PDF через индекс и query capability |
| `mixed-bread-search` | единый запрос с обычным ответом и файловым действием |
| `copy-approved` | план копирования, подтверждение и реальный файл на Desktop |
| `copy-denied` | отказ без файлового эффекта |
| `copy-timeout` | отсутствие подтверждения без скрытого выполнения |
| `broken-and-large` | устойчивость к повреждённым и большим файлам |

Smoke suite выбирает основные короткие сценарии; full suite запускает весь
набор. `--repeat` (от 1 до 10) помогает увидеть нестабильность генеративной
модели, а `--scenario` изолирует один сбой.

## Команды

Из каталога `labs/ai-scenario-lab`:

```powershell
python run.py prepare
python run.py prepare --pull
python run.py run smoke
python run.py run full --repeat 3
python run.py run full --scenario copy-denied
python run.py report
python run.py report --path
```

Модель по умолчанию — точный tag `qwen3.5:2b`, endpoint —
`http://127.0.0.1:11434`. Для явного эксперимента их можно переопределить:

```powershell
python run.py run smoke --model qwen3.5:2b --base-url http://127.0.0.1:11434
```

Обычный `run` никогда сам не скачивает модель. Сетевая загрузка разрешена только
явной командой `prepare --pull`; если Ollama или точный tag недоступны, запуск
завершается с понятным `NOT READY` и кодом 2.

Unit/integration тесты лаборатории не требуют живой модели:

```powershell
python -m pytest tests -q
```

Они используют детерминированный provider, но всё остальное — поиск,
оркестратор, разрешения, копирование и базы — остаётся production-кодом. Живой
smoke-прогон отдельно проверяет реальное поведение Qwen.

## Формат сценария и проверки

Сценарий — строгий JSON с `id`, `title`, `locale`, `tags`, `fixture` и
массивом `turns`. У turn есть пользовательский текст, approval decision и
`expect`.

Проверки намеренно не требуют дословного ответа модели. Поддерживаются:

- финальный `stage`;
- точный список вызванных `capabilities`;
- `found` и `copied`;
- `approval_required`;
- виды сообщений `message_kinds`;
- `assistant_contains_any` и `assistant_excludes`;
- `paths_exist` и `paths_absent` внутри виртуального ПК;
- `no_operations` для чистого разговора.

Так лаборатория допускает естественную вариативность языка, но не допускает
неверное действие, обход подтверждения или выдуманный результат.

## Отчёты и диагностика

Каждый live-run создаёт:

```text
reports/<UTC-timestamp>/summary.md
reports/<UTC-timestamp>/summary.json
reports/latest.txt
```

Markdown даёт короткий результат для человека. JSON хранит turn-by-turn checks,
сообщения, model boundary events, планы/результаты исполнителя, approval decision,
audit events, ошибку слоя и путь к виртуальному ПК. Благодаря этому можно
различить:

- модель не классифицировала или сформировала невалидный intent;
- compiler отклонил аргументы;
- Permission Gateway потребовал/отклонил подтверждение;
- capability выполнилась, но вернула неправильный результат;
- ответ правильный, но физический файловый эффект отсутствует;
- runtime завершил задачу ошибкой до обращения к модели.

## Что лаборатория доказывает — и чего не доказывает

На Windows она проверяет разговор, краткосрочную память, chat/action/mixed
маршрутизацию, выбор инструмента, валидацию intent, индекс, поиск, подтверждение,
отказ, timeout, копирование, Task Ledger и containment ошибок.

Она не эмулирует GNOME Shell, systemd, Unix peer credentials, inotify, Polkit,
настоящие mount points и Linux ACL. Эти механизмы остаются в Linux
integration/smoke suite. Успешный отчёт лаборатории означает, что AI и модульная
backend-цепочка согласованы; он не заменяет финальную проверку панели на Ubuntu.
