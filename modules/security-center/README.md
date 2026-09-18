# Security Center

`Security Center` — доверенный on-demand модуль защиты AI-native Linux.
Текущий код `0.7.0` предоставляет status, безопасное сканирование одного файла,
профили `quick`/`full`, Finding Store, optional локальный `clamd` и read-only
аудит Ubuntu.

Ubuntu CI дополнительно проверяет реальные Linux-границы: `AF_UNIX` framing,
kernel `SO_PEERCRED`, отказ чужому UID до передачи команды, symlink containment и
режимы `0700/0600` приватного quarantine storage. Это первая часть Linux
integration. Отдельный Ubuntu CI job составляет вторую часть: устанавливает
настоящий системный `clamd`, запускает его под package service user с изолированной
безопасной custom signature database и проверяет production `INSTREAM` adapter.
Публичные базы не скачиваются, живые вредоносные образцы не используются.

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
границы до 100 000 файлов, 64 GiB, глубины 64 и 60 секунд. Оба режима используют
тот же scanner для каждого файла, не следуют symlink/junction и становятся
`partial + unknown`, если проверка неполна.

Finding Store дедуплицирует подтверждённые observations и хранит только hash,
размер, относительный путь, detector/version/rule, severity, timestamps и
счётчик.
Содержимое и абсолютные пути не сохраняются. SQLite path задаётся Module Manager,
а не вызывающей стороной; POSIX-файлы базы ограничиваются mode `0600`.

## v0.5.0: Ubuntu posture

`security.posture.scan` без аргументов проверяет security updates, UFW,
AppArmor, TCP listeners, системные/пользовательские autostart entries и scanner
rules. Используются только фиксированные paths/commands с очищенным окружением,
таймаутами и bounded input/output. Collector ничего не исправляет и при
неполном покрытии возвращает `partial + unknown`.

## v0.6.0: обратимый карантин

Три R1 capability реализуют `prepare → approved commit → approved restore`.
Prepare повторно сверяет finding и digest, но не меняет файл. Commit выполняет
только атомарный same-filesystem move в закрытый runtime root; copy-delete и
безвозвратное удаление запрещены. Restore не перезаписывает занятый путь и после
каждого перемещения повторно проверяет SHA-256.

## v0.7.0: пользовательские режимы проверки

Панель запускает четыре безопасных сценария: конкретный файл, папку внутри
домашнего trusted root, quick scan типичных рискованных мест и full scan всех
настроенных roots. Quick включает существующие Downloads, Desktop,
`.config/autostart` и временную область. Full проходит домашнюю и временную
области целиком в пределах bounded профиля каждого root.

Пользователь не вводит абсолютный или относительный путь: GNOME-панель открывает
системный XDG Desktop Portal picker, а optional Nautilus MenuProvider добавляет
пункт проверки в контекстное меню. Оба entry point передают runtime только
относительную ссылку внутри home. `..`, абсолютные пути, symlink/junction и выход за root
отклоняются. «Полная» означает всё, что разрешено Security Center, а не скрытое
root-право на чтение всей ОС.

## Границы версии

`0.7.0` сканирует файл, выбранную папку или все bounded trusted roots, выполняет read-only Ubuntu
posture и возвращает JSON-safe
результат: digest, размер, итог, код ошибки и нормализованные observations. В ответ не входят содержимое файла, абсолютный
путь, секреты, произвольный detector output или traceback.

Версия не выполняет удаление, лечение, распаковку архивов, фоновый
мониторинг или обновление сигнатур. Scanner не имеет сети, не
запускает subprocess и не получает постоянный root. Full означает полный обход
выбранного resource в жёстких пределах профиля, а не всей операционной системы.

## Документация

- [User Scan API v7](../../Architecture/api/security-center-user-scan-v7.md)
- [ADR-036: пользовательские quick/full scan](../../Architecture/decisions/ADR-036-security-user-scan-modes.md)
- [Panel API v1](../../Architecture/api/security-center-panel-v1.md)
- [ADR-035: безопасная проекция в панели](../../Architecture/decisions/ADR-035-security-center-panel-projection.md)
- [Reversible Quarantine API v6](../../Architecture/api/security-center-quarantine-v6.md)
- [ADR-034: обратимый same-filesystem карантин](../../Architecture/decisions/ADR-034-security-reversible-quarantine.md)
- [Ubuntu Posture API v5](../../Architecture/api/security-center-posture-v5.md)
- [ADR-033: bounded read-only Ubuntu posture](../../Architecture/decisions/ADR-033-security-ubuntu-posture.md)
- [Scan Profiles and Finding Store API v4](../../Architecture/api/security-center-findings-v4.md)
- [ADR-032: Finding Store и bounded scan profiles](../../Architecture/decisions/ADR-032-security-finding-store-and-scan-profiles.md)
- [File Scan API v3: optional clamd adapter](../../Architecture/api/security-center-file-scan-v3.md)
- [ADR-031: ClamAV clamd через AF_UNIX + INSTREAM](../../Architecture/decisions/ADR-031-security-clamd-adapter.md)
- [File Scan API v2](../../Architecture/api/security-center-file-scan-v2.md)
- [ADR-028: безопасная граница файлового сканирования](../../Architecture/decisions/ADR-028-security-file-scan-boundary.md)
- [Foundation API v1](../../Architecture/api/security-center-foundation-v1.md)
- [ADR-026: фундамент Security Center](../../Architecture/decisions/ADR-026-security-center-foundation.md)
- [ADR-027: Security Campaign](../../Architecture/decisions/ADR-027-security-campaign.md)
- [План модуля](PLAN.md)
- [Архитектура модуля](ARCHITECTURE.md)
- [Security Campaign и лабораторная стратегия](SECURITY-LAB.md)

## Быстрые тесты

Из корня репозитория:

```bash
python -m pytest modules/security-center/tests -q
```
