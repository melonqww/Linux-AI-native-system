# Process Module Manager

Запускает enabled-модули в отдельных Python-процессах, сначала поднимает их
обязательные зависимости, проверяет JSON health-протокол и выгружает on-demand
процессы после idle timeout. Shell-строки из manifests не выполняются.

Текущий worker является lifecycle-host: он проверяет импорт entrypoint,
обслуживает `health`/`shutdown` и закрытый bounded `invoke` для first-party
operations. Имя operation валидируется, request/response ограничены 64 KiB,
а exception модуля не возвращается в клиентский JSON.
