# AI-native Linux System

Системный AI-помощник для Ubuntu Desktop: он понимает запрос пользователя, получает минимально необходимый контекст и выполняет только разрешённые действия через проверяемые инструменты.

## Статус

Проект находится на этапе v0.1. Первая целевая среда — **Ubuntu Desktop 24.04 LTS с GNOME**, запущенная в отдельной виртуальной машине.

Первый вертикальный сценарий — `system_status`: показать свободное место, CPU, RAM, батарею и процессы без каких-либо изменений в системе.

Реализация MVP начинается с **Python 3.12+** и стандартной библиотеки: это позволяет разработать policy layer и тесты до готовности Ubuntu VM. Системный адаптер будет запускаться только в Ubuntu.

Нативный UI-каркас для GNOME уже находится в `apps/desktop-panel/gnome-extension/`. Он повторяет текущий HTML-прототип, но работает как GNOME Shell Extension и не создаёт отдельное окно WebKit.

GNOME-панель подключена к ядру через authenticated Unix IPC: центральная вкладка
использует Intent Compiler, Execution Orchestrator и R1 approval, левая показывает
health, индекс и Task Ledger. CPU/RAM/батарея и процессы намеренно остаются без
фиктивных значений: first-party `system.monitor` уже отдельным worker-процессом читает
ограниченные Linux-метрики и отдаёт их панели через runtime API.

## Базовый принцип

Модель не имеет прямого доступа к shell, `sudo` или D-Bus. Она создаёт структурированное намерение, а Permission Gateway проверяет риск, права и параметры инструмента.

```text
intent → policy → tool → audit event → result
```

## Локальная демонстрация без Ubuntu

Уже сейчас можно проверить policy layer и audit log на Windows:

```powershell
$env:PYTHONPATH = "services\\agent-runtime\\src"
python -m ai_native_linux.cli --demo
python -m unittest discover -s services/agent-runtime/tests -v
```

Команда `--demo` не читает и не изменяет систему: она проверяет заранее заданное R0-намерение и записывает обезличенное audit event в игнорируемую Git папку `data/`.

## Локальный retrieval

Первый opt-in индексатор находится в `services/indexer`. Он индексирует явно
выбранную папку в локальный SQLite FTS5, исключает чувствительные и
неподдерживаемые файлы и возвращает результаты с путями и строками источника.
Команды запуска и правила исключений описаны в
[`services/indexer/README.md`](services/indexer/README.md).

## Каталог хранилищ

`services/storage-catalog` реализует реестр дисков и разрешений, каталог
файловых метаданных и виртуальные smart/snapshot-коллекции. Команды запуска и
текущие границы описаны в
[`services/storage-catalog/README.md`](services/storage-catalog/README.md).

## Capability Registry

`services/capability-registry` проверяет manifests модулей, вычисляет состояния
и зависимости и публикует доступные capabilities. Формальная manifest schema
находится в `packages/module-sdk`. Зарегистрированы `storage.catalog`,
`documents.index`, `documents.pdf`, `desktop.applications` и
`browser.navigation` и `system.monitor`; команды описаны в
[`services/capability-registry/README.md`](services/capability-registry/README.md).

## Intent Compiler v1

`services/intent-compiler` переводит свободные русские и английские запросы в
строго проверяемое намерение и preview-план. Языковой model provider здесь
заменяемый, а capability, зависимости, риск и approval вычисляет
детерминированное ядро. Локальный adapter использует `qwen3.5:2b` через Ollama,
`think=false` и semantic tool calls, которые сами ничего не исполняют. В
production-коде нет таблицы заранее известных фраз.
Подробности: [`services/intent-compiler/README.md`](services/intent-compiler/README.md)
и [ADR-004](Architecture/decisions/ADR-004-model-neutral-intent-compiler.md).

## Execution Orchestrator v1

`services/execution-orchestrator` принимает только серверный `plan_id`, повторно
проверяет план и исполняет R0-поиск. Результаты сразу фиксируются snapshot-коллекцией
и становятся доверенным контекстом следующего запроса. R1-копирование использует
отдельный preview и одноразовое подтверждение, проверяет SHA-256 и выполняет
rollback при сбое; подмена шагов через HTTP и replay запрещены. Подробнее:
[контракт API](Architecture/api/execution-orchestrator-v1.md)
и [ADR-005](Architecture/decisions/ADR-005-server-owned-execution-orchestration.md).

## Permission Gateway v1

`services/permission-gateway` назначает risk, phases, secure transports, scopes,
argument contracts, concurrency и deadlines независимо от модели и manifests.
Search/copy выполняются через общий bounded handler registry; availability и
scopes проверяются повторно непосредственно перед каждым шагом. Подробнее:
[контракты](Architecture/api/permission-gateway-v1.md) и
[ADR-007](Architecture/decisions/ADR-007-trusted-permission-gateway.md).

