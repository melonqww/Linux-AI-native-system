# Security Center

`Security Center` — доверенный on-demand модуль защиты AI-native Linux.
Текущий код `0.4.0` предоставляет status, безопасное сканирование одного файла,
профили `quick`/`full`, Finding Store и optional интеграцию с локальным `clamd`.

## v0.2.0: файловое сканирование

Запрос не принимает абсолютный путь. Core передаёт:

- `resource_id` — идентификатор заранее настроенного и разрешённого root;
- `relative_path` — относительный путь к одному обычному файлу внутри root.

Scanner разрешает пару на своей стороне, запрещает абсолютные пути, `..`,
symlink, каталоги и специальные файлы и проверяет, что открытый объект остался
внутри root. Файл читается потоком с лимитами размера, времени и объёма ответа;
содержимое не исполняется, не изменяется и не возвращается вызывающей стороне.

Во время одного прохода вычисляется SHA-256 и работают два локальных detector:

1. exact hash detector сравнивает итоговый SHA-256 с локальным набором известных
   вредоносных digest;
2. byte-pattern detector ищет точные ограниченные последовательности байтов,
   включая совпадения на границе блоков.

Это сигнатурный сканер: он уверенно распознаёт только известные локальные
сигнатуры и не пытается «понять код». AI, сеть, subprocess, внешние антивирусные
процессы и эвристическая классификация в `0.2.0` не используются.

## v0.3.0: реальный ClamAV engine

Optional `ClamdUnixSocketDetector` добавляет проверку тем же
`security.files.scan`, не расширяя публичный запрос. Scanner сам безопасно
открывает файл и передаёт уже прочитанные bounded chunks командой `INSTREAM`.
`clamd` не получает путь, `resource_id` или право самостоятельно открывать
пользовательский объект.

Разрешён только trusted Unix socket (`AF_UNIX`). TCP, hostname/port, shell и
subprocess запрещены. До отправки содержимого adapter обязан проверить peer UID
через `SO_PEERCRED` по непустому trusted allowlist. Команда, chunks, общий поток,
reply и connect/write/read timeouts ограничены; общий deadline scan нельзя
продлить внутренней настройкой.

Ответ clamd считается недоверенным. Raw reply и raw signature не возвращаются:
adapter преобразует их в закрытые states, error codes и bounded observation.
Если adapter не configured, базовые локальные detectors продолжают работать как
в `0.2.0`. Если он configured, но недоступен или ошибся, результат становится
`partial + unknown`, а не clean. Уже подтверждённая точная локальная сигнатура
сохраняет `malware_detected` даже при partial coverage.

Допустимы только три verdict:

- `malware_detected` — хотя бы одна точная сигнатура подтверждена;
- `no_threat_detected` — файл полностью прочитан, SHA-256 вычислен и все
  обязательные detectors успешно завершились без совпадений;
- `unknown` — проверка неполна: нарушен лимит, файл недоступен или изменился,
  либо чтение не завершилось корректно.

Fail-closed означает, что ошибка никогда не превращается в
`no_threat_detected`.

## v0.4.0: scan profiles и Finding Store

Доступны три пользовательских сценария:

- `security.files.scan` — проверить конкретный файл;
- `security.scan.run` — выполнить `quick` или `full` внутри выбранного trusted
  resource root;
- `security.findings.list` — получить bounded список подтверждённых находок.

Quick ограничен 128 файлами, 64 MiB, глубиной 3 и 15 секундами. Full расширяет
границы до 2048 файлов, 512 MiB, глубины 32 и 60 секунд. Оба режима используют
тот же scanner для каждого файла, не следуют symlink/junction и становятся
`partial + unknown`, если проверка неполна.

Finding Store дедуплицирует подтверждённые observations и хранит только hash,
размер, относительный путь, detector/version/rule, severity, timestamps и
счётчик.
Содержимое и абсолютные пути не сохраняются. SQLite path задаётся Module Manager,
а не вызывающей стороной; POSIX-файлы базы ограничиваются mode `0600`.

## Границы версии

`0.4.0` сканирует один файл или bounded trusted root и возвращает JSON-safe
результат: digest, размер, итог, код ошибки и нормализованные observations. В ответ не входят содержимое файла, абсолютный
путь, секреты, произвольный detector output или traceback.

Версия не выполняет карантин, удаление, лечение, распаковку архивов, фоновый
мониторинг, аудит Ubuntu или обновление сигнатур. Scanner не имеет сети, не
запускает subprocess и не получает постоянный root. Full означает полный обход
выбранного resource в жёстких пределах профиля, а не всей операционной системы.

## Документация

- [Scan Profiles and Finding Store API v4](../../Architecture/api/security-center-findings-v4.md)
- [ADR-032: Finding Store и bounded scan profiles](../../Architecture/decisions/ADR-032-security-finding-store-and-scan-profiles.md)
- [File Scan API v3: optional clamd adapter](../../Architecture/api/security-center-file-scan-v3.md)
- [ADR-031: ClamAV clamd через AF_UNIX + INSTREAM](../../Architecture/decisions/ADR-031-security-clamd-adapter.md)
- [File Scan API v2](../../Architecture/api/security-center-file-scan-v2.md)
- [ADR-028: безопасная граница файлового сканирования](../../Architecture/decisions/ADR-028-security-file-scan-boundary.md)
- [Foundation API v1](../../Architecture/api/security-center-foundation-v1.md)
- [ADR-026: фундамент Security Center](../../Architecture/decisions/ADR-026-security-center-foundation.md)
- [ADR-027: Security Campaign](../../Architecture/decisions/ADR-027-security-campaign.md)
- [MVP, архитектура и план](../../labs/security-lab/README.md)

## Быстрые тесты

Из корня репозитория:

```bash
python -m pytest modules/security-center/tests -q
```
