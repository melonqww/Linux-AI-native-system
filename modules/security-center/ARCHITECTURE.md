# Security Center MVP — архитектура

**Статус:** целевая архитектура; foundation, File Scanner, optional clamd,
quick/full coordinator, Finding Store, read-only Ubuntu posture и обратимый
карантин, пользовательские режимы, фоновые scan jobs и подтверждаемый quarantine
UI реализованы в `security.center` `0.9.0`; первый внешний Security Campaign
реализован в AI Scenario Lab.

## 1. Решение

Security Center создаётся как один first-party capability-домен, а не набор
мелких пользовательских модулей. Внутри домена полномочия разделяются между
процессами. Проектная документация хранится рядом с модулем, а будущие fixtures
принадлежат `labs/ai-scenario-lab`; отдельный конкурирующий runner не создаётся.
Специализированная проверка является третьим контуром `Security Campaign` внутри
существующей `AI Scenario Lab` и не поставляется как часть runtime.

```text
Пользователь / системная панель
              |
              v
      Workspace Runtime
              |
              v
      Permission Gateway
              |
              v
     security.center coordinator
        |          |          |
        v          v          v
  file scanner  posture    finding store
        |
        v
  quarantine worker
```

В MVP нет privileged broker, фонового monitor и AI-компонента.

## 2. Защищаемые свойства

Архитектура должна обеспечивать следующие проверяемые инварианты:

1. Сканирование не выполняет и не изменяет проверяемое содержимое.
2. Сканер читает только явно разрешённый объект и не следует за непроверенными
   symlink или изменившимся путём.
3. Ошибка, timeout или отсутствие движка никогда не становится clean verdict.
4. Verdict создаётся детектором по фактам, а не текстом модели или manifest.
5. Карантин применяется к той же идентичности файла, которая была проверена.
6. Любое изменение требует trusted policy и, где необходимо, подтверждения.
7. Карантин можно отменить без потери исходного содержимого и метаданных в
   поддерживаемой области.
8. Данные карантина не индексируются и не передаются модели.
9. Компрометация одного detector adapter не выдаёт сеть, root или произвольную
   запись в файловую систему.
10. Отключение или сбой Security Center не повышает права остальных модулей.
11. Только scanner открывает выбранный файл; внешний detector получает bounded
    поток, но не filesystem path.
12. Локальный detector peer принимается только через `AF_UNIX` после обязательной
    проверки UID посредством `SO_PEERCRED`; TCP запрещён.

## 3. Компоненты MVP

### Security Coordinator

Принимает только строгие capability-запросы, создаёт bounded task, вызывает
worker и агрегирует структурированный результат. Coordinator не интерпретирует
текст пользователя и не принимает антивирусные решения.

### File Scanner

Работает отдельным непривилегированным процессом. Он определяет тип объекта,
вычисляет хеш, вызывает один или несколько detector adapters и возвращает
нормализованные наблюдения. Сканирование имеет лимиты размера, количества
объектов, вложенности, времени и объёма вывода.

Первый реальный антивирусный движок подключён через adapter. Его отсутствие
отображается как `unavailable` или `partial`, а не скрывается внутренней
эвристикой.

Для `0.3.0` выбран `ClamdUnixSocketDetector`. Он не является владельцем файла:
scanner безопасно открывает объект один раз, одновременно вычисляет digest,
выполняет локальные detectors и отправляет прочитанные chunks командой
`INSTREAM`. Adapter соединяется только с trusted `AF_UNIX` socket, проверяет peer
UID через `SO_PEERCRED` по непустому allowlist и не поддерживает TCP.

Команда, chunk, суммарный поток, connect/write/read timeout и reply имеют жёсткие
ceiling. Raw clamd reply и signature остаются внутри adapter; наружу выходят
только закрыто нормализованные state, reason code и observation. Adapter
optional, однако после включения в trusted configuration он обязателен для clean
coverage. Его отказ даёт `partial + unknown`, если другой detector не подтвердил
угрозу. Точное локальное совпадение сохраняет `malware_detected` даже при
`partial`, потому что неполное дополнительное покрытие не отменяет hard evidence.

### Posture Collector

Выполняет только allowlisted read-only probes. Вывод системных команд не
передаётся напрямую пользователю: parser принимает ожидаемый формат, ограничивает
размер и формирует типизированные observations.

### Finding Store

Реализованная SQLite-версия хранит подтверждённые findings, detector version,
относительную ссылку, digest, severity, timestamps и occurrence count. Сырые
файлы, абсолютные пути, секреты, полный вывод процессов и содержимое документов
в базе не сохраняются. Task Ledger в этой версии ещё не подключён.

### Quarantine Worker

После одобренного server-owned плана повторно открывает объект безопасным
способом, проверяет его идентичность и перемещает в выделенное хранилище.
Операция записывает receipt, необходимый для восстановления. Безопасное удаление
из карантина откладывается за пределы MVP.

GNOME-панель получает только bounded metadata. Prepare не меняет файл и создаёт
одноразовый receipt; commit и restore проходят через secure transport, Permission
Gateway и отдельное явное подтверждение пользователя.

## 4. Основные контракты данных

`SecurityFinding` должен содержать как минимум:

```text
finding_id
detector_id
detector_version
verdict
severity
confidence
asset_reference
content_digest
evidence_codes
observed_at
state
recommended_action_ids
```

`QuarantineReceipt` должен содержать:

```text
quarantine_id
finding_id
original_reference
quarantined_reference
content_digest
original_metadata
created_at
restore_state
```

