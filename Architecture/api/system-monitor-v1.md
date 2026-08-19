# System Monitor API v1

## Назначение

`system.monitor` — first-party read-only capability для левой вкладки. Модуль
работает отдельным worker-процессом без root и получает только ограниченные
метрики из Linux `/proc`, `/sys` и `statvfs`.

`GET /v1/system-status` возвращает JSON snapshot со `schema_version: 1`:

- CPU usage, logical CPU count, load average и доступная температура;
- RAM, available memory и swap;
- батарея и её состояние;
- до 32 реальных файловых систем;
- до 20 процессов для панели (контракт модуля допускает максимум 50);
- до 16 thermal sensors;
- стабильные warning-коды при частично недоступных источниках.

Snapshot ограничен одним runtime frame 64 KiB. Отсутствие батареи, датчика или
прав чтения не считается падением всего модуля. На неподдерживаемой платформе
возвращается `supported: false`.

## Граница безопасности

Capability не отправляет сигналы процессам, не меняет приоритет, mount state или
питание. Будущая операция завершения процесса должна иметь отдельную capability,
trusted Permission Gateway policy, защищённый Unix endpoint и явное
подтверждение. UI не может получить это право из manifest.
