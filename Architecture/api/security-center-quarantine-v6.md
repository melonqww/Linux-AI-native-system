# Security Center Reversible Quarantine API v6

**Статус:** реализованный контракт `security.center` `0.6.0`.

## Capabilities

- `security.quarantine.prepare` принимает active `finding_id`, повторно сканирует
  объект и создаёт receipt в состоянии `awaiting_confirmation`;
- `security.quarantine.commit` принимает server-owned `quarantine_id`, требует
  approval и атомарно перемещает тот же digest в закрытое хранилище;
- `security.quarantine.restore` требует approval и возвращает объект только если
  исходный путь свободен.

Prepare не изменяет файл. Commit и restore относятся к R1, разрешены только по
secure transport и через trusted Permission Gateway.

## Инварианты

- перед commit заново проверяется SHA-256;
- используется `os.replace` на одном filesystem; copy-delete fallback запрещён;
- quarantine root и database находятся вне scan roots;
- object name создаётся worker и не содержит исходного имени;
- POSIX root имеет mode `0700`, object — `0600`;
- commit одноразовый, повторный вызов отклоняется;
- restore не перезаписывает существующий путь;
- после каждого move digest проверяется повторно;
- безвозвратного удаления нет.

Решение: [ADR-034](../decisions/ADR-034-security-reversible-quarantine.md).
