# ADR-027: Security Campaign как третий контур AI Scenario Lab

**Статус:** принято 2026-09-10; runner ещё не реализован

## Контекст

Обычные unit/contract-тесты проверяют функции и схемы, а User Journey Lab —
поведение системы в разговоре с человеком. Этого недостаточно для защитного
модуля. Security Center нужно испытывать подменой входов, сбоями detectors,
гонками файловых путей, превышением лимитов и попытками выхода из разрешённой
области.

Отдельная вторая лаборатория создала бы два механизма виртуальных машин,
containment, отчётов и supervision. Самотестирование внутри `security.center`
тоже не подходит: скомпрометированный компонент не должен быть собственным
доверенным наблюдателем.

## Решение

Существующая `AI Scenario Lab` в будущем получает третий режим —
`Security Campaign`:

1. Technical/Foundation проверяет production-контракты и общий runtime.
2. User Journey проверяет достижение пользовательской цели и диалог.
3. Security Campaign детерминированно атакует границы `security.center`.

Зависимость односторонняя:

```text
AI Scenario Lab / Security Campaign  ->  production security.center
production security.center           -X-> labs/*
```

Security Campaign импортирует реальные production contracts и функции, а не
лабораторную копию. `labs/security-lab` хранит документацию и будущие security
fixtures, но на первом этапе не получает отдельный конкурирующий runner.

Контур не использует Ollama для защитного verdict. Он применяет безопасные
fixtures, deterministic oracles и независимый effect collector. Допускается
безопасная тестовая сигнатура; живые вредоносные образцы не добавляются.

Будущие классы сценариев:

- malformed и oversized requests/results;
- symlink, path traversal и замена файла между scan и quarantine;
- detector crash, timeout и unavailable;
- deny, approval timeout и replay;
- quarantine/restore и rollback;
- corrupted или неподписанные rules;
- внешний containment canary;
- fail-closed verdict при частичном отказе.

## Критерий доверия

Security Campaign не доказывает абсолютную безопасность. Она доказывает
зафиксированные свойства для конкретного source fingerprint, fixtures и
окружения. Containment breach, скрытый файловый эффект или превращение ошибки в
clean verdict является жёстким провалом независимо от среднего pass-rate.

## Последствия

- Один lab supervisor и формат отчётов обслуживают три вида проверок.
- Security-проверки используют настоящий production-модуль и не позволяют ему
  оценивать самого себя.
- Быстрые unit/contract-тесты остаются в `modules/security-center/tests`.
- Linux permissions, peer credentials и реальный антивирусный adapter требуют
  отдельного Ubuntu VM integration-набора.
- До появления scanner/quarantine contracts Security Campaign остаётся
  документированным будущим режимом и не создаёт пустой runner.

Границы модуля приняты в
[`ADR-026`](ADR-026-security-center-foundation.md).
