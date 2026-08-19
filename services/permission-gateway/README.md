# Permission Gateway v1

Permission Gateway — доверенная policy-граница между `ExecutionPlan` и capability
handlers. Модель, UI и module manifest не назначают risk и не выдают scopes.

Для каждой first-party capability ядро задаёт immutable policy:

- системный risk R0–R3 и обязательность approval в плане;
- разрешённые фазы `execute`, `prepare`, `commit`;
- transports для каждой фазы;
- необходимые scopes;
- закрытый набор arguments;
- concurrency limit и cooperative deadline.

Manifest только заявляет наличие capability и запрашиваемые permissions. Gateway
отдельно проверяет актуальное состояние Capability Registry и доверенный
`ScopeGrantStore`. Отключение модуля или отзыв scope начинает действовать на
следующем вызове, даже если план был скомпилирован раньше.

`CapabilityExecutionRegistry` содержит только явно зарегистрированные core
handlers, никогда не импортирует код по имени от модели и не ставит бесконечную
очередь: при исчерпании concurrency budget запрос закрывается ошибкой busy.
Deadline передаётся handler и проверяется потоковыми storage-операциями между
блоками по 1 МиБ; Python-поток насильно не прерывается посреди записи.

Текущие trusted scopes совпадают с permission taxonomy manifests:
`filesystem.read-metadata`, `filesystem.read-content` и
`filesystem.write-content`. Наличие scope не отменяет более узкие проверки тома,
snapshot, destination, transport и одноразового approval.
