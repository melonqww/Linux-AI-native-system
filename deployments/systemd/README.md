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
test -S "$XDG_RUNTIME_DIR/ai-native-linux/runtime.sock" && echo connected
```

Модель и Ollama URL можно изменить в
`~/.config/ai-native-linux/runtime.env`, затем выполнить:

```bash
systemctl --user restart ai-native-linux-runtime.service
```

Удаление службы без удаления баз и настроек:

```bash
./deployments/systemd/uninstall-user-service.sh
```

Служба хранит SQLite и audit-файл в
`${XDG_DATA_HOME:-~/.local/share}/ai-native-linux`, а не внутри Git-репозитория.
