# systemd user service

Production-запуск ядра панели в пользовательской сессии GNOME. Служба создаёт
защищённый Unix socket, запускает Registry и first-party workers, включая
`system.monitor`, и автоматически перезапускается после сбоя.

Из корня репозитория:

```bash
./deployments/systemd/install-user-service.sh
```

Установщик создаёт отдельное Python-окружение в каталоге данных и сам
устанавливает runtime-зависимости. Нужны Python 3.12+ и поддержка `venv`. Если
Ubuntu сообщает, что `venv` отсутствует:

```bash
sudo apt install python3-venv
```

Успешная установка завершается строкой `RESULT: PANEL CORE CONNECTED`. Проверка:

```bash
systemctl --user status ai-native-linux-runtime.service
journalctl --user -u ai-native-linux-runtime.service -n 100 --no-pager
python3 deployments/systemd/runtime_probe.py \
  --socket "$XDG_RUNTIME_DIR/ai-native-linux/runtime.sock"
```

Модель и Ollama URL можно изменить в
`~/.config/ai-native-linux/runtime.env`, затем выполнить:

```bash
systemctl --user restart ai-native-linux-runtime.service
```

First-party модуль `model.ollama` проверяет настроенную модель и после согласия
загружает отсутствующие веса в фоне. Системный пакет Ollama он не устанавливает
и не использует `sudo`; Ollama может быть установлена через защищённое окно
панели модулем `provider.ollama` либо заранее пользователем или образом
системы. `provider.ollama` запускает только собственную user-local копию.
Внешний системный бинарник используется лишь при доступном loopback API, поэтому
runtime не создаёт второй daemon и отдельное хранилище моделей.

Необязательная модель Semantic Selector задаётся
`AI_NATIVE_SEMANTIC_MODEL` (по умолчанию `qwen3-embedding:0.6b`) в том же
`runtime.env`. Она появляется в общем lifecycle-каталоге и загружается только
после решения пользователя. Пока модель отсутствует или недоступна, маршрутизация
остаётся рабочей через bounded lexical fallback.

Установщик атомарно обновляет только прежнее стандартное значение
`qwen3:1.7b` в `runtime.env`. Любая явно выбранная пользователем модель
сохраняется без изменений.

Удаление службы без удаления баз и настроек:

```bash
./deployments/systemd/uninstall-user-service.sh
```

Служба хранит SQLite и audit-файл в
`${XDG_DATA_HOME:-~/.local/share}/ai-native-linux`, а не внутри Git-репозитория.
