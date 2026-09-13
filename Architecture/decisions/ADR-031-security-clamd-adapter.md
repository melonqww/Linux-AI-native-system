# ADR-031: Optional ClamAV clamd adapter через локальный поток

**Статус:** принято и реализовано в `security.center` `0.3.0`

## Контекст

Версия `0.2.0` безопасно открывает один разрешённый файл и проверяет точные
локальные hash- и byte-pattern сигнатуры. Этап `0.3.0` должен был добавить
настоящий локальный антивирусный движок, не передавая ему путь к пользовательскому
файлу и не превращая scanner в процесс с shell, subprocess или сетевыми правами.

По [официальному протоколу ClamD](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)
команда `INSTREAM` передаёт содержимое частями, а daemon возвращает результат
проверки потока. Это позволяет оставить
разрешение `resource_id + relative_path`, безопасное открытие и контроль identity
у нашего scanner. При этом локальный daemon является отдельной границей доверия:
подмена Unix socket, неограниченный ответ или неожиданный формат не должны
создать clean verdict либо попасть в публичный результат.

## Решение

В `0.3.0` вводится optional adapter `ClamdUnixSocketDetector`. Он подключается
только к заранее настроенному `AF_UNIX` socket и использует только bounded
`INSTREAM`. TCP endpoint, hostname, port, URL и socket path из capability payload
не допускаются. Adapter не запускает и не останавливает `clamd`.

Scanner по-прежнему сам разрешает trusted resource reference, открывает обычный
файл без следования по symlink, фиксирует identity и читает descriptor потоком.
Те же уже прочитанные блоки участвуют в локальных detectors и передаются clamd
кадрами `INSTREAM`; clamd никогда не получает абсолютный или относительный путь.

После соединения и до передачи первого блока adapter обязан получить peer
credentials через Linux `SO_PEERCRED`. Peer UID должен входить в обязательный
trusted allowlist. Невозможность получить credentials, UID вне allowlist или
платформа без этой проверки закрывают adapter с нормализованной ошибкой. Проверки
имени socket-файла, владельца и mode могут усиливать защиту, но не заменяют
проверку peer UID.

Протокол ограничен на каждом участке:

- команда является фиксированной NUL-terminated wire-формой `zINSTREAM\0`, без
  пользовательских фрагментов;
- каждый chunk имеет заданный максимум, 32-bit network-order length и учитывается
  в общем лимите scan;
- нулевой chunk завершает поток ровно один раз;
- connect, write и reply имеют отдельные timeouts и общий scan deadline;
- ответ читается до ограниченного терминатора и общего максимального размера;
- trailing data, несколько ответов, неизвестный status, invalid encoding,
  overflow и premature EOF считаются ошибкой adapter;
- raw reply и raw signature никогда не покидают adapter.

Adapter нормализует допустимый ответ в закрытый результат: `not_found`,
`threat_found` либо стабильный error code. Для `threat_found` наружу передаются
только фиксированный detector ID, bounded нормализованный rule ID,
classification и severity. Имя сигнатуры clamd сначала проверяется по строгой
грамматике и длине; неподходящее значение заменяется стабильным bounded digest-ID.
Ни raw daemon text, ни traceback, socket path, PID, UID или содержимое файла в
публичный `ScanResult` не входят.

Adapter optional в конфигурации поставки:

- если он не configured, обязательное покрытие `0.2.0` остаётся полным и может
  дать `no_threat_detected`;
- если он configured, он становится обязательным detector для данного scan;
- его недоступность, timeout, peer mismatch, protocol error или malformed reply
  дают `status=partial` и `verdict=unknown`, если точного совпадения нет;
- подтверждённая локальная hash/byte-сигнатура сохраняет
  `verdict=malware_detected` даже при `status=partial`, потому что hard evidence
  уже получено; неполное покрытие при этом остаётся явно видимым.

## Инварианты

- Только scanner открывает пользовательский файл; clamd не получает путь.
- Допустим только `AF_UNIX`; TCP и иной удалённый transport запрещены.
- Успешный `SO_PEERCRED` и попадание peer UID в trusted allowlist обязательны до
  отправки содержимого.
- Все команды, chunks, суммарный поток, ответы и времена bounded.
- Raw clamd reply и signature не являются публичным API или policy input.
- Ошибка configured adapter не может дать clean verdict.
- Уже подтверждённая точная локальная сигнатура не понижается из-за сбоя clamd.
- Adapter не использует shell, subprocess, AI или TCP-сеть.

## Отвергнутые варианты

- `clamdscan`/`clamscan` через subprocess: добавляет shell/process boundary,
  наследование окружения и parsing CLI output.
- Команда `SCAN <path>`: раскрывает daemon путь и заставляет повторно разрешать
  filesystem identity вне scanner.
- TCP clamd: расширяет attack surface, допускает ошибочную удалённую конфигурацию
  и усложняет аутентификацию.
- Доверие только правам socket-файла: pathname может быть подменён или вести к
  неправильному peer; identity проверяется на соединении.
- Игнорирование отказа optional adapter: если пользователь его configured, такой
  отказ делает покрытие неполным и не совместим с clean verdict.
- Публикация строки `FOUND`: daemon output недоверен и не должен попадать в UI,
  Task Ledger или модель без строгой нормализации.

## Последствия

`0.3.0` получает более полезное обнаружение известных угроз из базы ClamAV, но
не обещает выявление любого неизвестного malware. Появляется локальная runtime-
зависимость, отдельная причина partial scan и Linux-specific integration tests.
Сам Security Center остаётся отключаемым, read-only при сканировании и не
управляет жизненным циклом daemon.

Публичное расширение результата и протокол adapter описаны в
[`Security Center File Scan API v3`](../api/security-center-file-scan-v3.md).
