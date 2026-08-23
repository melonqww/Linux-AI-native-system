# Model Catalog API v1

Backend-контракт для отображения установки локальных моделей. Визуальное окно
и его кнопки принадлежат frontend; backend хранит решение, проверяет Ollama и
управляет загрузкой в отдельном модуле `model.ollama`.

Для обычного frontend-потока предпочтителен единый
[`inference-lifecycle-v1`](inference-lifecycle-v1.md), который учитывает
готовность Ollama. Этот низкоуровневый API остаётся для решений по моделям.

Оба метода доступны только через защищённый Unix socket с проверкой peer
credentials. Loopback HTTP возвращает `403 secure_transport_required`.

## Получение каталога

`POST /v1/models/catalog` с пустым объектом `{}` возвращает:

```json
{
  "schema_version": 1,
  "models": [
    {
      "model_id": "workspace.qwen",
      "provider": "ollama",
      "provider_model": "qwen3.5:2b",
      "display_name": "Qwen 3.5 2B",
      "role": "workspace_base",
      "required": true,
      "estimated_size_bytes": 2700000000,
      "installed": false,
      "decision": "unset",
      "prompt_required": true,
      "state": "consent_required",
      "progress_percent": null,
      "completed_bytes": 0,
      "total_bytes": null,
      "reason": "user_decision_required"
    }
  ]
}
```

Стабильные `model_id`:

- `workspace.qwen` — обязательная базовая модель Workspace;
- `assistant.llama` — необязательная Llama 3.2 3B.

Frontend показывает окно только при `prompt_required: true`. Значения `state`:
`consent_required`, `deferred`, `declined`, `starting`, `downloading`, `ready`,
`unavailable`, `error`.

## Ответ пользователя

`POST /v1/models/respond`:

```json
{"model_id":"assistant.llama","decision":"download"}
```

Разрешённые решения:

- `download` — сохранить согласие и начать фоновую загрузку;
- `later` — не показывать окно следующие 24 часа;
- `never` — больше не показывать окно до нового явного решения.

Ответ содержит актуальную запись выбранной модели в том же формате, что элемент
`models`. Для прогресса frontend периодически повторяет запрос каталога.

Установка LLaMA не меняет модель Workspace автоматически. Модельный routing по
ролям будет отдельным контрактом, поэтому необязательная модель не может
неожиданно изменить поведение базового общения.
