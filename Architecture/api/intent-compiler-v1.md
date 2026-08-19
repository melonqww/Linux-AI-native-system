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
- `unavailable` — намерение понятно, но нужный модуль сейчас недоступен.

Каждый шаг плана содержит `capability`, `arguments`, `depends_on`, `risk` и
`approval_required`. Значение `ready` не является разрешением на выполнение:
изменяющие шаги всё равно проходят Permission Gateway.

Если production model adapter ещё не подключён, runtime возвращает HTTP 503 с
`{"error": "intent_compiler_unavailable"}`.
