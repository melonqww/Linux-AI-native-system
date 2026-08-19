# Диагностика GNOME-панели

Этот файл хранит реальные проблемы, которые уже встретились при запуске
расширения `ai-native-linux@melonqww` в Ubuntu GNOME 46.

## Быстрое обновление и установка

Команды нужно выполнять по порядку и проверять результат `git pull`. Если он
завершился ошибкой сети, `install.sh` нельзя считать обновившим код: скрипт
просто установит старую копию, которая уже лежит в локальном репозитории.

```bash
cd ~/Projects/Linux-AI-native-system
git pull --ff-only origin main
bash apps/desktop-panel/gnome-extension/install.sh
gnome-extensions info ai-native-linux@melonqww
```

Если появляется `Could not resolve host: github.com`, сначала восстановить
интернет и повторить `git pull`. Не ориентироваться только на строку
`Установлено` из `install.sh`.

## Что означают состояния

- `RESULT: READY` у `validate_extension.py` — это только статическая проверка
  файлов, она не доказывает, что GNOME Shell создал панель.
- `Состояние: ACTIVE` — расширение включено и `enable()` завершился без
  необработанного исключения.
- `Состояние: ERROR` — GNOME Shell не смог загрузить расширение или его
  интерфейс. Причину смотреть в журнале текущего запуска.

```bash
journalctl --user --since "1 minute ago" --no-pager -o cat \
  | grep -i -A25 -B5 "AI-native Linux"
```

## Уже исправленные ошибки

### `No property spacing on StBoxLayout`

В GNOME Shell 46 нельзя передавать `spacing` в конструкторе `St.BoxLayout`.

Неправильно:

```js
new St.BoxLayout({spacing: 6});
```

Отступы задаются в `stylesheet.css` через `spacing: 6px;`. Метод
`set_spacing()` также не поддерживается и использовать его нельзя.

### `confirmationContent.set_spacing is not a function`

Это была попытка исправить предыдущую ошибку несовместимым методом. В текущем
варианте JavaScript-вызовов `spacing` нет вообще; отступы остаются только в CSS.

### `this.get_stylesheet is not a function`

В GNOME Shell 46 таблица стилей берётся из каталога расширения:

```js
this.dir.get_child('stylesheet.css')
```

### `ReferenceError: runtime is not defined`

`ChatView` должен получать клиент явно:

```js
new ChatView(this._runtime)
```

### Ошибка из-за Soup/GI typelib

Визуальный прототип не должен падать из-за отсутствующего `Soup` в системе.
Транспорт к runtime пока заменён безопасным no-op-клиентом; подключать HTTP
к runtime нужно отдельным этапом после стабилизации Shell-интерфейса.

## Диагностический fallback

Если создание основной панели падает, расширение показывает кнопку
`AI-native Linux: ошибка панели` и пишет stack trace с маркером
`panel construction failed`. Это означает, что расширение уже запущено, но
нужно исправить конкретную несовместимость GNOME API из журнала.

