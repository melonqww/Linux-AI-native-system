# system.monitor

First-party read-only модуль системного мониторинга для левой вкладки панели.
Работает отдельным Python worker-процессом без root и внешних зависимостей.

Snapshot v1 включает CPU cores/threads, load average, uptime, RAM/swap,
доступные без root EDAC DIMM/channel сведения, thermal zones, батарею,
block-backed пользовательские тома и до 50 процессов. Процессы сортируются на
стороне worker по CPU или памяти, по возрастанию либо убыванию. Чтение `/proc`
и `/sys` ограничено, частичные ошибки превращаются в стабильные warning-коды.

Управление процессами намеренно не входит в v1: завершение процесса потребует
отдельной capability, Permission Gateway policy, защищённого Unix IPC и явного
подтверждения изменяющей операции.
