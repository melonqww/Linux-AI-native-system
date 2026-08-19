# AI-native Linux System

Системный AI-помощник для Ubuntu Desktop: он понимает запрос пользователя, получает минимально необходимый контекст и выполняет только разрешённые действия через проверяемые инструменты.

## Статус

Проект находится на этапе v0.1. Первая целевая среда — **Ubuntu Desktop 24.04 LTS с GNOME**, запущенная в отдельной виртуальной машине.

Первый вертикальный сценарий — `system_status`: показать свободное место, CPU, RAM, батарею и процессы без каких-либо изменений в системе.

Реализация MVP начинается с **Python 3.12+** и стандартной библиотеки: это позволяет разработать policy layer и тесты до готовности Ubuntu VM. Системный адаптер будет запускаться только в Ubuntu.

Нативный UI-каркас для GNOME уже находится в `apps/desktop-panel/gnome-extension/`. Он повторяет текущий HTML-прототип, но работает как GNOME Shell Extension и не создаёт отдельное окно WebKit.

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
`browser.navigation`; команды описаны в
[`services/capability-registry/README.md`](services/capability-registry/README.md).

## Intent Compiler v1

`services/intent-compiler` переводит свободные русские и английские запросы в
строго проверяемое намерение и preview-план. Языковой model provider здесь
заменяемый, а capability, зависимости, риск и approval вычисляет
детерминированное ядро. Локальный adapter использует `qwen3:1.7b` через Ollama,
`think=false` и semantic tool calls, которые сами ничего не исполняют. В
production-коде нет таблицы заранее известных фраз.
Подробности: [`services/intent-compiler/README.md`](services/intent-compiler/README.md)
и [ADR-004](Architecture/decisions/ADR-004-model-neutral-intent-compiler.md).

## Execution Orchestrator v1

`services/execution-orchestrator` принимает только серверный `plan_id`, повторно
проверяет план и исполняет R0-поиск. Результаты сразу фиксируются snapshot-коллекцией
и становятся доверенным контекстом следующего запроса. Перед R1-копированием v1
останавливается с `awaiting_approval`; подмена шагов через HTTP и повторный запуск
одного плана запрещены. Подробнее: [контракт API](Architecture/api/execution-orchestrator-v1.md)
и [ADR-005](Architecture/decisions/ADR-005-server-owned-execution-orchestration.md).

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

Следующий background-слой реализован в `services/index-scheduler`: Linux
`inotify`, mount monitoring, ограниченная coalescing-очередь, load pause и
инкрементальное обновление каталога, текста и PDF. `Module Manager` действительно
запускает его lifecycle в отдельном процессе; состояние доступно через
`GET /v1/index-status`.

Поиск не сканирует диск на каждый запрос. Каталог и индекс обновляются отдельно,
а интерактивный путь читает SQLite/FTS — это принципиально для быстрой панели.

Для запуска локального API панели из корня проекта:

```bash
PYTHONPATH="services/agent-runtime/src:services/execution-orchestrator/src:services/intent-compiler/src:services/capability-registry/src:services/module-manager/src:services/query-service/src:services/storage-catalog/src:services/indexer/src:services/index-scheduler/src:modules/documents-pdf/src" \
python -m ai_native_linux.cli --serve-panel
```

## Автоматические проверки

`python -m pytest -q` проверяет все сервисы, модули, структуру и safety-инварианты.
GitHub Actions повторяет набор на Windows и Ubuntu 24.04, где дополнительно
выполняется настоящий inotify integration test. Подробности находятся в
[`docs/developer/testing.md`](docs/developer/testing.md).

## Запуск нативной панели в Ubuntu

В Ubuntu с GNOME из корня репозитория выполните:

```bash
bash apps/desktop-panel/gnome-extension/install.sh
```

После установки расширение можно перезапустить командами `gnome-extensions disable ai-native-linux@melonqww` и `gnome-extensions enable ai-native-linux@melonqww`. Поисковое поле обращается к read-only runtime на `127.0.0.1:8765`; изменяющие операции этим endpoint недоступны.

## Документация

- [Концепция v0.1](Architecture/AI-native-Linux-v0.1-концепция.md)
- [Структура проекта и пути](Architecture/02-Структура-проекта-и-пути.md)
- [Контракты намерений и инструментов](Architecture/api/intent-and-tool-contracts.md)
- [Intent Compiler API v1](Architecture/api/intent-compiler-v1.md)
- [Execution Orchestrator API v1](Architecture/api/execution-orchestrator-v1.md)
- [Контракты Storage Catalog](Architecture/api/storage-catalog-contracts.md)
- [Контракт статуса индекса](Architecture/api/runtime-index-status.md)
- [JSON Schema manifest модуля](packages/module-sdk/schema/module-manifest.schema.json)
- [Решение о портфолио-MVP](Architecture/decisions/ADR-001-portfolio-mvp-scope.md)
- [Решение о каталоге хранилищ и виртуальных коллекциях](Architecture/decisions/ADR-002-storage-catalog-and-virtual-collections.md)
- [Решение о модульном capability-ядре](Architecture/decisions/ADR-003-modular-capability-core.md)
- [Решение об Intent Compiler](Architecture/decisions/ADR-004-model-neutral-intent-compiler.md)
- [Решение об оркестрации серверных планов](Architecture/decisions/ADR-005-server-owned-execution-orchestration.md)
- [Желаемые будущие возможности системы](Architecture/product/future-system-capabilities.md)
- [Подготовка Ubuntu VM](docs/developer/Ubuntu-VM-setup.md)