Публичные ответы используют bounded labels и reason codes. Произвольные строки
детектора считаются недоверенными и не становятся командами или policy.

## 5. Поток сканирования

```text
явный объект
→ проверка scope
→ безопасное открытие
→ фиксация идентичности и лимитов
→ hash/type/signature detectors + optional bounded clamd INSTREAM
→ нормализация observations
→ deterministic verdict policy
→ сохранение finding
→ безопасный результат пользователю
```

Повторное совпадение той же версии detector/rule и того же digest увеличивает
occurrence count существующего finding. Новая версия detector или другой digest
создают отдельную запись, поэтому происхождение не переписывается задним числом.

`clamd` не получает путь и не открывает объект повторно. Если adapter configured,
clean verdict возможен только после корректного peer check, полной отправки
потока и валидного bounded reply. TCP, shell, subprocess и AI в этом потоке
отсутствуют.

## 6. Поток карантина

```text
finding
→ prepare с точным объектом и digest
→ preview
→ пользовательское подтверждение
→ повторная проверка объекта
→ атомарная изоляция или безопасный отказ
→ receipt
→ проверка отсутствия объекта в исходном месте
```

Если атомарное перемещение невозможно, fallback-копирование и удаление не должны
незаметно менять смысл операции. Такой случай требует отдельного безопасного
протокола и может быть отклонён в первой версии.

## 7. Модель полномочий

MVP использует минимальный набор будущих permission scopes:

```text
security.read-posture
security.read-selected-content
security.read-findings
security.write-findings
security.write-quarantine
security.restore-quarantine
```

Scope не является путём и не заменяет проверку конкретного ресурса. Manifest
может запросить scope, но только core policy определяет risk, transport,
approval, concurrency и deadline.

## 8. Граница целостности

На этапе разработки исходники остаются обычными файлами репозитория. Production-
защита кода планируется отдельно и не считается свойством MVP лаборатории.

Целевая установленная версия должна разделять:

- root-owned read-only код;
- изменяемую базу правил;
- пользовательские findings;
- закрытое карантинное хранилище;
- подписанные release metadata.

Защита от same-user изменения и проверка целостности при старте реалистичны.
Защита после полного root-компромисса требует системной цепочки доверия и не может
быть обеспечена одной папкой модуля.

## 9. Security Lab

Этот каталог является владельцем архитектуры модуля. Безопасные fixtures
живут только внутри внешнего `AI Scenario Lab`, без копии production-кода.

Существующая `AI Scenario Lab` получила третий самостоятельный режим
`Security Campaign`. Он строит одноразовый `VirtualSecurityHost` с разрешёнными
и закрытыми областями, виртуальным карантином, synthetic posture snapshots и
внешним canary. Campaign импортирует реальные production-файлы, contracts и
функции `security.center` и проверяет их извне, не копируя scanner, workers или
policy.

Направление зависимости является обязательным:

```text
AI Scenario Lab / Security Campaign  →  production security.center
production security.center           ↛  labs/*
```

Runtime-проверка собственной целостности модуля является защитной функцией, но
не тестированием и не заменяет внешний `Security Campaign`.

Типы проверок разделяются:

- unit — parser, verdict policy, лимиты и state machines;
- contract — manifest, capabilities, permissions и reason codes;
- containment — пути, symlink, races, canary и quarantine;
- fault — crash, timeout, malformed output и недоступный detector;
- Linux integration — реальные права, процессы и системные probes в VM;
- end-to-end — несколько безопасных security-запросов через Workspace Runtime.

`Security Campaign` не должна требовать Ollama: verdict и safety checks остаются
детерминированными. Отдельные пользовательские сценарии могут проверять
маршрутизацию запроса через обычный User Journey контур, но модель не может
изменить hard safety verdict модуля.

## 10. Этапы реализации

1. Контракты findings и deterministic verdict policy. ✓
2. Bounded File Scanner с fake detector для полностью автономных тестов. ✓
3. Adapter реального локального антивирусного движка. ✓
4. Quick/full coordinator и Finding Store. ✓
5. Read-only Posture Collector и безопасная публичная проекция. ✓
6. Карантин с prepare/commit, receipt и restore. ✓
7. Третий `Security Campaign` в `AI Scenario Lab` и безопасные fixtures. ✓
8. Linux VM integration с настоящим clamd и системными permissions.

Каждый этап должен оставлять систему полезной и отключаемой. Дополнительные
фоновые и привилегированные возможности начинаются только после стабильного MVP.

Этап 3 реализован в `security.center` `0.3.0` и ограничен optional ClamAV clamd
adapter через `AF_UNIX + INSTREAM`. Его контракт закреплён
в [`File Scan API v3`](../../Architecture/api/security-center-file-scan-v3.md),
а решение — в
[`ADR-031 Security`](../../Architecture/decisions/ADR-031-security-clamd-adapter.md).
Этап 4 реализован в `0.4.0`; контракты находятся в
[`API v4`](../../Architecture/api/security-center-findings-v4.md) и
[`ADR-032`](../../Architecture/decisions/ADR-032-security-finding-store-and-scan-profiles.md).
Этап 5 реализован в `0.5.0`; контракт находится в
[`Posture API v5`](../../Architecture/api/security-center-posture-v5.md), решение —
в [`ADR-033`](../../Architecture/decisions/ADR-033-security-ubuntu-posture.md).
Этап 6 реализован в `0.6.0`; контракт находится в
[`Quarantine API v6`](../../Architecture/api/security-center-quarantine-v6.md),
решение — в [`ADR-034`](../../Architecture/decisions/ADR-034-security-reversible-quarantine.md).
