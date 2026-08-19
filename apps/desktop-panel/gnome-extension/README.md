# GNOME Shell-каркас AI-native Linux

Это первый нативный слой панели. Он повторяет компоновку HTML-прототипа, но рисуется самим GNOME Shell: без отдельного окна WebKit, без белого фона и без зависимости от XWayland.

## Установка в Ubuntu/GNOME

Из корня репозитория:

```bash
bash apps/desktop-panel/gnome-extension/install.sh
```

Для ручного включения/перезапуска:

```bash
gnome-extensions enable ai-native-linux@melonqww
gnome-extensions disable ai-native-linux@melonqww
gnome-extensions enable ai-native-linux@melonqww
```

Перед включением можно проверить файлы без загрузки GNOME Shell:

```bash
python3 apps/desktop-panel/gnome-extension/validate_extension.py --installed
```

Расширение устанавливается в `~/.local/share/gnome-shell/extensions/ai-native-linux@melonqww`.

Панель отправляет поисковые запросы в локальный runtime по
`http://127.0.0.1:8765/v1/search` и отображает найденные пути. Если runtime не
запущен, ошибка остаётся внутри панели и GNOME Shell продолжает работать.

Изменяющие систему операции через этот read-only endpoint не выполняются:
копирование проходит отдельный этап плана и явного подтверждения.

При проблемах запуска см. [TROUBLESHOOTING.md](TROUBLESHOOTING.md): там
зафиксированы особенности GNOME 46, ошибки совместимости и порядок диагностики.
