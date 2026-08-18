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

## Запуск нативной панели в Ubuntu

В Ubuntu с GNOME из корня репозитория выполните:

```bash
bash apps/desktop-panel/gnome-extension/install.sh
```

После установки расширение можно перезапустить командами `gnome-extensions disable ai-native-linux@melonqww` и `gnome-extensions enable ai-native-linux@melonqww`. Пока это UI-каркас: вкладки, сворачивание и локальное добавление сообщения работают без подключения модели.

## Документация

- [Концепция v0.1](Architecture/AI-native-Linux-v0.1-концепция.md)
- [Структура проекта и пути](Architecture/02-Структура-проекта-и-пути.md)
- [Контракты намерений и инструментов](Architecture/api/intent-and-tool-contracts.md)
- [Решение о портфолио-MVP](Architecture/decisions/ADR-001-portfolio-mvp-scope.md)
- [Подготовка Ubuntu VM](docs/developer/Ubuntu-VM-setup.md)
