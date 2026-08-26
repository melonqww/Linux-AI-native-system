# software.manager

Автономный модуль установки и удаления Linux-приложений с долговечными задачами,
прогрессом, остановкой и продолжением после перезапуска процесса. Первая версия
работает со штатным Unix API `snapd` и не запускает `sudo` или shell.

Модуль пока намеренно не содержит `module.json` и не обнаруживается Capability
Registry. Подключение к core будет отдельным этапом после проверки runtime в Ubuntu.

## Границы v0.1

- встроенный проверенный каталог из 20 популярных Snap-приложений;
- подготовка установки или удаления всегда создаёт задачу в состоянии
  `awaiting_confirmation`;
- после подтверждения `snapd` выполняет долговечный asynchronous change;
- состояние задачи и внешний change ID сохраняются в SQLite;
- прогресс читается из task progress `snapd`, а неизвестный процент остаётся `null`;
- pause установки abort-ит текущий change, resume создаёт новый change;
- uninstall нельзя приостанавливать;
- APT, Flatpak, PPA и произвольные `.deb` не входят в первую версию.

## Задел для следующих версий

Контракт приложения уже публикует `supported_locales`,
`supported_install_locations` и `install_options`. Выбор пользователя хранится в
задаче как `InstallPreferences`, поэтому будущий backend сможет предложить язык,
другой поддерживаемый installation root и отдельные checkbox-компоненты без
изменения формата задачи. Сейчас каталог честно объявляет только системный язык,
стандартное расположение и отсутствие дополнительных опций; неподдерживаемые
значения отклоняются до обращения к snapd.

Фактические операции изменения системы требуют штатной авторизации snapd/Polkit.
Пароли модуль не принимает и не передаёт.

## Автономная консольная проверка в Ubuntu

```bash
PYTHONPATH=modules/software-manager/src python -m ai_native_software catalog
PYTHONPATH=modules/software-manager/src python -m ai_native_software prepare-install steam
PYTHONPATH=modules/software-manager/src python -m ai_native_software confirm TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software refresh TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software pause TASK_ID
PYTHONPATH=modules/software-manager/src python -m ai_native_software resume TASK_ID
```

Подготовка и подтверждение разделены намеренно: команда установки не изменяет
систему до отдельного `confirm`. Для запроса административного разрешения клиент
использует штатное взаимодействие snapd с Polkit, а не пароль или `sudo`.
