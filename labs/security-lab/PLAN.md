# Security Center MVP — план работ

**Статус:** план согласования; реализация ещё не начата.

## Результат MVP

Мы создаём один отключаемый first-party модуль `security.center`, который без AI
и без обязательной сети выполняет четыре пользовательские функции:

1. сканирует явно выбранный файл или ограниченный каталог;
2. создаёт объяснимый структурированный verdict и сохраняет finding;
3. после подтверждения помещает тот же проверенный файл в обратимый карантин;
4. выполняет ограниченный read-only аудит Ubuntu.

MVP не является полной endpoint-защитой: он не обещает обнаружение неизвестного
вредоносного ПО, не исправляет систему автоматически и не работает постоянно в
фоне.

## Главный принцип

Security Center нельзя защитить выдачей всех прав одному процессу. Мы защищаем
его отсутствием лишних прав и короткими проверяемыми каналами между компонентами.

```text
UI / Workspace Runtime
        |
        | server-owned plan_id
        v
Permission Gateway
        |
        | capability + scope + deadline
        v
Security Coordinator
   |            |              |
   | scan job   | posture job  | finding command
   v            v              v
File Scanner  Posture       Finding Store
   |
   v
Quarantine prepare -> approval -> Quarantine commit
```

Ни текст пользователя, ни manifest, ни detector output не являются полномочием.
Право на действие появляется только из trusted policy ядра.

## Этап 0 — границы до кода

Первым результатом должны стать согласованные контракты, а не работающий сканер.
Нужно определить:

- какие компоненты являются отдельными процессами;
- кто создаёт и проверяет `plan_id`, `job_id` и deadline;
- какие поля разрешены в каждом сообщении;
- как представить выбранный файл без доверия к произвольному пути;
- кто владеет finding database и quarantine directory;
- какие состояния допустимы у scan, finding и quarantine operation;
- какой компонент имеет право менять каждое состояние;
- какие ошибки являются terminal и fail-closed;
- какие данные можно показывать UI и Task Ledger;
- какие данные никогда не передаются модели и индексатору.

Этап завершён, когда для каждого перехода можно ответить: кто отправитель, кто
получатель, что проверяется, какое право требуется и что происходит при сбое.

## Контракты общения

### Общий request envelope

Каждый внутренний запрос должен иметь закрытую схему:

```text
protocol_version
request_id
plan_id
step_id
capability_id
operation
resource_reference
deadline
payload
```

Парсер принимает только известную версию, точный набор полей и разрешённую
операцию. Лишнее поле, неверный тип, истёкший deadline, повторный `request_id`
или неизвестная capability приводят к отказу до обращения к файлу.

### Scan result

Scanner возвращает observations, а не системное решение:

```text
job_id
asset_identity
content_digest
detector_id
detector_version
rule_version
observations
scan_state
limits_applied
```

Coordinator принимает результат только для активного `job_id`, от ожидаемого
worker и до deadline. Неизвестный detector, malformed output, crash или timeout
дают `unknown`/`unavailable`, но никогда `no_threat_detected`.

### Quarantine command

Quarantine Worker получает закрытую операцию, а не shell-команду:

```text
quarantine | restore
approved_plan_id
asset_identity
expected_digest
destination_role
deadline
```

Перед изменением worker повторно проверяет identity и digest. Несовпадение
закрывает операцию без попытки обработать похожий файл.

## Защита от воздействия

В MVP мы защищаемся от ошибочного ввода, недоверенных файлов, обычного
пользовательского процесса и компрометации одного непривилегированного worker.
Полный root-компромисс и физическая подмена диска остаются вне этой границы.

Обязательные свойства будущей реализации:

- production не импортирует код или fixtures из `labs/security-lab`;
- процессы запускаются с очищенным окружением и фиксированным entrypoint;
- worker нельзя подменить через `PATH`, `PYTHONPATH` или пользовательский config;
- IPC использует защищённый локальный endpoint и проверяет peer identity;
- сообщения имеют строгую схему, размер, deadline и защиту от replay;
- scanner не имеет сети и записи за пределами временной рабочей области;
- posture collector выполняет только фиксированные read-only probes;
- quarantine worker пишет только в quarantine storage;
- quarantine storage не индексируется и не открывается как обычный файл;
- изменения используют server-owned plan и повторную policy-проверку;
- состояние и receipts записываются атомарно;
- ошибка проверки целостности переводит модуль в `degraded`/`unavailable`;
- код, база правил, findings и quarantine хранятся раздельно.

