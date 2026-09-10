# Security Center Foundation API v1

## Назначение

Foundation API подтверждает, что first-party модуль `security.center`
зарегистрирован, запускается Module Manager в отдельном процессе и отвечает по
ограниченному контракту. API не сканирует файлы и не заявляет антивирусную
защиту.

## Capability

```text
security.module.status
```

Trusted policy:

- risk: `R0`;
- approval: не требуется;
- phase: `execute`;
- transports: `internal`, `unix_peer`, `loopback_http`;
- required scope: `security.read-status`;
- timeout: 5 секунд;
- arguments: отсутствуют.

Capability не содержит `user_intent`. Языковая модель не получает status
operation как пользовательский tool.

## Worker invocation

```json
{
  "operation": "status",
  "payload": {}
}
```

Неизвестная operation даёт ограниченную ошибку `unknown_operation`, а непустой
или не-object payload — `invalid_payload`.

## Результат

```json
{
  "schema_version": 1,
  "module_id": "security.center",
  "module_version": "0.2.0",
  "state": "ready",
  "lifecycle": "on-demand",
  "capabilities": [
    "security.module.status",
    "security.files.scan"
  ]
}
```

Ответ детерминирован, JSON-safe и не содержит пути, окружение, process
arguments, секреты или traceback.

## Lifecycle

`worker_start()` переводит worker в ready-состояние. Повторный start безопасен.
`worker_health()` и status invocation до запуска или после stop закрываются
ошибкой `security_worker_not_started`. `worker_stop()` идемпотентен.

Импорт `ai_native_security` не запускает lifecycle и не выполняет I/O.

## Не входит в foundation v1

- scan request/result (добавлен отдельным контрактом File Scan API v2);
- findings persistence;
- posture probes;
- quarantine/restore;
- detector или rules API;
- пользовательский HTTP endpoint;
- background monitoring;
- AI.

Новые контракты добавляются только вместе с реализацией и trusted policy.

Архитектурное решение: [`ADR-026`](../decisions/ADR-026-security-center-foundation.md).
