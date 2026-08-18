# Storage catalog

Три связанных компонента для системного поиска AI-native Linux:

- **Volume Registry** обнаруживает диски и точки монтирования и отдельно хранит
  разрешение `none`, `metadata` или `content`, а также общий и свободный объём;
- **File Catalog** инкрементально хранит пути, типы, размеры, роли и классы
  чувствительности, не переходя границу файлового устройства;
- **Virtual Collections** сохраняет динамические запросы или снимки ссылок на
  оригинальные файлы без автоматического копирования.

Системный том при первом обнаружении получает разрешение `content`.
Дополнительные и съёмные тома получают `none`, пока пользователь явно не изменит
разрешение. Метаданные чувствительных файлов могут находиться в каталоге, но
обычный поиск их не возвращает.

## Команды

Из корня репозитория:

```powershell
$env:PYTHONPATH = "services\storage-catalog\src"

python -m ai_native_storage volumes refresh
python -m ai_native_storage volumes list
python -m ai_native_storage volumes permission VOLUME_ID metadata

python -m ai_native_storage catalog scan VOLUME_ID
python -m ai_native_storage catalog search --extension pdf
python -m ai_native_storage catalog search --name algebra --extension pdf
python -m ai_native_storage catalog status

python -m ai_native_storage collections create-smart "PDF по математике" --extension pdf --name math
python -m ai_native_storage collections create-snapshot "Найденные PDF" --extension pdf
python -m ai_native_storage collections list
python -m ai_native_storage collections show COLLECTION_ID
```

По умолчанию база хранится в `data/storage/catalog.sqlite3`. CLI не запускает
полное сканирование автоматически: сначала реестр обнаруживает хранилища, затем
runtime или пользователь запускает каталогизацию разрешённого тома.

Текущая версия каталогизирует метаданные. Поиск по извлечённому содержимому
обслуживается `services/indexer`; следующим интеграционным слоем станет единый
query service, объединяющий оба результата.

## Границы текущей реализации

- нет фонового watcher/daemon;
- platform discovery пока видит смонтированные файловые системы; интеграция с
  UDisks2 для обнаружения и монтирования отключённых разделов остаётся следующим
  Linux-адаптером;
- нет извлечения текста из PDF;
- нет materialize-операции копирования или перемещения;
- каталог не повышает привилегии и сохраняет недоступные области как ошибки;
- virtual collection содержит ссылки, а не резервные копии файлов.
