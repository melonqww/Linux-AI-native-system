# provider.ollama

First-party модуль установки Ollama без административных прав. Он обнаруживает
системный `ollama` либо устанавливает проверенный официальный Linux release в
`${XDG_DATA_HOME}/ai-native-linux/providers/ollama`.

Provider также является единственным владельцем процесса для своей user-local
копии. Системный Ollama принимается только когда его loopback API отвечает;
наличие бинарника без работающего service возвращает публичную ошибку и не
приводит к запуску второго daemon с другим model store.

Модуль не запускает `sudo`, `pkexec`, shell и официальный `install.sh`.
Release metadata читается из GitHub API, архив принимается только с официальных
GitHub download-hosts и только при наличии SHA-256 digest. Содержимое безопасно
распаковывается в отдельный staging-каталог; абсолютные пути, `..`, devices и
выходящие наружу ссылки отклоняются.

Поддерживаются Linux x86_64 и ARM64. Решения `install`, `later`, `never`
сохраняются отдельно от ядра; `later` действует 24 часа. Контракт панели:
[`Architecture/api/ollama-provider-v1.md`](../../Architecture/api/ollama-provider-v1.md).
