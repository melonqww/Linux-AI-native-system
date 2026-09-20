# Security Center Quarantine Panel API v9

**Статус:** реализованный контракт `security.center` `0.9.0`.

Панель использует authenticated Unix IPC:

- `POST /v1/security/quarantine/prepare` — повторно проверить active finding и
  получить server-owned preview без изменения файла;
- `POST /v1/security/quarantine/commit` — после явного подтверждения переместить
  тот же digest в закрытое хранилище;
- `POST /v1/security/quarantine/cancel` — инвалидировать неподтверждённый receipt;
- `POST /v1/security/quarantine/restore` — после отдельного подтверждения вернуть
  объект, только если исходный путь свободен.

Snapshot Security Center содержит до 10 активных findings и до 10 объектов в
состоянии `quarantined`. Панель получает только resource-relative path, размер,
severity, state и непрозрачные идентификаторы. Абсолютный путь и содержимое файла
не передаются.

Commit и restore разрешены только через secure transport и Permission Gateway с
`approval_granted=true`. Закрытие подтверждения не изменяет файл. Оставшиеся
после аварийного завершения receipts инвалидируются при следующем старте worker.

Решение: [ADR-038](../decisions/ADR-038-security-quarantine-panel-approval.md).
