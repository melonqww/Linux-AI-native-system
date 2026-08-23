# Inference Lifecycle API v1

Единый backend-снимок зависимости `Ollama → Qwen/LLaMA`. Frontend не должен
самостоятельно объединять ответы provider и model-модулей или угадывать порядок
показа окон.

`POST /v1/inference/status` с пустым `{}` доступен только через защищённый Unix
socket и всегда возвращает публичный снимок:

```json
{
  "schema_version": 1,
  "state": "action_required",
  "provider": {
    "provider_id": "ollama",
    "state": "consent_required",
    "installed": false,
    "prompt_required": true
  },
  "models": [
    {
      "model_id": "workspace.qwen",
      "state": "consent_required",
      "effective_state": "blocked",
      "blocked_by": "provider.ollama",
      "effective_prompt_required": false
    },
    {
      "model_id": "assistant.llama",
      "state": "consent_required",
      "effective_state": "blocked",
      "blocked_by": "provider.ollama",
      "effective_prompt_required": false
    }
  ],
  "errors": []
}
```

Публичные общие состояния:

- `action_required` — нужно решение пользователя;
- `provider_preparing` — Ollama загружается, устанавливается или запускается;
- `models_preparing` — загружается Qwen или LLaMA;
- `ready` — обязательная Qwen готова, optional-модель может быть отклонена;
- `blocked` — пользователь отложил или запретил обязательную зависимость;
- `error` — один из lifecycle-компонентов недоступен или завершился ошибкой.

Provider проверяется первым. Пока он не готов, модели всё равно присутствуют в
ответе, но получают `effective_state: blocked`. После готовности Ollama backend
автоматически открывает model stage без специальной команды frontend.
Поле `installed` само по себе не означает готовность: coordinator принимает
provider только при `state: ready`, то есть после ответа `/api/version`.

Если module worker не запустился или его ответ некорректен, endpoint не исчезает:
он возвращает стабильные placeholder-записи Qwen и LLaMA со state `error` и
без технических деталей. Поэтому окно ошибки можно отрисовать даже при частично
сломавшемся backend.
