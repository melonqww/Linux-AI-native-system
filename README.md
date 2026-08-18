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

Поиск не сканирует диск на каждый запрос. Каталог и индекс обновляются отдельно,
а интерактивный путь читает SQLite/FTS — это принципиально для быстрой панели.

Для запуска локального API панели из корня проекта:

```bash
PYTHONPATH="services/agent-runtime/src:services/query-service/src:services/storage-catalog/src:services/indexer/src:modules/documents-pdf/src" \
python -m ai_native_linux.cli --serve-panel
```

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
- [Контракты Storage Catalog](Architecture/api/storage-catalog-contracts.md)
- [JSON Schema manifest модуля](packages/module-sdk/schema/module-manifest.schema.json)
- [Решение о портфолио-MVP](Architecture/decisions/ADR-001-portfolio-mvp-scope.md)
- [Решение о каталоге хранилищ и виртуальных коллекциях](Architecture/decisions/ADR-002-storage-catalog-and-virtual-collections.md)
- [Решение о модульном capability-ядре](Architecture/decisions/ADR-003-modular-capability-core.md)
- [Желаемые будущие возможности системы](Architecture/product/future-system-capabilities.md)
- [Подготовка Ubuntu VM](docs/developer/Ubuntu-VM-setup.md)
