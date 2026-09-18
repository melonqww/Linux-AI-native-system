# Security Center Scan Profiles and Finding Store API v4

**Статус:** реализованный контракт `security.center` `0.4.0`, совместимый с
[File Scan API v3](security-center-file-scan-v3.md).

## Возможности

Версия сохраняет проверку одного файла и добавляет две capability:

```text
security.files.scan
security.scan.run
security.findings.list
```

### Один файл

`security.files.scan` по-прежнему принимает только доверенный `resource_id` и
относительный `relative_path`. При подтверждённом совпадении каждая bounded
observation автоматически записывается в Finding Store. Публичный ответ scan v3
не меняется.

### Quick и full

```json
{
  "resource_id": "downloads",
  "relative_path": "optional/subdirectory",
  "mode": "quick"
}
```

`quick` и `full` — детерминированные профили обхода одного заранее настроенного
root, а не право передать произвольный системный путь. Начиная с `0.7.0`,
необязательный `relative_path` ограничивает обход одной проверенной папкой внутри
root; абсолютные пути, `..` и symlink/junction boundary отклоняются.

| Профиль | Файлы | Суммарные байты | Глубина | Deadline |
|---|---:|---:|---:|---:|
| `quick` | 128 | 64 MiB | 3 | 15 s |
| `full` | 100 000 | 64 GiB | 64 | 60 s |

Каждый кандидат повторно проходит все проверки безопасного открытия одиночного
scanner. Symlink и junction не обходятся. Обход имеет стабильный лексический
порядок и не исполняет, не распаковывает и не изменяет содержимое.

Пример ответа:

```json
{
  "schema_version": 1,
  "resource_id": "downloads",
  "mode": "quick",
  "status": "completed",
  "verdict": "malware_detected",
  "error_code": null,
  "scanned_files": 12,
  "scanned_bytes": 4096,
  "threat_files": 1,
  "unknown_files": 0,
  "skipped_files": 0,
  "finding_ids": [7]
}
```

Finding IDs в ответе ограничены 64 значениями. Счётчики отражают всю
завершённую часть кампании.

### Список находок

`security.findings.list` принимает необязательные `state` (`active`, `resolved`,
`ignored`) и `limit` от 1 до 25. По умолчанию возвращаются 20 последних active
записей. Запись содержит только:

- стабильный числовой ID;
- `resource_id` и нормализованный относительный путь;
- SHA-256 и размер;
- detector, detector version, rule ID, classification и severity;
- state, первое/последнее наблюдение и счётчик повторений.

Абсолютные пути, содержимое файла, raw detector output, socket configuration,
секреты и traceback не сохраняются и не возвращаются. Одинаковая комбинация
`resource + path + hash + detector + detector version + rule` обновляет одну
запись.

## Fail-closed policy

`no_threat_detected` разрешён только если профиль завершён в своих объявленных
границах и каждый просканированный файл получил полный clean verdict. Ошибка
обхода, неизвестный результат файла, race или недоступный объект дают
`partial + unknown + scan_incomplete`; достижение лимита даёт
`profile_limit_reached`. Подтверждённая сигнатура сохраняет
`malware_detected`, даже если остальное покрытие неполно.

## Хранилище и полномочия

Путь SQLite задаёт только trusted Module Manager через
`AI_NATIVE_SECURITY_FINDINGS_DATABASE`; caller не может подменить его в payload.
На POSIX файлы базы и sidecar получают mode `0600`. Это защищает от других
непривилегированных пользователей, но не от root или компрометации владельца
процесса. Manifest запрашивает отдельные scopes `security.write-findings` и
`security.read-findings`; risk и transport по-прежнему назначает только trusted
Permission Gateway.

Архитектурное решение: [ADR-032](../decisions/ADR-032-security-finding-store-and-scan-profiles.md).
