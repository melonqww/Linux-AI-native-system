# Security Center File Scan API v3

**Статус:** реализованный контракт `security.center` `0.3.0`, расширяющий
[API v2](security-center-file-scan-v2.md).

## Совместимость capability

Публичная capability и запрос не меняются:

```text
security.files.scan
```

```json
{
  "resource_id": "workspace",
  "relative_path": "downloads/sample.bin"
}
```

Клиент не может передать detector configuration, socket path, transport,
timeout, command или raw clamd options. Настройка adapter принадлежит trusted
bootstrap/policy. Все правила безопасного разрешения и открытия файла из API v2
сохраняются.

## Optional clamd adapter

Если trusted configuration включает `ClamdUnixSocketDetector`, scanner:

1. самостоятельно открывает разрешённый файл и фиксирует его identity;
2. создаёт socket только семейства `AF_UNIX` по trusted bounded socket path;
3. после connect проверяет `SO_PEERCRED` и membership peer UID в непустом trusted
   allowlist;
4. отправляет фиксированную NUL-terminated wire-команду `zINSTREAM\0`;
5. передаёт уже прочитанные scanner блоки как bounded frames с 4-byte unsigned
   network-order length и завершает поток нулевым frame;
6. принимает один bounded NUL-terminated reply;
7. закрыто нормализует reply до observation либо стабильного error code.

TCP socket, DNS, URL, hostname/port, `SCAN <path>`, shell и subprocess запрещены.
`clamd` не получает `resource_id`, `relative_path` или filesystem path.

Конкретные максимумы command, chunk, total stream, reply, connect/write/read
timeouts задаются константами реализации и trusted configuration в пределах
жёстких compile-time ceilings. Общий deadline capability имеет приоритет; ни
один внутренний timeout не может продлить его.

## Нормализованный ответ v3

V3 добавляет явное покрытие detectors. Пример полного scan без совпадений:

```json
{
  "schema_version": 2,
  "resource_id": "workspace",
  "relative_path": "downloads/sample.bin",
  "status": "completed",
  "verdict": "no_threat_detected",
  "sha256": "<64 lowercase hex characters>",
  "size_bytes": 128,
  "error_code": null,
  "observations": [],
  "detectors": [
    {
      "detector": "local-signatures",
      "version": "builtin-v1",
      "state": "completed",
      "reason_code": null
    },
    {
      "detector": "clamd",
      "version": "instream-v1",
      "state": "completed",
      "reason_code": null
    }
  ]
}
```

Configured clamd недоступен, локальных совпадений нет:

```json
{
  "schema_version": 2,
  "resource_id": "workspace",
  "relative_path": "downloads/sample.bin",
  "status": "partial",
  "verdict": "unknown",
  "sha256": "<64 lowercase hex characters>",
  "size_bytes": 128,
  "error_code": "detector_unavailable",
  "observations": [],
  "detectors": [
    {
      "detector": "local-signatures",
      "version": "builtin-v1",
      "state": "completed",
      "reason_code": null
    },
    {
      "detector": "clamd",
      "version": "instream-v1",
      "state": "unavailable",
      "reason_code": "detector_unavailable"
    }
  ]
}
```

Если локальная точная сигнатура уже подтверждена, тот же отказ clamd даёт
`status=partial`, `verdict=malware_detected` и сохраняет нормализованную локальную
observation. Partial сообщает о неполном покрытии, но не отменяет полученное hard
evidence.

## Закрытые значения

Публичные detector states ограничены `completed`, `unavailable` и `failed`.
Публичные adapter error codes:

- `detector_unavailable`;
- `detector_timeout`;
- `detector_identity_rejected`;
- `detector_protocol_error`.

Clamd `FOUND` становится bounded `DetectorObservation` с detector
`clamd`. Rule ID нормализуется по строгой ASCII-грамматике и длине либо
заменяется стабильным digest-ID. Raw signature, полный reply, socket path,
credentials peer, file bytes и traceback запрещены во внешнем результате.

## Verdict policy

| Условие | Status | Verdict |
|---|---|---|
| Все configured detectors завершились, точных совпадений нет | `completed` | `no_threat_detected` |
| Любой detector дал подтверждённое совпадение, покрытие полное | `completed` | `malware_detected` |
| Clamd configured и не завершился, подтверждённых совпадений нет | `partial` | `unknown` |
| Clamd configured и не завершился, локальное точное совпадение есть | `partial` | `malware_detected` |
| Файл не удалось безопасно открыть или стабильно прочитать | `rejected`/`failed` | `unknown` |

Если clamd не configured, его отсутствие не создаёт partial: обязательными
остаются detectors базового профиля v2. `no_threat_detected` всегда означает
только отсутствие угроз среди полностью выполненного configured набора проверок.

## Не входит в v3

Запуск или настройка daemon, TCP, обновление базы ClamAV, directory/archive
orchestration, quarantine, findings persistence, background monitoring, shell,
subprocess и AI.

Архитектурное решение: [`ADR-031 Security clamd adapter`](../decisions/ADR-031-security-clamd-adapter.md).
