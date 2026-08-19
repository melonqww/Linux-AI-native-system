# Task Ledger API v1

## Назначение

Task Ledger — пользовательская история задач, а не технический лог. Его записи не
содержат exception message, traceback, исходный код, запрос пользователя,
содержимое документов или внутреннюю причину сбоя.

Состояния: `planned`, `running`, `awaiting_approval`, `interrupted`, `completed`,
`completed_with_skips`, `failed`, `cancelled`.

`can_cancel` истинно только у работающей задачи без уже принятого cancel request.
`can_continue` истинно только у системно прерванной задачи с safe checkpoint.
После запроса продолжения флаг выключается до того, как capability атомарно
примет запрос и checkpoint.

## Runtime endpoints

`GET /v1/tasks` возвращает до 50 свежих задач без путей к файлам и checkpoint.
Эта лента пригодна для группировки «сегодня / вчера / ранее».

Следующие команды доступны только через Unix socket с проверенным `SO_PEERCRED`:

- `POST /v1/tasks/detail` — полная карточка и локальные object references;
- `POST /v1/tasks/cancel` — cooperative cancel request работающей задаче;
- `POST /v1/tasks/continue` — запрос продолжения `interrupted` задачи.

Тело каждой команды закрытое:

```json
{"task_id":"canonical-task-uuid"}
```

HTTP fallback отвечает `403 secure_transport_required`. Недоступное действие
возвращает `409 task_action_not_available`, отсутствующая задача —
`404 task_not_found`. В ответах нет технической причины остановки задачи.

## Checkpoint contract

Checkpoint — внутренний JSON-объект до 64 KiB с положительной версией схемы. Он
не входит в пользовательскую проекцию. Запись разрешена только работающей задаче,
заранее отмеченной resumable. Capability обязана интерпретировать собственную
версию и продолжать с последней полностью завершённой границы (файл, PDF-страница,
batch), а не с неопределённого промежуточного байта.

## Retention

По умолчанию terminal records удаляются через семь суток после `finished_at`
вместе с событиями и ссылками. Активные, ожидающие подтверждения и прерванные
задачи retention не затрагивает.
