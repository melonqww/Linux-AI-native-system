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

- обычное общение: Qwen-классификация, затем отдельный естественный chat-ответ;
- системная команда: классификация и semantic route с tools выбранных
  capability; при пустом lexical candidate set используются только операции,
  опубликованные включёнными модулями;
- смешанный запрос использует текстовую часть того же semantic route, а точный
  результат система добавляет отдельным сообщением без второго model call;
- сообщение «План готов. Начинаю выполнение» создаёт ядро без лишнего model call;
- Qwen не формулирует количество, пути или полноту поиска: task result строит
  backend из подтверждённых фактов `OrchestrationResult`;
- при недоступности или неверном ответе модели используется системный итог.

Candidate set является рекомендацией, а не решением об исполнении. Пустой набор
не доказывает conversation; найденный кандидат не может переопределить
conversation-классификацию. Если mixed/action split теряет capability исходного
сообщения, Workspace передаёт semantic router неизменённый полный текст и не
публикует ответ по ошибочному conversation-фрагменту.

Перед model route Workspace читает `model.local.status`. При
`consent_required`, `deferred` или `declined` Qwen не вызывается и frontend
может получить каталог через model lifecycle API. При `starting` или
`downloading` run завершается безопасным notice с текущим прогрессом, а фоновая
загрузка продолжается в `model.ollama`. Следующий запрос после состояния
`ready` обрабатывается штатно.

Каждое сообщение содержит `source`: `user`, `qwen`, `system` или `tool`, чтобы
frontend различал естественный ответ, ход выполнения и проверенный результат.

Сообщения удаляются через 24 часа. Связанный `task_id` остаётся в Task Ledger
семь суток. После рестарта незавершённый workspace run закрывается безопасным
состоянием `failed`, поэтому frontend не показывает вечное выполнение.
# Addendum: input metadata and clarification (ADR-021)

`POST /v1/workspace/submit` still accepts `{"text":"..."}`. It additionally
accepts `attachments: [{"kind":"image","name":"photo.png"}]` (maximum 8,
kind image/document/audio/video, name 1..200 printable characters). No paths,
bytes or access grants are accepted. The current text channel cannot process
these attachments; the new message kind `input_unavailable` represents that
fact. No tools run for that entire turn, including mixed requests. The user
can send a separate text-only task; no visual facts are inferred from metadata.

`clarification` remains a question, not approval. A destination reply can resume
the immediately preceding saved request for the same authenticated principal,
within 10 minutes and with unchanged active collection. It creates a new plan;
R1 still requires its ordinary approval. Topic changes and cancellations do not
execute pending work. Legacy text-only clients need no payload change.
