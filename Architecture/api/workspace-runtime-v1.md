# Workspace Runtime API v1

Backend-контракт центральной рабочей области. Все endpoints доступны только через
аутентифицированный Unix socket runtime; loopback HTTP возвращает
`secure_transport_required`.

## Отправка

`POST /v1/workspace/submit`

```json
{"text":"Найди PDF по математике"}
```

Ответ `202` сразу возвращает `WorkspaceRun`. Модель, планирование и capability
выполняются в ограниченной фоновой очереди; IPC-поток панели не блокируется.

## Состояние

- `POST /v1/workspace/run` — `{"run_id":"UUID"}`;
- `POST /v1/workspace/runs` — `{"limit":20,"active_only":true}`;
- `POST /v1/workspace/messages` — `{"limit":200}`.

`WorkspaceRun` содержит:

```json
{
  "run_id": "UUID",
  "stage": "executing",
  "stage_label": "Выполняю",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "started_at": "ISO-8601",
  "finished_at": null,
  "elapsed_ms": 3500,
  "stage_elapsed_ms": 1200,
  "user_message_id": "UUID",
  "assistant_message_id": null,
  "task_id": "UUID или null",
  "approval_request_id": "UUID или null"
}
```

Frontend рисует только публичные стадии. `elapsed_ms` можно локально увеличивать
от последнего ответа, периодически синхронизируя его с runtime. ETA в v1 нет.

## Подтверждение

Если stage равен `awaiting_approval`, run содержит `approval_request_id`.

`POST /v1/workspace/approval/respond`

```json
{"approval_request_id":"UUID","confirmed":true}
```

После ответа frontend снова читает run. Подтверждение относится только к одному
подготовленному R1-действию и проходит Permission Gateway.

## Вызовы модели

- обычное общение: один вызов Qwen;
- системная команда: один вызов для semantic route и один после выполнения;
- сообщение «План готов. Начинаю выполнение» создаёт ядро без лишнего model call;
- итог Qwen принимается только если полностью совпадает с одним из сообщений,
  построенных backend из подтверждённых фактов `OrchestrationResult`;
- при недоступности или неверном ответе модели используется системный итог.

Сообщения удаляются через 24 часа. Связанный `task_id` остаётся в Task Ledger
семь суток. После рестарта незавершённый workspace run закрывается безопасным
состоянием `failed`, поэтому frontend не показывает вечное выполнение.
