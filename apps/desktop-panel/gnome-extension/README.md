# GNOME Shell-каркас AI-native Linux

Это первый нативный слой панели. Он повторяет компоновку HTML-прототипа, но рисуется самим GNOME Shell: без отдельного окна WebKit, без белого фона и без зависимости от XWayland.

## Установка в Ubuntu/GNOME

Из корня репозитория:

```bash
bash apps/desktop-panel/gnome-extension/install.sh
```

Установщик панели сначала обновляет и перезапускает `systemd --user` runtime,
затем заменяет файлы и повторно включает расширение. Поэтому новая панель не
остаётся подключённой к старому процессу ядра. Без runtime socket интерфейс
загрузится, но покажет `Ядро — недоступно`.

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

Центральная вкладка использует асинхронный путь `workspace/submit → run/messages
→ workspace/approval/respond`; внутри ядра он проходит Intent Compiler,
Orchestrator и Permission Gateway. Левая вкладка читает health, capabilities,
состояние индекса, Task Ledger и read-only snapshot `system.monitor`: CPU, RAM/swap,
батарею, температуры, диски и процессы. Во время показа вкладки метрики
обновляются каждые три секунды. Если runtime не запущен, ошибка остаётся внутри
панели и GNOME Shell продолжает работать.

Loopback HTTP в расширении не используется. Изменяющие операции выполняются
только после проверки Permission Gateway и явного подтверждения R1 preview.

`panel-presenter.js` — чистый слой представления без GNOME API. Он формирует
пользовательские тексты для результатов, R1, ошибок, состояния ядра и
Task Ledger. Все состояния проверяются Node-тестами, а сам модуль повторно
загружается настоящим GJS в Linux CI.

Для smoke-проверки в настоящей сессии GNOME 46–48:

```bash
./apps/desktop-panel/gnome-extension/smoke-test.sh
```

Скрипт переустанавливает расширение, проверяет состояние `enabled` и новые
ошибки с UUID расширения в journal. `READY FOR VISUAL CHECK` означает, что можно
открыть панель и оценить геометрию, анимацию и переносы текста глазами.

При проблемах запуска см. [TROUBLESHOOTING.md](TROUBLESHOOTING.md): там
зафиксированы особенности GNOME 46, ошибки совместимости и порядок диагностики.
