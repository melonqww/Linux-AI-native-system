# System Monitor API v1

## Назначение

`system.monitor` — first-party read-only capability для левой вкладки. Модуль
работает отдельным worker-процессом без root и получает только ограниченные
метрики из Linux `/proc`, `/sys` и `statvfs`.

`GET /v1/system-status` возвращает JSON snapshot со `schema_version: 1` с
сортировкой процессов по CPU по умолчанию. Для управляемого списка процессов
доступен `POST /v1/system-status`:

```json
{"process_limit": 20, "process_sort": "memory", "process_order": "desc"}
```

Допустимые значения: `process_sort` — `cpu` или `memory`, `process_order` —
`asc` или `desc`, `process_limit` — от 1 до 50.

- CPU usage, физические ядра, логические потоки, CPU packages, load average и
  доступная температура;
- RAM, available memory, swap и доступная без root EDAC-топология модулей;
- батарея и её состояние;
- до 32 пользовательских block-backed томов с total/used/free; Snap, loop,
  squashfs, сетевые и виртуальные mount'ы не возвращаются;
- до 20 процессов для панели (контракт модуля допускает максимум 50);
- до 16 thermal sensors;
- стабильные warning-коды при частично недоступных источниках.

Snapshot ограничен одним runtime frame 64 KiB. Отсутствие батареи, датчика или
прав чтения не считается падением всего модуля. На неподдерживаемой платформе
возвращается `supported: false`.

`memory.installed_modules`, `channel_count` и `channel_mode` читаются из EDAC
или доступных текущему пользователю SMBIOS Type 17 records. Они имеют значение
`null`/`unknown`, если ядро или драйвер памяти не публикует топологию. Сервис не
требует root и не запускает `dmidecode`, поэтому не подменяет недоступные
аппаратные сведения догадками.

## Граница безопасности

Capability не отправляет сигналы процессам, не меняет приоритет, mount state или
питание. Будущая операция завершения процесса должна иметь отдельную capability,
trusted Permission Gateway policy, защищённый Unix endpoint и явное
подтверждение. UI не может получить это право из manifest.