## Task Ledger v1

`services/task-ledger` хранит семидневную пользовательскую историю задач,
агрегированные результаты, кликабельные ссылки на локальные файлы и безопасные
checkpoints. Это компонент ядра, а не плагин и не debug log: traceback, код,
model reasoning и технические причины ошибок в пользовательскую ленту не попадают.
Отмена работающей задачи кооперативна и терминальна; продолжить можно только
системно прерванную задачу с checkpoint. Детали с путями и управление доступны
только через authenticated Unix IPC. Подробнее: [контракт](Architecture/api/task-ledger-v1.md)
и [ADR-008](Architecture/decisions/ADR-008-core-task-ledger.md).

## Шесть базовых компонентов

Текущий фундамент собран в один модульный контур:

1. `services/module-manager` запускает capability-модули отдельными процессами,
   поднимает зависимости, проверяет health, ограничивает ожидание ответа и
   выгружает idle-модули.

2. `services/query-service` объединяет быстрый поиск по метаданным и содержимому.
3. `modules/documents-pdf` извлекает постраничный текст PDF для локального FTS.
4. loopback bridge в `services/agent-runtime` соединяет сервис с GNOME-панелью.
5. Materialize Service в `services/storage-catalog` копирует snapshot только
   после явного одноразового подтверждения и повторной проверки источников.
6. `modules/desktop-applications` и `modules/browser-navigation` дают базовый
   поиск приложений, безопасное планирование URL и веб-поиска.

`services/workspace-service` задаёт единую системную рабочую ленту без списка
чатов: сообщения живут 24 часа, run stages содержат только безопасный публичный
прогресс и точное прошедшее время. Operational task-memory теперь переживает
рестарт отдельно от сообщений, а Task Ledger по-прежнему хранит факты операций
семь суток. Архитектура зафиксирована в
[`ADR-009`](Architecture/decisions/ADR-009-system-workspace-runtime.md).
Гибридные сообщения и полнота поиска разделены между модулями в
[`ADR-014`](Architecture/decisions/ADR-014-modular-workspace-turn-pipeline.md).
Backend API рабочей области:
[`Architecture/api/workspace-runtime-v1.md`](Architecture/api/workspace-runtime-v1.md).
Жизненный цикл локальной модели вынесен в first-party `model.ollama`: ядро
использует model-neutral контракты, а модуль управляет Qwen и дополнительной
LLaMA. Отсутствующие веса загружаются в фоне только после решения пользователя.
Backend-контракт панели: [`model-catalog-v1`](Architecture/api/model-catalog-v1.md).
Архитектурное решение:
[`ADR-011`](Architecture/decisions/ADR-011-model-lifecycle-module.md).
Сам inference runtime устанавливает отдельный `provider.ollama`: без root, в
user-local каталог и только после проверки SHA-256 официального release.
Контракт: [`ollama-provider-v1`](Architecture/api/ollama-provider-v1.md),
решение: [`ADR-012`](Architecture/decisions/ADR-012-user-local-ollama-provider.md).
Согласованный порядок `Ollama → Qwen/LLaMA` отдаёт единый
[`inference-lifecycle-v1`](Architecture/api/inference-lifecycle-v1.md); решение
зафиксировано в [`ADR-013`](Architecture/decisions/ADR-013-inference-lifecycle-coordinator.md).

`modules/system-monitor` реализует read-only диспетчер: CPU, load average,
uptime, RAM/swap, thermal zones, батарею, диски и процессы. Контракт:
[`Architecture/api/system-monitor-v1.md`](Architecture/api/system-monitor-v1.md).

`modules/system-updates` выполняет bounded read-only симуляцию APT в отдельном
on-demand worker и отдаёт панели только сводку. Установка и refresh остаются в
штатном Ubuntu update UI; контракт описан в
[`Architecture/api/system-updates-v1.md`](Architecture/api/system-updates-v1.md).

Следующий background-слой реализован в `services/index-scheduler`: Linux
`inotify`, mount monitoring, ограниченная coalescing-очередь, load pause и
инкрементальное обновление каталога, текста и PDF. `Module Manager` действительно
запускает его lifecycle в отдельном процессе; состояние доступно через
`GET /v1/index-status`.

Поиск не сканирует диск на каждый запрос. Каталог и индекс обновляются отдельно,
а интерактивный путь читает SQLite/FTS — это принципиально для быстрой панели.

Для ручного foreground-запуска API панели из корня проекта:

```bash
PYTHONPATH="services/agent-runtime/src:services/task-ledger/src:services/workspace-service/src:services/turn-router/src:services/permission-gateway/src:services/execution-orchestrator/src:services/intent-compiler/src:services/capability-registry/src:services/module-manager/src:services/query-service/src:services/storage-catalog/src:services/indexer/src:services/index-scheduler/src:modules/documents-pdf/src" \
python -m ai_native_linux.cli --serve-panel
```

На Linux команда автоматически создаёт защищённый Unix socket
`$XDG_RUNTIME_DIR/ai-native-linux/runtime.sock`, проверяет kernel-provided
`PID/UID/GID` клиента и только через этот канал разрешает R1 confirmation.
Loopback HTTP включается явно флагом `--transport http` и остаётся R0-only.

## Автоматические проверки

`python -m pytest -q` проверяет все сервисы, модули, структуру и safety-инварианты.
GitHub Actions повторяет набор на Windows и Ubuntu 24.04, где дополнительно
выполняется настоящий inotify integration test. Подробности находятся в
[`docs/developer/testing.md`](docs/developer/testing.md).

## Запуск нативной панели в Ubuntu

В Ubuntu с GNOME из корня репозитория сначала установите и запустите
ядро как `systemd --user` service, затем обновите GNOME-расширение:

```bash
./deployments/systemd/install-user-service.sh
bash apps/desktop-panel/gnome-extension/install.sh
```

Первая команда должна завершиться `RESULT: PANEL CORE CONNECTED`. Служба
автоматически поднимает Registry, `system.monitor`, индекс и Intent Compiler при
входе в сессию. После установки расширение можно перезапустить командами
`gnome-extensions disable ai-native-linux@melonqww` и
`gnome-extensions enable ai-native-linux@melonqww`. Подробности:
[`deployments/systemd/README.md`](deployments/systemd/README.md).

## Документация

- [Концепция v0.1](Architecture/AI-native-Linux-v0.1-концепция.md)
- [Структура проекта и пути](Architecture/02-Структура-проекта-и-пути.md)
- [Контракты намерений и инструментов](Architecture/api/intent-and-tool-contracts.md)
- [Intent Compiler API v1](Architecture/api/intent-compiler-v1.md)
- [Execution Orchestrator API v1](Architecture/api/execution-orchestrator-v1.md)
- [Runtime Unix IPC v1](Architecture/api/runtime-unix-ipc-v1.md)
- [Permission Gateway v1](Architecture/api/permission-gateway-v1.md)
- [Task Ledger v1](Architecture/api/task-ledger-v1.md)
- [System Monitor API v1](Architecture/api/system-monitor-v1.md)
- [System Updates API v1](Architecture/api/system-updates-v1.md)
- [Контракты Storage Catalog](Architecture/api/storage-catalog-contracts.md)
- [File Search R1](Architecture/api/file-search-r1.md)
- [Контракт статуса индекса](Architecture/api/runtime-index-status.md)
- [JSON Schema manifest модуля](packages/module-sdk/schema/module-manifest.schema.json)
- [Решение о портфолио-MVP](Architecture/decisions/ADR-001-portfolio-mvp-scope.md)
- [Решение о каталоге хранилищ и виртуальных коллекциях](Architecture/decisions/ADR-002-storage-catalog-and-virtual-collections.md)
- [Решение о модульном capability-ядре](Architecture/decisions/ADR-003-modular-capability-core.md)
- [Решение о единой системной рабочей области](Architecture/decisions/ADR-009-system-workspace-runtime.md)
- [Решение о модульном Workspace Turn Pipeline v2](Architecture/decisions/ADR-014-modular-workspace-turn-pipeline.md)
- [Решение о долговечном модуле установки приложений](Architecture/decisions/ADR-015-durable-software-manager.md)
- [Решение о модульном жизненном цикле локальных моделей](Architecture/decisions/ADR-011-model-lifecycle-module.md)
- [Решение об Intent Compiler](Architecture/decisions/ADR-004-model-neutral-intent-compiler.md)
- [Решение об оркестрации серверных планов](Architecture/decisions/ADR-005-server-owned-execution-orchestration.md)
- [Решение об authenticated Unix transport](Architecture/decisions/ADR-006-authenticated-unix-runtime-transport.md)
- [Решение о доверенном Permission Gateway](Architecture/decisions/ADR-007-trusted-permission-gateway.md)
- [Решение о core Task Ledger](Architecture/decisions/ADR-008-core-task-ledger.md)
- [Желаемые будущие возможности системы](Architecture/product/future-system-capabilities.md)
- [Подготовка Ubuntu VM](docs/developer/Ubuntu-VM-setup.md)
