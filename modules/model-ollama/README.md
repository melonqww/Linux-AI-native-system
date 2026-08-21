# model.ollama

First-party модуль жизненного цикла локальных моделей. Ядро запрашивает только
`model.local.ensure/status` и `model.catalog.read/respond`; проверка Ollama,
запуск доступного user-process, решения пользователя и фоновая загрузка весов
остаются за границей ядра.

Модуль никогда не использует `sudo` и не устанавливает системные пакеты. Если
Ollama не установлен, он возвращает `ollama_not_installed`. Если бинарник есть,
но локальный сервер не запущен, модуль может запустить `ollama serve` от имени
текущего пользователя. Отсутствующие Qwen или LLaMA загружаются через loopback
Ollama API с bounded progress-состоянием только после решения `download`.

Каталог содержит `workspace.qwen` (обязательная базовая модель) и
`assistant.llama` (необязательная дополнительная модель). Решения `later` и
`never` сохраняются в `model-lifecycle.sqlite3`; `later` действует 24 часа.
Публичный IPC-контракт описан в
[`Architecture/api/model-catalog-v1.md`](../../Architecture/api/model-catalog-v1.md).
