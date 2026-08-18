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

Расширение устанавливается в `~/.local/share/gnome-shell/extensions/ai-native-linux@melonqww`.

На этом шаге это работающий UI-каркас: вкладки переключаются, панель сворачивается стрелкой, поле ввода добавляет сообщение локально. Подключение к agent-runtime добавим следующим отдельным слоем.
