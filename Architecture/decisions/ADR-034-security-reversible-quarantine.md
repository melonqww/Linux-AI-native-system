# ADR-034: обратимый same-filesystem карантин

**Статус:** принято и реализовано в `security.center` `0.6.0`

## Контекст

Автоматическое удаление необратимо, а copy-delete создаёт сложные crash и
race-сценарии.

## Решение

Карантин состоит из prepare, approved commit и approved restore. Worker хранит
durable receipt, повторно проверяет finding digest и использует только атомарный
rename на том же filesystem. Если это невозможно, операция отклоняется.

Хранилище получает server-owned UUID names и отделено от scan roots. Restore
отказывается перезаписывать существующий объект. Finding становится resolved
после commit и active после restore.

## Последствия

MVP обеспечивает обратимую изоляцию обычных пользовательских файлов без delete.
Он не поддерживает cross-filesystem fallback, системные файлы, лечение,
автоматический карантин и уничтожение quarantined objects.
