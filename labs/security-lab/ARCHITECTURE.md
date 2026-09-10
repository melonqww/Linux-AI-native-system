# Security Center MVP — архитектура

**Статус:** предложение для обсуждения, без production-реализации.

## 1. Решение

Security Center создаётся как один first-party capability-домен, а не набор
мелких пользовательских модулей. Внутри домена полномочия разделяются между
процессами. `labs/security-lab` хранит проектную документацию и будущие fixtures,
но на первом этапе не является самостоятельным test harness или runner.
Специализированная проверка будет третьим контуром `Security Campaign` внутри
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

Первый реальный антивирусный движок подключается через adapter. Его отсутствие
отображается как `unavailable` или `partial`, а не скрывается внутренней
эвристикой.

### Posture Collector

Выполняет только allowlisted read-only probes. Вывод системных команд не
передаётся напрямую пользователю: parser принимает ожидаемый формат, ограничивает
размер и формирует типизированные observations.

### Finding Store

Хранит findings и историю изменения их состояния. Сырые файлы, секреты, полный
вывод процессов и содержимое документов в базе не сохраняются. Task Ledger
получает только безопасную пользовательскую сводку.

### Quarantine Worker

После одобренного server-owned плана повторно открывает объект безопасным
способом, проверяет его идентичность и перемещает в выделенное хранилище.
Операция записывает receipt, необходимый для восстановления. Безопасное удаление
из карантина откладывается за пределы MVP.

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
→ hash/type/signature detectors
→ нормализация observations
→ deterministic verdict policy
→ сохранение finding
→ безопасный результат пользователю
```

Повторное сканирование создаёт новое observation. История не переписывается
задним числом при обновлении правил.

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

`labs/security-lab` является namespace для этой архитектуры и будущих безопасных
fixtures. Отдельного конкурирующего runner здесь на первом этапе нет.

Существующая `AI Scenario Lab` получит третий самостоятельный режим
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

1. Контракты findings и deterministic verdict policy.
2. Bounded File Scanner с fake detector для полностью автономных тестов.
3. Adapter реального локального антивирусного движка.
4. Read-only Posture Collector.
5. Finding Store и безопасная проекция в UI/Task Ledger.
6. Карантин с prepare/commit, receipt и restore.
7. Третий `Security Campaign` в `AI Scenario Lab`, безопасные fixtures и Linux
   VM integration.

Каждый этап должен оставлять систему полезной и отключаемой. Дополнительные
фоновые и привилегированные возможности начинаются только после стабильного MVP.
