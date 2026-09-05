# Intent Compiler API v1

## Endpoint

`POST http://127.0.0.1:8765/v1/intent/compile`

```json
{"text": "Найди PDF по математике и скопируй результаты на рабочий стол"}
```

Другие поля запрещены. Активная коллекция, destination и разрешения никогда не
принимаются из HTTP body: runtime подставляет их из доверенного состояния задачи.
Endpoint строит preview и ничего не исполняет.

## Состояния результата

- `ready` — все capability доступны, план можно передать policy layer;
- `needs_clarification` — не хватает смысла/контекста или model output отклонён;
- `unavailable` — зарезервированное состояние для capability, исчезнувшей после
  построения плана; штатно неисполняемые операции заранее исключаются из model
  schema и semantic functions.

Каждый шаг плана содержит `capability`, `arguments`, `depends_on`, `risk` и
`approval_required`. Значение `ready` не является разрешением на выполнение:
изменяющие шаги всё равно проходят Permission Gateway.

Набор операций не является частью статической API-схемы. Runtime строит его из
включённых контрактов Capability Registry, реально зарегистрированных execution
handlers и доверенных Permission Gateway policies. Поэтому отсутствующий или
отключённый модуль не может быть предложен моделью как исполнимая команда.

По умолчанию runtime использует локальный Ollama adapter с `qwen3.5:2b`. Если
Ollama или модель временно недоступны, ответ имеет `needs_clarification` и
диагностику `provider_unavailable`; остальные runtime-возможности продолжают
работать. HTTP 503 `intent_compiler_unavailable` используется, когда compiler
явно отключён флагом `--no-intent-compiler`.
