# Ollama Provider API v1

Backend-контракт окна установки Ollama. Визуальное окно принадлежит frontend,
а загрузка, проверка и user-local установка выполняются модулем
`provider.ollama`.

Методы доступны только через Unix socket с проверкой peer credentials.
Loopback HTTP возвращает `403 secure_transport_required`.

## Состояние

`POST /v1/providers/ollama/status` с `{}`:

```json
{
  "schema_version": 1,
  "provider_id": "ollama",
  "display_name": "Ollama",
  "state": "consent_required",
  "installed": false,
  "managed": false,
  "version": null,
  "decision": "unset",
  "prompt_required": true,
  "progress_percent": null,
  "completed_bytes": 0,
  "total_bytes": null,
  "reason": "user_decision_required"
}
```

Состояния: `consent_required`, `deferred`, `declined`, `downloading`,
`installing`, `ready`, `error`, `unsupported`. `managed: true` означает, что
используется копия внутри данных AI-native Linux; `false` при системной Ollama.

## Решение пользователя

`POST /v1/providers/ollama/respond`:

```json
{"decision":"install"}
```

- `install` — сохранить согласие и начать фоновую установку;
- `later` — не показывать окно 24 часа;
- `never` — больше не показывать до нового явного решения.

Ответ имеет тот же формат, что status. Во время загрузки frontend периодически
повторяет status. Повторное `install` после `error` запускает новую попытку.

## Граница безопасности

Установка не получает root и не меняет `/usr`. Модуль получает metadata
последнего официального GitHub release, требует опубликованный SHA-256 digest,
проверяет архив и атомарно переключает управляемую версию. Системные драйверы
CUDA/ROCm модуль не устанавливает.
