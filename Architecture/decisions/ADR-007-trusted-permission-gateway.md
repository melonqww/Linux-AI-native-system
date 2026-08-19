# ADR-007: Trusted Permission Gateway and capability execution registry

## Статус

Принято для core v0.1.

## Контекст

Intent Planner и manifests знают названия capabilities, но являются недостаточной
границей для выполнения. Если risk, permissions или handler routing выводить из
manifest/model output, модуль сможет повысить себе права. Ветвление оркестратора
по каждому новому capability также быстро превращается в непроверяемый hardcode.

## Решение

Core хранит независимые immutable policies. Manifest объявляет provider и
requested permissions, но не изменяет policy и не выдаёт grant. Перед каждым
handler-вызовом Gateway повторно проверяет policy, актуальную доступность модуля,
risk/approval плана, phase, kernel-derived transport context, trusted scopes и
закрытый argument contract.

Handlers регистрируются кодом сборки в bounded registry. Dynamic import по данным
модели запрещён. Concurrency исчерпывается fail-closed; cooperative deadline
передаётся handler, потому что принудительное завершение Python thread посреди R1
может оставить неопределённый результат.

R1 разделён на prepare/commit. Commit требует consumed approval и secure transport
даже при прямом внутреннем вызове оркестратора. Все policy decisions имеют
metadata-only audit.

## Последствия

Новые модули требуют явной core-policy и регистрации handler — одной записи в
manifest недостаточно. Это намеренное трение безопасности. Scopes можно отзывать
во время работы, а отключение provider блокирует старые планы. Domain handlers
сохраняют собственные более узкие проверки; Gateway не заменяет volume ACL,
filesystem containment или rollback.
