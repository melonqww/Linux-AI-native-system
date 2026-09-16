# ADR-033: bounded read-only Ubuntu posture

**Статус:** принято и реализовано в `security.center` `0.5.0`

## Контекст

Файловый scan не показывает базовые защитные настройки Ubuntu. Универсальный
shell или автоматическое исправление при этом дали бы модулю чрезмерные права.

## Решение

Добавить отдельный `UbuntuPostureCollector` и capability
`security.posture.scan`. Файловый scanner не получает subprocess. Collector
может запускать только фиксированные executable с закрытыми аргументами,
окружением, timeout и bounded output и читать allowlisted системные файлы.

Результат содержит нормализованные evidence codes и counts. Недоступный probe не
считается healthy. Commit, repair и privileged операции отсутствуют.

## Последствия

Версия показывает updates, firewall, AppArmor, listening ports, autostart и
scanner rules без изменения системы. Это моментальный снимок, а не постоянный
мониторинг. Исправления и process attribution остаются отдельными этапами.
