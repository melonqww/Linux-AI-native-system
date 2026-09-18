# Security Center User Scan API v7

**Статус:** реализованный контракт `security.center` `0.7.0`.

Панель вызывает `POST /v1/security/scan` только через authenticated Unix IPC.
Допустимы четыре закрытые формы:

```json
{"target":"quick"}
{"target":"full"}
{"target":"file","relative_path":"Downloads/sample.bin"}
{"target":"folder","relative_path":"Downloads","mode":"full"}
```

`quick` проверяет существующие Downloads, Desktop, пользовательский autostart и
временную область. `full` проверяет все roots, предоставленные trusted bootstrap:
домашнюю и отдельную временную область. Каждый root сохраняет лимиты профиля;
достижение лимита даёт `partial + unknown`, а не ложный clean.

Точечные пути всегда относительны к home. Пустые компоненты, `.`, `..`, обратная
косая черта, абсолютный путь, отсутствующая папка и symlink/junction boundary
отклоняются. Runtime проверяет Permission Gateway перед вызовом worker.

Ответ объединяет только счётчики, status и verdict. Абсолютные roots и содержимое
файлов наружу не выходят. Full не означает root-доступ ко всей ОС: это полный
обход всех явно разрешённых Security Center областей.

Панель получает выбранный локальный объект через XDG Desktop Portal, переводит
его в home-relative reference и не показывает поле ручного ввода абсолютного
пути. Nautilus MenuProvider передаёт локальный `file://` URI отдельному клиенту;
тот канонизирует путь, проверяет confinement в home и обращается к тому же Unix
IPC. Quick/full из панели и точечная проверка из Nautilus завершаются системным
уведомлением с итоговыми счётчиками.

Решение: [ADR-036](../decisions/ADR-036-security-user-scan-modes.md).
