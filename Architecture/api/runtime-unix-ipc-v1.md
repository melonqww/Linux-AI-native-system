# Runtime Unix IPC v1

## Назначение

Production transport панели в Linux — Unix domain socket
`$XDG_RUNTIME_DIR/ai-native-linux/runtime.sock`. Это локальный filesystem endpoint,
не TCP-порт. Runtime создаёт отдельный каталог с mode `0700`, socket с `0600` и
при каждом соединении получает от ядра Linux `PID/UID/GID` через `SO_PEERCRED`.
Соединение принимается только от текущих реальных UID и GID runtime.

Runtime отказывается:

- работать с socket-каталогом другого владельца или доступным группе/остальным;
- заменять обычный файл, symlink или чужой socket;
- удалять активный socket другого экземпляра;
- принимать сообщение больше 64 КиБ или неполную строку;
- принимать неизвестные поля, версию, method или некорректный UUID.

Owned stale socket удаляется только после неуспешной connect-пробы и повторной
проверки inode. При штатной остановке runtime удаляет только тот inode socket,
который создал сам.

## Wire format

Одно соединение обслуживает один JSON request и один JSON response, каждый
завершается `\n`.

```json
{
  "version": 1,
  "request_id": "6f02d873-30af-4e92-a39a-e45953902c6b",
  "method": "POST",
  "path": "/v1/approval/respond",
  "body": {"approval_request_id": "...", "confirmed": true}
}
```

```json
{
  "version": 1,
  "request_id": "6f02d873-30af-4e92-a39a-e45953902c6b",
  "status": 200,
  "body": {"state": "completed"}
}
```

Маршруты и payload совпадают с runtime API; маршрутизация общая для транспортов.
R1 confirmation, Task Ledger detail с локальными путями, cancel и continue
разрешены только в authenticated Unix transport. Loopback HTTP является R0/dev
fallback и отвечает `403 secure_transport_required` на эти маршруты.

## Граница защиты

`SO_PEERCRED` нельзя подделать из userspace: PID/UID/GID предоставляет ядро. Это
закрывает доступ другим Linux-пользователям и процессам без доступа к runtime
namespace/socket. Но все обычные процессы одного пользователя имеют тот же UID и
GID. Поэтому peer credentials не доказывают, что запрос создала именно GNOME
панель. Следующее усиление — запуск панели/runtime как связанной systemd user
сессии и дополнительная привязка клиента (inherited descriptor или session
secret); она не заменяет, а дополняет `SO_PEERCRED`.
