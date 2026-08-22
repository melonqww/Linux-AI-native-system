# Runtime index status v1

`GET http://127.0.0.1:8765/v1/index-status` возвращает read-only состояние:

```json
{
  "catalog": {"entries": 1200, "by_volume": {"volume-id": 1200}},
  "content_index": {"sources": 95, "chunks": 410},
  "scheduler": {
    "state": "idle",
    "queued": 0,
    "processed": 30,
    "failed": 0,
    "last_error": null,
    "active_rescans": 0,
    "inaccessible": 12,
    "coverage_complete": true,
    "covered_volume_ids": ["volume-id"],
    "scanning_volume_ids": [],
    "thread_alive": true
  }
}
```

Возможные состояния scheduler: `idle`, `updating`, `paused_load`, `degraded`.
Endpoint не запускает scan и ничего не изменяет.

После каждого запуска scheduler ставит reconcile-scan для уже известных
разрешённых дисков. Пока `coverage_complete=false`, нулевой результат поиска
считается предварительным.
