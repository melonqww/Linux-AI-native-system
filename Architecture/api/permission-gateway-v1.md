# Permission Gateway contracts v1

## Поток решения

```text
server-owned ExecutionPlan
  → CapabilityInvocation
  → PermissionGateway.evaluate
  → allow/deny decision + stable reason_code
  → bounded CapabilityExecutionRegistry
  → first-party handler
```

Invocation содержит UUID request/plan, step ID, capability ID, phase, arguments,
заявленные планом risk/approval и системный `ExecutionContext`. Последний включает
transport principal, доверенные scopes и факт consume одноразового approval.
Клиент не может передать эти поля в runtime payload.

## Фазы

- `execute` — непосредственная R0-операция;
- `prepare` — построение preview для изменяющей операции без commit;
- `commit` — изменение после approval.

Для `storage.materialize.plan-copy` prepare допускается через локальные transports,
а commit — только `internal` или `unix_peer`, с
`filesystem.read-content`, `filesystem.write-content` и consumed approval.

## Fail-closed reason codes

- `policy_not_found`;
- `capability_unavailable`;
- `declared_risk_mismatch` / `declared_approval_mismatch`;
- `phase_not_allowed` / `transport_not_allowed`;
- `approval_required` / `scope_not_granted`;
- `required_arguments_missing` / `arguments_not_allowed`;
- `invalid_argument_value` / `invalid_invocation_identity`;
- `handler_unavailable`.

Любое deny-решение останавливает handler. Audit записывает capability, phase,
transport, risk, decision и reason code, но не arguments, запрос пользователя,
пути или snippets.

## Двойная проверка permissions

Gateway отвечает за coarse-grained scope. Handler обязан повторно применять
domain-проверки. Например, `filesystem.read-content` не позволяет читать том с
актуальным разрешением `metadata`: Query Service отбрасывает старые content hits
FTS на каждом запросе. Аналогично write scope не заменяет destination validation,
SHA-256, approval и no-clobber materialization.
