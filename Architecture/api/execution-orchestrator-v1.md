# Execution Orchestrator API v1

## Выполнение серверного плана

`POST http://127.0.0.1:8765/v1/plan/execute`

```json
{"plan_id": "uuid-from-intent-compile"}
```

Разрешено только поле `plan_id`. Полный план, шаги, risk и arguments из клиента
не принимаются. Идентификатор действителен 15 минут и может быть получен из
хранилища только один раз.

## Состояния

- `completed` — все поддерживаемые R0-шаги завершены;
- `awaiting_approval` — R0-префикс завершён, создан безопасный preview R1;
- `failed` — capability завершилась контролируемой ошибкой.
- `cancelled` — пользователь явно отклонил R1; файловая запись не выполнялась.

Успешный search-шаг возвращает результаты и `collection_id`. Этот snapshot
становится `active_collection_id` доверенного контекста для следующего запроса.
Фильтр `languages` пока является advisory: поиск выполняется, а ответ содержит
warning `language_filter_not_yet_applied`.

R1 search output различает `result_count` (сколько результатов приложено к
ответу) и `total_matches` (сколько совпадений известно Query Service). Поле
`total_is_exact=false` означает bounded lower bound; дополнительно возвращаются
warnings `more_results_available` и `total_matches_is_lower_bound`. Это не даёт
backend сообщить «найдено 50», если на самом деле показана только первая страница.

## Ответ на R1 preview

Результат `awaiting_approval` содержит `approval_request`: назначение, имена,
количество, общий размер, TTL и непривилегированный ID. Исходные пути и внутренний
approval token в preview не входят.

`POST http://127.0.0.1:8765/v1/approval/respond`

```json
{"approval_request_id": "uuid-from-preview", "confirmed": true}
```

Разрешены ровно два поля. Approval одноразовый и действует 5 минут. `false`
удаляет pending-план. `true` создаёт grant внутри runtime, заново проверяет
identity/размер/mtime/SHA-256 каждого источника, свободное место и назначение,
после чего копирует через временные файлы и atomic replace. При ошибке созданная
часть удаляется, но уже существовавшие пользовательские файлы не затрагиваются.

400 используется для неизвестного, просроченного или уже использованного plan ID,
а также если повторная детерминированная проверка плана не прошла. 503 означает,
что оркестратор отключён вместе с Intent Compiler.

Все ошибки runtime используют общий envelope `error.code`, `error.request_id` и
`error.retryable`. Ожидаемые ошибки могут содержать безопасный `detail`;
неожиданные исключения возвращают только `internal_error`, без текста exception и
traceback. Capability-сбои внутри выполнения превращаются в redacted
`failed/error_code`, поэтому один повреждённый модуль не обрывает API-процесс.
Типизированные storage-ошибки нормализуются, например, в
`integrity_check_failed`, `destination_conflict`, `insufficient_space` и
`permission_denied`. Если best-effort cleanup сам не завершился, возвращается
отдельный критичный код `rollback_incomplete`, а исходная ошибка не раскрывается.
