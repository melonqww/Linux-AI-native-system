# Linux shell

Минимальная GTK/WebKit-обёртка открывает текущий HTML-прототип как отдельное
Linux-приложение, без Firefox и других браузеров.

Запуск из корня репозитория:

```bash
python3 apps/desktop-panel/linux-shell/panel.py
```

На X11 окно пытается закрепиться справа снизу. В Wayland окончательное
позиционирование контролирует GNOME; для точной привязки позже добавим
GTK-Layer-Shell или GNOME Shell Extension.
