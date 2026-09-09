# model.ollama

First-party модуль жизненного цикла локальных моделей. Ядро запрашивает только
`model.local.ensure/status` и `model.catalog.read/respond`; решения пользователя
и фоновая загрузка весов остаются за границей ядра.

Модуль никогда не запускает и не останавливает `ollama serve`: процессом владеет
только `provider.ollama` либо внешний системный service. Если loopback API не
готов, модуль возвращает `ollama_server_unavailable`. Отсутствующие Qwen или
LLaMA загружаются через loopback Ollama API с bounded progress-состоянием только
после решения `download`.

Каталог содержит `workspace.qwen` (обязательная базовая модель),
`assistant.llama` (необязательная дополнительная модель) и
`semantic.selector` (необязательный `qwen3-embedding:0.6b` для multilingual
поиска capability). Отсутствие semantic-модели не блокирует Workspace: Turn
Router использует лексический fallback. Решения `later` и `never` сохраняются в
`model-lifecycle.sqlite3`; `later` действует 24 часа.
Публичный IPC-контракт описан в
[`Architecture/api/model-catalog-v1.md`](../../Architecture/api/model-catalog-v1.md).
