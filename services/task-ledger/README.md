# Task Ledger

Core service for the seven-day, user-facing activity history. It is not a
plugin, executor, notification renderer, or debug log.

The ledger stores only structured lifecycle facts, aggregate counters, safe
checkpoints, and local object references. The normal projection never contains
exception text, tracebacks, source code, model reasoning, queries, document
contents, or technical failure reasons.

Control semantics are intentionally small:

- a running task may receive a cooperative cancellation request;
- a system-interrupted task may continue from its last safe checkpoint;
- a user-cancelled task is terminal and cannot be continued;
- work without a safe checkpoint fails closed after a restart.

Terminal history is retained for seven days by default. Active, approval-waiting,
and interrupted tasks are not removed by retention cleanup.
