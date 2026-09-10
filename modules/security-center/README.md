# Security Center

`Security Center` — доверенный on-demand модуль защиты AI-native Linux. Версия
`0.1.0` намеренно является только фундаментом: она проверяет подключение к
Capability Registry, trusted policy, отдельный worker lifecycle и строгий
публичный status-контракт до появления файловых или привилегированных действий.

## Реализовано

Модуль публикует одну capability:

- `security.module.status` с permission `security.read-status`;
- без `user_intent` и без передачи operation языковой модели;
- закрытый пустой input object;
- детерминированный bounded JSON-safe ответ.

Worker поддерживает стандартный lifecycle:

```text
worker_start()
worker_health()
worker_invoke("status", {})
worker_stop()
```

Повторные start и stop безопасны. Health и invocation закрываются ошибкой, если
worker не запущен. Неизвестная operation, непустой или не-object payload
отклоняются.

## Пока не реализовано

Foundation не сканирует файлы, не обнаруживает malware, не хранит findings, не
использует карантин, сеть, subprocess, AI или root. Импорт пакета не выполняет
I/O и не запускает worker.

Следующие функции могут добавляться только отдельными capabilities вместе с
trusted policy, строгими контрактами, resource limits и тестами. Запланированные
границы не считаются готовым API.

## Документация

- [Foundation API v1](../../Architecture/api/security-center-foundation-v1.md)
- [ADR-026: фундамент Security Center](../../Architecture/decisions/ADR-026-security-center-foundation.md)
- [ADR-027: Security Campaign](../../Architecture/decisions/ADR-027-security-campaign.md)
- [Дискуссия о вариантах MVP](../../Architecture/discussions/security-center-mvp-options.md)
- [MVP, архитектура и план развития](../../labs/security-lab/README.md)

## Быстрые тесты

Из корня репозитория:

```bash
python -m pytest modules/security-center/tests -q
```
