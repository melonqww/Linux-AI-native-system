# Process Module Manager

Запускает enabled-модули в отдельных Python-процессах, сначала поднимает их
обязательные зависимости, проверяет JSON health-протокол и выгружает on-demand
процессы после idle timeout. Shell-строки из manifests не выполняются.

Текущий worker является lifecycle-host: он проверяет импорт entrypoint и
обслуживает `health`/`shutdown`. RPC конкретных capabilities добавляется поверх
этого стабильного процесса.
