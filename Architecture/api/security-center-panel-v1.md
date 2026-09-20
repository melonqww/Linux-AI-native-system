# Security Center Panel API v1

Панель читает компактную пользовательскую проекцию через authenticated Unix IPC:

```text
POST /v1/security/snapshot
{}
```

Ответ объединяет три существующие read-only операции production-модуля:

```json
{
  "schema_version": 1,
  "module": {"state": "ready", "module_version": "1.0.0"},
  "posture": {"verdict": "no_findings", "observations": []},
  "findings": {"state": "active", "findings": []}
}
```

Runtime запрашивает не более 10 активных findings. В них допустим только
относительный путь внутри trusted resource; содержимое файла, абсолютный путь,
сырой ответ detector и traceback запрещены. Endpoint не принимает поля и
недоступен через loopback HTTP fallback.

Этот контракт ничего не изменяет: обновление экрана повторяет posture scan и
чтение Finding Store. File scan и quarantine будут отдельными командами с
resource selection и Permission Gateway approval.

Решение: [ADR-035](../decisions/ADR-035-security-center-panel-projection.md).
