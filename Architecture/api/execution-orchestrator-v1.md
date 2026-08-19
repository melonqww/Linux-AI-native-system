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
- `awaiting_approval` — R0-префикс завершён, следующий R1-шаг не запускался;
- `failed` — capability завершилась контролируемой ошибкой.

Успешный search-шаг возвращает результаты и `collection_id`. Этот snapshot
становится `active_collection_id` доверенного контекста для следующего запроса.
Фильтр `languages` пока является advisory: поиск выполняется, а ответ содержит
warning `language_filter_not_yet_applied`.

400 используется для неизвестного, просроченного или уже использованного plan ID,
а также если повторная детерминированная проверка плана не прошла. 503 означает,
что оркестратор отключён вместе с Intent Compiler.

Все ошибки runtime используют общий envelope `error.code`, `error.request_id` и
`error.retryable`. Ожидаемые ошибки могут содержать безопасный `detail`;
неожиданные исключения возвращают только `internal_error`, без текста exception и
traceback. Capability-сбои внутри выполнения превращаются в redacted
`failed/error_code`, поэтому один повреждённый модуль не обрывает API-процесс.
