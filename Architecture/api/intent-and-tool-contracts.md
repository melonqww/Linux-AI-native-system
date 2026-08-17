# Контракты намерений и инструментов — v0.1

## Цель

Этот контракт отделяет недетерминированный ответ модели от системного действия. Модель может предложить только известное намерение из allowlist; gateway сам проверяет параметры и выбирает инструмент.

## Общий жизненный цикл

```text
User request
  → Intent proposal
  → Policy decision
  → Approval (только если требуется)
  → Tool execution
  → Redacted audit event
  → User-facing result
```

Каждый объект содержит `request_id`, который связывает сообщение пользователя, решение политики, действие и журнал.

## Первое намерение: `get_system_status`

Это единственное намерение, которое реализуется первым. Оно всегда относится к уровню риска **R0**: данные только читаются, состояние системы не меняется.

### Предложение модели

```json
{
  "request_id": "uuid",
  "intent": "get_system_status",
  "arguments": {
    "metrics": ["disk", "memory", "cpu", "battery", "processes"],
    "process_limit": 10
  },
  "reason": "Пользователь спросил, почему система работает медленно."
}
```

Ограничения gateway:

- `intent` обязан быть равен `get_system_status`;
- `metrics` — подмножество фиксированного списка `disk`, `memory`, `cpu`, `battery`, `processes`;
- `process_limit` — целое число от 1 до 20;
- `reason` показывается пользователю, но не определяет права;
- неизвестные поля и значения отклоняются.

### Решение политики

```json
{
  "request_id": "uuid",
  "tool": "ubuntu.system.status.read",
  "risk_level": "R0",
  "decision": "allow",
  "approval_required": false,
  "allowed_scopes": ["current-user", "host-metrics"]
}
```

### Результат инструмента

```json
{
  "request_id": "uuid",
  "status": "success",
  "data": {
    "disk": [{"mount": "/", "free_gb": 42.1, "used_percent": 54}],
    "memory": {"total_gb": 7.8, "available_gb": 4.2},
    "cpu": {"usage_percent": 18.4},
    "battery": {"present": true, "charge_percent": 81, "state": "discharging"},
    "processes": [{"pid": 123, "name": "example", "cpu_percent": 12.1, "memory_mb": 250}]
  }
}
```

Ответ не содержит команд, переменных окружения, токенов, содержимого файлов или необработанных логов.

## Audit event

```json
{
  "event_id": "uuid",
  "request_id": "uuid",
  "timestamp": "2026-08-17T00:00:00Z",
  "intent": "get_system_status",
  "tool": "ubuntu.system.status.read",
  "risk_level": "R0",
  "decision": "allow",
  "result": "success"
}
```

Журнал хранит только метаданные действия. Параметры и результат перед записью проходят redaction.
