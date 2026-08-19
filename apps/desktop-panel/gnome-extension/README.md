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

Панель подключается к production runtime через пользовательский Unix socket
`$XDG_RUNTIME_DIR/ai-native-linux/runtime.sock`. `RuntimeClient` создаёт одно
соединение на один bounded JSON request; сервер проверяет UID/GID/PID клиента
через Linux `SO_PEERCRED`.

Центральная вкладка использует полный путь `intent/compile → plan/execute →
approval/respond`, а не прямой поиск. Левая вкладка читает health, capabilities,
состояние индекса и Task Ledger. Если runtime не запущен, ошибка остаётся внутри
панели и GNOME Shell продолжает работать.

Loopback HTTP в расширении не используется. Изменяющие операции выполняются
только после проверки Permission Gateway и явного подтверждения R1 preview.

При проблемах запуска см. [TROUBLESHOOTING.md](TROUBLESHOOTING.md): там
зафиксированы особенности GNOME 46, ошибки совместимости и порядок диагностики.
