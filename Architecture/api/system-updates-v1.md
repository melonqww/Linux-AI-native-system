# System Updates API v1

## Назначение

`system.updates` — on-demand first-party модуль для read-only проверки
обновлений Ubuntu. Панель вызывает:

```http
POST /v1/system-updates/check
{}
```

Ответ содержит `state` (`updates_available`, `up_to_date`, `unavailable`),
`available_count`, `security_count`, не более 50 имён пакетов, возраст APT-кэша
и признак `cache_stale`.

## Безопасность и точность

Worker запускает `/usr/bin/apt-get` или `/bin/apt-get` без shell только с
`--simulate`, `Debug::NoLocking=1`, фиксированным окружением и timeout 20 секунд.
Он не выполняет `apt-get update`, не устанавливает пакеты, не получает root и не
удерживает APT/dpkg locks. stdout/stderr ограничены 1 MiB, а наружу не попадают
диагностические сообщения APT.

Проверка использует локальные package lists. При кэше старше 24 часов ответ
помечается `cache_stale`; панель не заявляет, что система актуальна, а открывает
штатный `update-manager` либо GNOME Software. Обновление кэша и установка идут
через стандартный Ubuntu/PolicyKit UI, не через привилегии панели.