В установленной Ubuntu код должен стать root-owned и недоступным для записи
обычному пользователю. Подпись релиза и проверка файлов при запуске планируются
до beta, но не заменяют изоляцию процессов и Permission Gateway.

## Этап 1 — контракты и базовые тесты

После утверждения этапа 0 создаются только типы, строгие parsers, manifest и
минимальный process skeleton без настоящего антивирусного движка.

Сначала нужны обычные быстрые тесты проекта. Это ещё не третий `Security
Campaign`, но они также проверяют production-модуль извне:

- Python-файлы компилируются и пакеты импортируются без side effects;
- `module.json` соответствует manifest schema v2;
- capability IDs уникальны и имеют допустимый формат;
- capability permissions входят в permissions модуля;
- каждая capability имеет trusted policy в Permission Gateway;
- risk и approval нельзя назначить или понизить через manifest;
- parsers отклоняют лишние поля, неверные типы и длинные значения;
- неизвестная версия протокола возвращает bounded reason code;
- истёкший deadline и повторный request ID отклоняются;
- недоверенный path не становится разрешённым resource reference;
- направление импортов одностороннее: тесты могут импортировать production
  package, а production package не импортирует `labs/security-lab` или
  `labs/ai-scenario-lab`;
- импорт модуля не запускает scan, subprocess, сеть или запись;
- отключённый модуль не публикует capabilities;
- ошибка старта оставляет модуль недоступным и не ломает Registry.

Эти проверки размещаются рядом с будущим модулем и в существующих
`tests/structure`, Capability Registry и Permission Gateway tests. Они не требуют
Ubuntu, Ollama, антивирусной базы или сети.

## Этап 2 — детерминированное сканирование

- bounded обход явно выбранного объекта;
- безопасное открытие без исполнения содержимого;
- фиксация identity, digest и типа;
- fake detector для автономных contract-тестов;
- adapter первого локального антивирусного движка;
- нормализация результата и fail-closed verdict policy;
- лимиты файлов, глубины, времени, памяти и вывода.

Критерий этапа: одинаковые fixtures дают структурно одинаковые результаты, а
сбой detector не выдаётся за отсутствие угрозы.

## Этап 3 — findings и базовый posture

- локальный Finding Store с ограниченной схемой;
- безопасная проекция результата в UI и Task Ledger;
- allowlisted Ubuntu probes;
- security updates, firewall, AppArmor, ports, autostart и rules status;
- состояния `healthy`, `finding`, `partial` и `unavailable`.

Критерий этапа: аудит ничего не изменяет, а finding содержит detector, версию
правил и evidence codes.

## Этап 4 — обратимый карантин

- server-owned `prepare` и понятный preview;
- одноразовое approval;
- повторная проверка identity/digest;
- изоляция в закрытом хранилище;
- receipt, restore и rollback;
- отсутствие безвозвратного удаления.

Критерий этапа: deny, timeout, replay, path race и crash не приводят к скрытому
изменению файла.

## Этап 5 — Security Campaign и Linux integration

После стабилизации production-контрактов существующая `AI Scenario Lab` получает
третий отдельный контур `Security Campaign`. Он импортирует реальные
production-файлы и функции `security.center`, создаёт `VirtualSecurityHost` и
запускает безопасные attack fixtures, containment/fault scenarios и отчёты.
Campaign не содержит второй реализации scanner, workers или policy.

`labs/security-lab` остаётся проектной документацией и namespace для безопасных
security fixtures. Отдельный runner и второй тестовый backend в этой папке на
первом этапе не создаются. Production-модуль не запускает Campaign и не проверяет
сам себя.

Отдельный Ubuntu VM набор затем проверяет реальные filesystem permissions,
антивирусный adapter, IPC peer identity и карантин. Живые вредоносные образцы в
репозитории не используются.

## Ближайшие решения

1. Утвердить сообщения и владельцев состояний из этапа 0.
2. Зафиксировать permission scopes и risk каждой capability.
3. Выбрать безопасную ссылку на файл между core и scanner.
4. Определить state machines для scan job, finding и quarantine operation.
5. После этого создать manifest, contracts и базовые быстрые тесты.

До первых четырёх решений писать scanner, quarantine и `Security Campaign`
преждевременно: иначе критические границы станут случайным следствием кода.
