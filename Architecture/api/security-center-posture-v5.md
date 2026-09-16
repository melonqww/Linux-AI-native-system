# Security Center Ubuntu Posture API v5

**Статус:** реализованный контракт `security.center` `0.5.0`.

## Capability

`security.posture.scan` не принимает аргументов. Trusted policy назначает R0,
scope `security.read-posture`, один concurrent scan и timeout 30 секунд.

## Проверки

Collector выполняет шесть фиксированных read-only probes:

1. cached APT simulation для security updates без refresh, lock и установки;
2. `ufw status` по фиксированному абсолютному executable path;
3. AppArmor через `/sys/module/apparmor/parameters/enabled`;
4. bounded inventory TCP listeners из `/proc/net/tcp*`;
5. bounded inventory `.desktop` autostart entries;
6. состояние встроенных scanner rules и configured clamd adapter.

Команды, пути, timeout и environment не поступают от caller. Raw output,
адреса портов, package names, абсолютные пути и содержимое не возвращаются.

Ответ содержит `supported`, `status`, `verdict` и шесть observations. Ошибка
одного probe даёт `partial + unknown`, но подтверждённый finding сохраняет
`findings_detected`. Collector ничего не устанавливает и не исправляет.

Решение: [ADR-033](../decisions/ADR-033-security-ubuntu-posture.md).
