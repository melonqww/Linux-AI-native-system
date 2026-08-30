# software.manager

Автономный Linux-модуль установки и удаления приложений с долговечными задачами,
прогрессом, остановкой и продолжением после перезапуска. Первая реализация
работает со штатным Unix API `snapd`, не запускает shell/`sudo` и не принимает
пароли.

Модуль зарегистрирован в Capability Registry как отдельный background worker.
Core и GNOME-панель получают snapshot каталога, задач и бэкапов через
аутентифицированный Unix IPC. Install/remove разделены на prepare/commit и
защищены Permission Gateway; pause/resume/cancel доступны только через secure
task-control endpoint. Удаление нельзя запустить без второй confirmation-stage.

## Возможности v0.3

- встроенный каталог из 20 точных Snap package IDs;
- одна явная confirmation-стадия для установки и две для удаления, включая
  финальное «Вы точно хотите удалить?»;
- фактический прогресс из task progress snapd; неизвестный процент остаётся
  `null`;
- во время скачивания публикуются полученные/общие байты, сглаженная скорость и
  примерное оставшееся время; после установки создаётся подтверждаемое событие
  уведомления, которое переживает перезапуск;
- Linux-диспетчер доставляет событие через стандартный `notify-send` в системный
  центр уведомлений GNOME/KDE; панель не показывает отдельную внутреннюю плашку;
- SQLite-состояние и внешний change ID переживают закрытие процесса и
  перезагрузку;
- pause установки проходит через подтверждаемое `pausing`, abort-ит change и
  только после ответа snapd становится `paused`; resume создаёт новый change;
- исчезновение сети переводит загрузку в `waiting_for_network`, а возвращение
  сети автоматически продолжает её; ручная pause никогда не снимается системой;
- startup reconciliation переиспользует сохранённый change ID, retries имеют
  backoff, а watchdog останавливает зависшую загрузку перед новой попыткой;
- удаление по умолчанию сохраняет автоматический snapshot-бэкап, а опция
  `--no-backup` передаёт snapd явный JSON-флаг `purge`;
- snapshots синхронизируются из snapd и публикуют оставшееся время в секундах и
  днях; восстановление сначала создаёт долговечную задачу повторной установки,
  а после её завершения запускает `snap restore` — данные не восстанавливаются
  в отсутствующее приложение;
- отдельный GNOME-адаптер проверяет реальное состояние приложения, мягко
  закрывает его и закрепляет/открепляет в `favorite-apps`.

Ожидаемый срок автоматического бэкапа считается от стандартных 31 дней snapd и
помечается `expiry_estimated=true`, поскольку системная настройка retention может
быть изменена. Ручным snapshots выдуманный срок не назначается. Чтение реального
`snapshots.automatic.retention` оставлено Linux-интеграционному этапу.

## Контракт карточки приложения

- установленное, но не работающее приложение предлагает `launch`;
- принятый запрос запуска показывает «Запускается»;
- только подтверждённые GNOME окна или процессы дают «Запущено» и действие
  `close`;
- если запуск не подтверждён за 60 секунд, возвращаются «Не удалось запустить» и
  только `retry`;
- после `close` состояние снова проверяется — закрытие не считается успешным
  заранее.

Действий `open` и `details` в контракте нет. Закрытие использует
`Shell.App.request_quit()`, без принудительного `kill`.

## Параметры будущих обновлений

`Application` публикует `supported_locales`, `supported_install_locations` и
`install_options`, а выбор сохраняется в `InstallPreferences`. Сейчас честно
поддержаны только системный язык, стандартное расположение и две пользовательские
post-install галочки: `pin_to_gnome` и `launch_after_install`. Внутренняя опция
`restore_from_backup` доступна только координатору восстановления. Неподдерживаемый
язык, путь или checkbox отклоняется до обращения к snapd.

Ярлык на рабочем столе пока не объявлен поддерживаемым: в GNOME это зависит от
расширения Desktop Icons и политики доверия `.desktop`. APT, Flatpak, PPA,
произвольные `.deb` и дополнительные installation roots также остаются за
границей текущей версии.

## Автономная консольная проверка в Ubuntu

```bash
PYTHONPATH=modules/software-manager/src python -m ai_native_software catalog
PYTHONPATH=modules/software-manager/src python -m ai_native_software prepare-install steam --option pin_to_gnome
PYTHONPATH=modules/software-manager/src python -m ai_native_software confirm TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software refresh TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software pause TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software resume TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software cancel TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software reconcile

PYTHONPATH=modules/software-manager/src python -m ai_native_software prepare-remove steam
PYTHONPATH=modules/software-manager/src python -m ai_native_software confirm TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software confirm-remove TASK_ID

PYTHONPATH=modules/software-manager/src python -m ai_native_software sync-backups --package-name steam
PYTHONPATH=modules/software-manager/src python -m ai_native_software backups
PYTHONPATH=modules/software-manager/src python -m ai_native_software ack-notification TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software dispatch-notifications
PYTHONPATH=modules/software-manager/src python -m ai_native_software prepare-restore snap:35:steam
PYTHONPATH=modules/software-manager/src python -m ai_native_software confirm-restore snap:35:steam
PYTHONPATH=modules/software-manager/src python -m ai_native_software refresh-restore snap:35:steam
```

Для системных изменений клиент разрешает штатную авторизацию snapd через Polkit.
Реальные операции установки, GNOME Shell и Polkit должны пройти отдельные
integration tests на Ubuntu до подключения изменяющих runtime-команд.
