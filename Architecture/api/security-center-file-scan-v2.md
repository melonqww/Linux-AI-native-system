# Security Center File Scan API v2

**Статус:** контракт для реализации `security.center` `0.2.0`.

## Capability

```text
security.files.scan
```

Capability read-only и on-demand. Она не передаёт модели содержимое файла и не
принимает абсолютный путь, команды, detector configuration или карантинное
действие. Risk, transport, scope, timeout и доступные `resource_id` задаёт
trusted policy, а не manifest.

## Запрос

```json
{
  "resource_id": "workspace",
  "relative_path": "downloads/sample.bin"
}
```

- `resource_id` — bounded opaque ID настроенного root из trusted mapping;
- `relative_path` — bounded относительный путь к одному обычному файлу внутри
  этого root.

Схема закрыта для лишних полей. Пустые значения, absolute path, `..`, symlink,
каталог, специальный файл и неизвестный `resource_id` отклоняются до чтения
содержимого. Безопасность определяется идентичностью открытого объекта, а не
одной строковой проверкой пути.

## Сканирование

Файл читается блоками до EOF с явными лимитами размера, deadline, размера блока,
числа observations и итогового JSON. Один поток данных используется для:

- SHA-256 всего файла;
- поиска точных bounded byte patterns с сохранением минимального overlap между
  блоками.

После полного чтения exact hash detector сравнивает SHA-256 с локальной базой.
База должна содержать хотя бы одну валидную hash- или byte-сигнатуру, иначе она
отклоняется при доверенной загрузке. Каждая observation содержит только
стабильные detector, rule ID, классификацию и severity. Байты файла и
произвольный текст правила не возвращаются.

## Ответ

```json
{
  "schema_version": 1,
  "resource_id": "workspace",
  "relative_path": "downloads/sample.bin",
  "status": "completed",
  "verdict": "no_threat_detected",
  "sha256": "<64 lowercase hex characters>",
  "size_bytes": 128,
  "error_code": null,
  "observations": []
}
```

Публичный ответ bounded и JSON-safe; абсолютный путь, содержимое, environment,
секреты, traceback и raw detector output запрещены.

## Verdict

| Условие | Verdict |
|---|---|
| Есть валидное точное hash/byte-pattern совпадение | `malware_detected` |
| Файл полностью и стабильно прочитан; настроенные сигнатуры не совпали | `no_threat_detected` |
| Любая другая ситуация | `unknown` |

`unknown` включает I/O error, limit/deadline, изменение файла, недоступный root и
неполный digest. Он не означает подтверждённое malware.

## Не входит в v2

Каталоги и архивы, AI, сеть, subprocess, внешние AV engines, quarantine,
удаление/лечение, background monitoring, findings persistence и обновление
правил.

Архитектурное решение: [`ADR-028`](../decisions/ADR-028-security-file-scan-boundary.md).
