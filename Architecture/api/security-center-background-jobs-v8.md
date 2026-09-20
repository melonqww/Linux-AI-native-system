# Security Center Background Jobs API v8

**Статус:** реализованный контракт `security.center` `0.8.0`.

Панель запускает проверки через authenticated Unix IPC:

- `POST /v1/security/jobs/start` — создать одну bounded scan job;
- `POST /v1/security/jobs/status` — получить реальные счётчики прогресса;
- `POST /v1/security/jobs/cancel` — запросить безопасную отмену;
- `POST /v1/security/jobs/history` — получить до 20 последних запусков;
- `POST /v1/security/jobs/settings` — прочитать или изменить автоматический режим.

Одновременно выполняется не более одной проверки. Job хранит только target,
режим, timestamps, state, verdict и агрегированные счётчики. Абсолютные пути и
содержимое файлов в истории не записываются.

Состояния: `queued`, `running`, `cancelling`, `completed`, `partial`,
`cancelled`, `failed`. Прогресс содержит `scanned_files`, `scanned_bytes`,
`threat_files`, `unknown_files`, `skipped_files` и `elapsed_seconds`.

Автоматические проверки по умолчанию выключены. После включения Security Center
планирует bounded quick scan раз в 24 часа. Ручные проверки работают независимо
от переключателя. Настройка и завершённая история сохраняются локально в
приватной SQLite-базе runtime, исключённой из scan roots.

Решение: [ADR-037](../decisions/ADR-037-security-background-scan-jobs.md).
