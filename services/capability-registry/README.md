# Capability Registry

Минимальная реализация модульного user-space core из ADR-003. Registry читает
строгие `module.json`, сохраняет пользовательское enable/disable, вычисляет
состояния через граф зависимостей и публикует только capabilities доступных
модулей.

## Запуск

```powershell
$env:PYTHONPATH = "services\capability-registry\src"

python -m ai_native_capabilities validate services/storage-catalog/module.json
python -m ai_native_capabilities sync services
python -m ai_native_capabilities list
python -m ai_native_capabilities capabilities
python -m ai_native_capabilities providers documents.text.search

python -m ai_native_capabilities disable documents.index
python -m ai_native_capabilities enable documents.index
python -m ai_native_capabilities quarantine documents.index "health check failed"
python -m ai_native_capabilities clear-quarantine documents.index
```

База по умолчанию: `data/capabilities/registry.sqlite3`.

## Что гарантируется

- неизвестные поля и некорректные manifests отклоняются;
- `entrypoint.python_path` не выходит из каталога модуля;
- несовместимый `core_api`, отсутствующая зависимость и цикл дают объяснимое
  состояние `unavailable`;
- выключенный или quarantined модуль не публикует capabilities;
- отключение зависимости автоматически скрывает capabilities зависимого модуля;
- повторный sync не сбрасывает пользовательский выбор enable/disable;
- manifest объявляет запрашиваемые права, но не выдаёт их модулю.

Текущая версия является Registry, а не process supervisor: она ещё не запускает
entrypoint, не выполняет health checks и не выдаёт permission scopes. Эти
операции добавляются следующим слоем Module Manager поверх стабильного Registry.
