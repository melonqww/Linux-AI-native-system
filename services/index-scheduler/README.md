# Storage Watch and Index Scheduler

Background capability `storage.watch` поддерживает каталог и content index в
актуальном состоянии вне интерактивного пути панели.

## Поведение

- На Linux рекурсивно использует `inotify`; symbolic links и переходы на другой
  filesystem не отслеживаются.
- События одного файла объединяются после короткого debounce.
- Очередь ограничена. Переполнение превращается в один глобальный rescan вместо
  неограниченного расхода RAM.
- По умолчанию за цикл обрабатывается не больше 16 записей, между циклами есть
  ожидание событий. При высокой нормализованной load average очередь ставится на
  паузу.
- Metadata permission обновляет только каталог. Содержимое читается исключительно
  при `content` permission.
- Подключение диска не выдаёт ему разрешение. Новый разрешённый диск получает
  watcher и фоновый rescan.
- Если дерево превышает inotify watch budget, уже установленные watches остаются,
  а непокрытая часть сверяется порционным fallback-rescan не чаще одного раза в
  15 минут.
- PDF передаётся ограниченному `documents.pdf`; обычный текст проходит FilePolicy.
- `.ssh`, `.gnupg`, secrets, keyrings, password-store, ключи, hidden и symlink-
  источники не попадают в FTS.
- Обычный `PermissionError` системного каталога считается `inaccessible`, а не
  падением модуля; watcher продолжает работу с остальной частью диска.

Module worker вызывает `worker_start`, `worker_health` и `worker_stop`, поэтому
background loop действительно живёт в отдельном процессе и корректно завершается.
На платформах без inotify запускается lifecycle и mount monitor, но filesystem-
события ожидают platform adapter.

Статус доступен через `GET /v1/index-status` локального runtime bridge.
