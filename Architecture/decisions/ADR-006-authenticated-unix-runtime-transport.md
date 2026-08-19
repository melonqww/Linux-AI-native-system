# ADR-006: Authenticated Unix runtime transport

## Статус

Принято для Linux production runtime.

## Контекст

IPv4 loopback не является границей пользователя: любой процесс в той же network
namespace может обратиться к порту. После появления R1 approval это позволяет
обойти панель и самостоятельно отправить `confirmed=true`. Нужны kernel-provided
identity, filesystem permissions и transport-level запрет R1 в fallback-каналах.

## Решение

На Linux `--transport auto` выбирает Unix domain socket в `XDG_RUNTIME_DIR`.
Каталог и socket принадлежат текущему непривилегированному пользователю и имеют
mode `0700/0600`. Каждое соединение допускается только после `SO_PEERCRED` и
совпадения реальных UID/GID; PID обязан быть положительным. IPC использует
ограниченный версионированный JSON-line контракт и общую transport-neutral
маршрутизацию runtime.

Loopback HTTP остаётся явным `--transport http` для Windows и диагностики R0. Он
не рекламирует `execution.r1.copy` и никогда не принимает R1 confirmation.

## Последствия

R1 больше не доступен через TCP, socket не виден другим пользователям, а peer
identity нельзя подменить полями JSON. Windows до отдельного authenticated named
pipe transport остаётся R0-only. Одинаковый UID всё ещё объединяет GNOME Shell и
другие процессы пользователя; для защиты от вредоносного same-user процесса
потребуется второй session-bound фактор поверх peer credentials.
