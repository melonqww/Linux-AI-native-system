# История AI Scenario Lab и доказательства прогонов

Этот документ — долговечный журнал развития лаборатории и проверяемого
фундамента AI-native Linux System. Он отвечает не только на вопрос «сколько
тестов прошло», но и показывает:

- какой именно backend и профиль проверялись;
- какой была лаборатория на момент запуска;
- какие проблемы она обнаружила;
- какие архитектурные решения появились вслед за наблюдениями;
- что действительно подтверждено повторным прогоном, а что только исправлено в
  коде и ещё ожидает проверки.

Это не release badge и не подборка только успешных запусков. Заблокированные,
ошибочные и неполные прогоны остаются в истории. Процент прохождения разных
матриц нельзя сравнивать без учёта добавленных сценариев и изменения исходников.

Агрегаты и безопасные результаты отдельных случаев доступны не только в
таблицах этого документа, но и в машиночитаемом виде:
[`public-evidence/index.json`](public-evidence/index.json). Правила очистки и
границы публикации описаны в
[`public-evidence/README.md`](public-evidence/README.md).

## Как читать журнал

| Поле | Значение |
|---|---|
| Run ID | UTC-идентификатор неизменяемой папки запуска. |
| Source fingerprint | SHA-256 состояния проверяемых исходников, записанный manifest. Это точнее даты файла и не выдаётся за Git commit. |
| Матрица | Полное количество gates, contract-сценариев и journey-вариантов с обоими seeds. |
| Passed / failed / error / not run | Фактический исход, без автоматического retry до зелёного результата. |
| Wall time | Разница между `launch.json.launched_unix` и последним heartbeat. Для аварийного запуска это время до остановки, а не benchmark. |
| Verdict | `problems_found` означает, что лаборатория отработала и нашла проблемы; `incomplete` означает, что доказательства полного прогона нет. |
| Evidence status | `observed` — факт из сохранённого отчёта; `implemented` — изменение внесено, но ещё не перепроверено; `verified` — изменение подтверждено новым сопоставимым прогоном. |

Сырые отчёты находятся в `labs/ai-scenario-lab/reports/` и исключены из Git:
они могут содержать model prose, виртуальные пути и большие traces. В этот
журнал переносятся только агрегаты, безопасные идентификаторы и проверяемые
выводы. Полный отчёт остаётся локальным источником доказательств.

## До Foundation: первые contract-прогоны

Первая версия лаборатории ещё не имела автономного supervisor, source snapshot,
двух seeds и единого Foundation verdict. Каждый запуск писал `summary.json` и
`summary.md`, а целевые перезапуски использовались для исследования конкретного
сценария. Эти результаты нельзя напрямую сравнивать с Foundation, но удалять их
из истории тоже неправильно.

| Дата UTC | Run ID | Объём | Результат | Что показывает этот запуск |
|---|---|---:|---:|---|
| 2026-09-01 | `20260901T083800.134838Z` | 5 contract-сценариев | `1 passed / 4 failed` | Самая ранняя сохранённая точка: разговор работал, а search, mixed и обе copy-ветки ещё выявляли проблемы. |
| 2026-09-01 | `20260901T085725.103871Z` | 5 contract-сценариев | `4 passed / 1 failed` | Search и approve/deny уже проходили; mixed bread + PDF оставался отдельной проблемой. |
| 2026-09-01 | `20260901T090126.625962Z` | 1 целевой сценарий | `0 passed / 1 failed` | Изолированный повтор подтвердил, что mixed-сбой воспроизводится отдельно от всей матрицы. |
| 2026-09-01 | `20260901T090540.703777Z` | 1 целевой сценарий | `1 passed / 0 failed` | Первый сохранённый зелёный mixed-прогон; ещё не доказательство стабильности серии. |
| 2026-09-01 | `20260901T090623.348733Z` | 5 contract-сценариев | `2 passed / 3 failed` | Повтор всей пятёрки показал нестабильность search/copy, которую один зелёный targeted run скрыть не смог. |
| 2026-09-01 | `20260901T090946.202088Z` | 5 contract-сценариев | `5 passed / 0 failed` | Первый полностью зелёный базовый набор. |
| 2026-09-01 | `20260901T091234.794673Z` | 7 contract-сценариев | `7 passed / 0 failed` | Матрица расширена timeout и broken/large files без потери базового результата. |
| 2026-09-01 | `20260901T165729.386530Z` | 13 contract-сценариев | `11 passed / 2 failed` | После добавления negative, prompt-injection, memory и controlled faults лаборатория снова нашла проблемы; расширение покрытия честно снизило итог. |

Главный результат первого дня — не строка `7/7`, а последовательность
`1/5 → 4/5 → targeted failure → targeted pass → 2/5 → 5/5 → 7/7 → 11/13`.
Она показывает, почему один удачный вызов модели не может считаться готовностью
фундамента.

## До Foundation: развитие journey campaigns

Goal-driven campaign добавил реальные многошаговые действия и пользовательские
вариации. В отличие от отдельных contract runs, campaign сохранял каждую
попытку, multidimensional coverage, failure groups и traces.

| Дата UTC | Campaign ID | Attempts | Passed / failed | Wall time | Роль запуска |
|---|---|---:|---:|---:|---|
| 2026-09-02 | `20260902T104744.050428Z` | 6 | `6 / 0` | 4 мин 09 с | Первый короткий зелёный campaign; ограниченный объём, не release evidence. |
| 2026-09-02 | `20260902T132734.616554Z` | 21 | `18 / 3` | 14 мин 35 с | Расширение персон и поведения впервые выявило journey failures. |
| 2026-09-02 | `20260902T141159.409703Z` | 19 | `13 / 6` | 20 мин 27 с | Более строгая многошаговая матрица увеличила число наблюдаемых проблем. |
| 2026-09-02 | `20260902T143939.715654Z` | 19 | `14 / 5` | 18 мин 11 с | Частичное улучшение без сокрытия оставшихся пяти failures. |
| 2026-09-02 | `20260902T150110.092752Z` | 19 | `19 / 0` | 19 мин 36 с | Зелёная версия тогдашней campaign-матрицы; позже Foundation расширил её ещё сильнее. |
| 2026-09-04 | `20260904T092042.528725Z` | 28 | `20 / 8` | 32 мин 27 с | После расширения модульных контрактов новая матрица снова обнаружила восемь проблем. |
| 2026-09-11 | `20260911T063120.548905Z` | 1 | `1 / 0` | 1 мин 34 с | Целевой regression после изменения search intent. |
| 2026-09-11 | `20260911T063302.263197Z` | 1 | `1 / 0` | 45 с | Второй изолированный regression; зелёный targeted run не заменяет Foundation. |
| 2026-09-16 | `20260916T094802.784091Z` | 1 | `1 / 0` | 1 мин 57 с | Целевая проверка после изменений semantic routing. |
| 2026-09-16 | `20260916T151723.628818Z` | 4 | `4 / 0` | 6 мин 28 с | Короткая повторная серия исправленных цепочек перед следующим полным прогоном. |

Targeted campaign нужен для быстрого подтверждения гипотезы, но журнал всегда
помечает его объём. `1/1` и `4/4` не сравниваются с полным Foundation и не
используются как самостоятельное утверждение о готовности продукта.

## Сводная хронология Foundation

| Дата UTC | Run ID | Source fingerprint | Профиль лаборатории | Среда | Результат | Доля passed | Wall time | Главный наблюдаемый результат |
|---|---|---|---|---|---|---:|---:|---|
| 2026-09-03 | `20260903T113427.287427Z` | `08e345ce2cf0…` | Foundation v1, 87 позиций, 42 уникальных live-subjects, seeds 7/19 | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `1 passed / 1 failed / 85 not run`, `incomplete` | не применяется | 48 с | Быстрый gate `lab-tests` остановил live-часть. Лаборатория не превратила поломанную предпосылку в ложный AI-результат. |
| 2026-09-03 | `20260903T113742.684991Z` | `e56d73943438…` | Та же матрица: contract + adaptive journeys, RU/EN/mixed, память, отрицания, approve/deny, fault cases | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `56 passed / 31 failed`, `problems_found` | 64.4% | 2 ч 14 мин 57 с | Первый завершённый baseline: 31 проблема вместо преждевременного объявления готовности; отдельно видны MODEL, ROUTER и ещё неклассифицированные сбои. |
| 2026-09-03 | `20260903T142744.382953Z` | `658f9ba5d69e…` | 91 позиция; добавлены граница conversation/action и уточнение destination | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `1 passed / 1 failed / 89 not run`, `incomplete` | не применяется | 1 мин 24 с | Расширенная матрица снова была остановлена prerequisite-gate. История сохраняет не только удачные попытки. |
| 2026-09-03 | `20260903T143141.483306Z` | `6e18410c247d…` | 91 позиция, 44 уникальных live-subjects | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `63 passed / 28 failed`, `problems_found` | 69.2% | 1 ч 57 мин 18 с | Новые boundary-сценарии вошли в полный проход; количество проблем сократилось относительно первого baseline, но release-gate остался закрыт. |
| 2026-09-09 | `20260909T160441.419929Z` | `9669270c9a04…` | 91 позиция; тот же source fingerprint, что у следующего запуска | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `21 passed / 28 failed / 42 error`, `problems_found` | 23.1% passed | 58 мин 51 с | 42 случая отделены как инфраструктурные ошибки, а не смешаны с неверным поведением модели и не засчитаны как pass. |
| 2026-09-10 | `20260910T073608.294590Z` | `9669270c9a04…` | Та же матрица и тот же fingerprint | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `39 passed / 52 failed`, `problems_found` | 42.9% | 1 ч 49 мин 41 с | Повтор без изменения source устранил класс `error`, но не сделал продукт зелёным. Пара запусков доказала влияние runtime-состояния и необходимость раздельно учитывать infrastructure и behavioral failures. |
| 2026-09-14 | `20260914T111835.647947Z` | `7ba2da19fbfc…` | 95 позиций, 46 live-subjects; добавлены semantic-topic и exact-phrase E2E oracle | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `63 passed / 32 failed`, `problems_found` | 66.3% | 2 ч 00 мин 44 с | Лаборатория стала строже: одинаковое число найденных файлов больше не доказывает правильный смысл поиска; появились отдельные COMPILER и EXECUTOR группы. |
| 2026-09-16 | `20260916T073344.244866Z` | `04c78469d62f…` | 95 позиций, два seeds, 18 committed + 7 generated contract-сценариев и 21 journey-case | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `81 passed / 14 failed`, `problems_found` | 85.3% | 2 ч 04 мин 53 с | На тот момент лучший сохранённый полный прогон: gates и 49/50 scenario attempts прошли, но 13 journey и один memory contract ещё блокировали кандидат. Один ход превысил 90 секунд. |
| 2026-09-21 | `20260921T082636.506678Z` | `bdd8ed5a5e59…` | 97 позиций, 47 live-subjects; новый четырёхходовой repeat mixed regression и typed failure evidence | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `91 passed / 6 failed`, `problems_found` | 93.8% | 2 ч 14 мин 44 с | Все позиции выполнены без infrastructure errors и automatic retry. Одиннадцать прежних failures исчезли; лаборатория воспроизвела устойчивые router-сбои в cautious/verbose формулировках и оба повтора нового mixed-сценария. |
| 2026-09-23 | `20260923T084919.842465Z` | `1ff4db1cbf09…` | 113 позиций; добавлены File Operations R1, approve/deny и реальные файловые эффекты | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `93 passed / 20 failed`, `problems_found` | 82.3% | 2 ч 49 мин | Более строгая матрица обнаружила неверный поиск, лишние операции, потерю продолжения и ответы без подтверждённых файловых фактов. Снижение доли относительно 91/97 не является регрессией сопоставимой матрицы. |
| 2026-09-24 | `20260924T091434.648761Z` | `aae7f172fb4a…` | Адресный профиль: 11 ранее упавших типов на обоих seeds + 3 gates | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `22 passed / 3 failed`, `problems_found` | 88.0% | 28 мин 50 с | Перепроверка показала, что 17 из прежних 20 провалов устранены, но перенос файла на обоих seeds и один длинный английский запрос ещё не проходили. Не является полным Foundation. |
| 2026-09-25 | `20260925T080528.342605Z` | `6b30b8bc7d6e…` | Адресный профиль: два оставшихся типа на обоих seeds + 3 gates | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `7 passed / 0 failed`, `backend_candidate` в адресном профиле | 100% адресного профиля | 5 мин 38 с | Подтверждены перенос после поиска и длинная английская цепочка с уточнением назначения и отказом. Зелёный targeted run не заменяет полный. |
| 2026-09-25 | `20260925T081344.764208Z` | `6b30b8bc7d6e…` | Полный Foundation v1: 113 позиций, 55 live-cases × seeds 7/19 + 3 gates | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `113 passed / 0 failed / 0 error`, `backend_candidate` | 100% проверенной матрицы | 2 ч 48 мин 25 с | Первый зелёный полный прогон расширенной матрицы: 68 scenario attempts, 42 journeys и 3 gates; нет unknown failures, slow cases или automatic retries. Это доказательство backend-кандидата в виртуальном ПК, не Linux release. |

### Почему строки нельзя свести к одному графику pass-rate

Между запусками менялись исходники и сама строгость oracle. Переход с 63/91 на
63/95 не означает отсутствие развития: в матрицу вошли новые semantic/exact
проверки. Сравнение 21/91 и 39/91 особенно полезно именно потому, что source
fingerprint одинаков: оно показывает влияние внешнего runtime-состояния. Для
честного before/after отчёта сравниваются одновременно:

1. пересечение одинаковых case ID;
2. новые case ID отдельно;
3. source fingerprint, model tag/digest и platform;
4. failure fingerprints, а не только итоговый процент;
5. длительность по ходам и число infrastructure errors.

## Как лаборатория развивалась

| Дата | Стадия лаборатории | Что появилось | Связанные решения | Что это изменило в доказательствах |
|---|---|---|---|---|
| 2026-09-01 | Изолированная AI Scenario Lab | Виртуальный ПК внутри репозитория, настоящий production pipeline, Ollama/Qwen, containment canary и сохраняемые traces | [ADR-017](../../Architecture/decisions/ADR-017-isolated-ai-scenario-lab.md) | Появилась возможность проверять модель и выполнение без риска для пользовательских файлов. |
| 2026-09-02 | Goal-driven journeys | Персоны, опечатки, сленг, отсутствие пунктуации, approve/deny, rephrase/correct и многошаговые цели | [ADR-018](../../Architecture/decisions/ADR-018-goal-driven-user-journey-lab.md), [ADR-019](../../Architecture/decisions/ADR-019-advisory-capability-routing.md) | Проверка перестала быть набором заранее известных идеальных фраз. |
| 2026-09-03 | Автономный Foundation | `prepare/start/status`, snapshots, два seeds, process isolation, timeout, atomic reports и запрет automatic retry | [ADR-020](../../Architecture/decisions/ADR-020-autonomous-foundation-validation.md), [ADR-021](../../Architecture/decisions/ADR-021-grounded-actions-and-input-facts.md) | Долгий прогон больше не требует присутствия разработчика; неполный запуск невозможно принять за зелёный. |
| 2026-09-04–05 | Модули как источник истины | Self-describing capability manifests, runtime operation catalog, точные search constraints и grounded destination clarification | [ADR-022](../../Architecture/decisions/ADR-022-self-describing-capability-contracts.md), [ADR-023](../../Architecture/decisions/ADR-023-grounded-search-constraints-and-exact-oracles.md), [ADR-024](../../Architecture/decisions/ADR-024-runtime-operation-catalog.md) | Лаборатория использует те же capability-контракты, что production, и проверяет аргументы плана и реальные пути. |
| 2026-09-09 | Semantic capability selector | Ограниченный multilingual selector поверх Registry, lexical fallback и максимум кандидатов | [ADR-025](../../Architecture/decisions/ADR-025-bounded-semantic-capability-selector.md) | Опечатки и естественные формулировки проверяются без словаря пользовательских фраз в ядре. |
| 2026-09-11–13 | Смысл document search | Разделены semantic topic и exact phrase; добавлены парные E2E oracle | [ADR-030](../../Architecture/decisions/ADR-030-search-match-intent-contract.md), [ADR-026: grounded retrieval](../../Architecture/decisions/ADR-026-grounded-retrieval-and-complete-operation-graphs.md) | Семантически похожий файл больше не считается доказательством наличия точной фразы. |
| 2026-09-14–16 | Lossless arguments и память уточнений | Тип файла отделён от темы, module-owned conditional arguments сохраняются, intent drafts переживают clarification, destination typo обрабатывается безопасно | [ADR-029](../../Architecture/decisions/ADR-029-lossless-operation-arguments.md) | Green result требует корректного назначения, имени папки, выбранных файлов и полного графа операции. |
| 2026-09-18–21 | Диагностика и реальный mixed regression | Безопасная классификация MODEL/ROUTER/COMPILER/POLICY/EXECUTOR/INDEX/CONTAINMENT; новый четырёхходовой повторный PDF + bread сценарий | [ADR-018](../../Architecture/decisions/ADR-018-goal-driven-user-journey-lab.md), [ADR-020](../../Architecture/decisions/ADR-020-autonomous-foundation-validation.md) | Полный run `91/97` подтвердил диагностику без UNKNOWN и воспроизвёл потерю повторного mixed search на обоих seeds. |
| 2026-09-22–25 | File Operations R1 и повтор по прежним провалам | Новые файловые сценарии, проверка плана до approval, read-only inspect, классификация неверных аргументов и адресный профиль по failed subjects на обоих seeds | [ADR-039](../../Architecture/decisions/ADR-039-file-operations-r1-contract.md), [ADR-030](../../Architecture/decisions/ADR-030-search-match-intent-contract.md) | Расширение выявило 20 проблем на 113 позициях; адресные прогоны сузили их до 3 и затем до 0; полный `113/113` отдельно подтвердил отсутствие видимых регрессий в этой матрице. |

## Что лаборатория уже принесла проекту

| Наблюдение | Как оно проявилось | Реакция проекта | Текущий evidence status |
|---|---|---|---|
| Prerequisite может сломаться до модели | Два запуска остановились после `lab-tests_failed`, оставив 85/89 случаев `not_run` | Foundation различает `blocked/incomplete` и `problems_found`; live-часть не стартует после провала обязательного gate | **verified** сохранёнными blocked runs |
| Среда исполнения влияет на результат | Два запуска с fingerprint `9669270c9a04…` дали 42 infrastructure errors, а затем 0 errors | Infrastructure failure хранится отдельно от model/router/compiler failure; повтор не переписывает прошлый результат | **observed**, причина каждого error остаётся в локальных logs |
| Идеальная фраза не покрывает пользователя | Journey failures концентрировались на typo, cautious, impatient, verbose и no-punctuation вариантах | Добавлены персональные мутации, semantic selector и многошаговые цели | **verified в текущей матрице**: полный 113/113 прошёл все 42 journey attempts; произвольные формулировки вне матрицы не доказаны |
| Правильное число файлов может скрывать неправильный смысл | Тематический и точный поиск раньше могли смешиваться | Добавлены разные intent-контракты и точные result paths для semantic/exact пары | **verified** в последнем сохранённом run для обоих seeds |
| Безопасность нельзя усреднять | Deny, timeout, prompt injection и executor failure могут потеряться в общем pass-rate | Safety-теги требуют 100%, containment имеет приоритет, forbidden effects проверяются отдельно | **verified** в сохранённых traces, но не заменяет Linux smoke |
| Ответ модели может противоречить системе | Ручной диалог показал `0 найдено / 1 недоступен`, после чего Qwen заявил об отсутствии доступа и потерял повторный mixed search | Добавлен scenario `repeat-mixed-search-grounding`, метрика `inaccessible`, запрет ложных access claims и performance bound | **verified fix в текущей матрице**: этот сценарий и весь полный 113/113 прошли на обоих seeds |
| UNKNOWN затрудняет исправление | В run 2026-09-16 семь failures остались без надёжного владельца | Evidence extractor теперь использует typed checks, compiler diagnostics, model events и executor error codes; prose и пути исключены | **verified**: run 2026-09-23 разложил 20 failures без UNKNOWN, а текущий полный run не содержит failures |

## Последний подтверждённый полный прогон

Run `20260925T081344.764208Z` — подтверждённая точка для проверенного
backend-кандидата. Это не утверждение о готовности Linux UI или всех возможных
пользовательских формулировок:

| Измерение | Наблюдение |
|---|---|
| Итог | `backend_candidate`, 113 passed / 0 failed / 0 error / 0 not run |
| Объём | 2 prerequisite gates + model preflight, 68 scenario attempts и 42 journey attempts; seeds 7 и 19, automatic retries = 0 |
| Модель и среда | Qwen `qwen3.5:2b` с 8192 context tokens, Ollama 0.21.0, Windows, Python 3.13.14; проверка внутри изолированного виртуального ПК |
| Языки | EN 36/36, RU 72/72, mixed 2/2; три gates без языковой метки |
| Поведение | Все представленные standard, cautious, verbose, typo, slang, no-punctuation, impatient, negative и prompt-injection варианты прошли |
| Memory | Сценарии недавней памяти на глубине 5 и 35 прошли; это не доказательство неограниченной памяти |
| Safety | В матрице прошли deny, timeout, prompt-injection, controlled faults и проверки реальных файловых эффектов; containment failures нет |
| Диагностика | `failures = 0`, `UNKNOWN = 0`, slow cases = 0; ни один неуспешный повтор не скрыт retry |
| Performance | Полный wall time 2 ч 48 мин 25 с; ни один ход не отмечен как превышающий лабораторный лимит 90 секунд. Это не UX benchmark |
| Известные границы | Нет end-to-end подтверждения GNOME/systemd/Unix IPC/реальных ACL, восстановления после отключения питания, извлечения диска и понимания реальных изображений |

Публичный очищенный [JSON этого прогона](public-evidence/foundation/20260925T081344.764208Z.json)
содержит 113 статусов, digest модели и SHA-256 четырёх первичных JSON. Сырые traces остаются
локально в `reports/foundation/20260925T081344.764208Z/` и в Git не входят.
Source fingerprint относится к состоянию кода **на момент запуска**, а не к
последующему коммиту документации и публичного экспортёра.

## Что остаётся проверить после зелёного Foundation

Полный прогон подтвердил только заявленную backend-матрицу. Перед решением о
Linux beta нужны проверки реальной установки и обновления GNOME-панели,
systemd-сервиса, Unix transport/прав, поведения на реальных томах и при
неожиданном отключении. После изменения runtime-кода или матрицы потребуется
новый прогон; `113/113` не переносится автоматически на будущую версию.

После явной команды запуска сюда добавляется новая строка, даже если запуск:

- заблокирован prerequisite;
- остановлен пользователем;
- завершён с infrastructure errors;
- ухудшил результат;
- выявил новый неизвестный класс проблемы.

## Шаблон записи нового запуска

```markdown
### <run-id>

| Поле | Значение |
|---|---|
| Commit / source fingerprint | ... |
| Profile / model / digest / context | ... |
| Platform / Python / Ollama | ... |
| Matrix | gates ..., contracts ..., journeys ..., seeds ... |
| State / verdict | ... |
| Counts | passed ..., failed ..., error ..., not_run ... |
| Wall time / slow turns | ... |
| Failure layers / new fingerprints | ... |
| Regressions fixed | verified / still failing / not comparable |
| New problems | ... |
| Known gaps | ... |
| Raw local report | reports/foundation/<run-id>/ |
```

## Правила честности

1. Незавершённый запуск никогда не сравнивается как успешный полный run.
2. Retry не удаляет и не заменяет предыдущую строку.
3. Изменившаяся матрица показывается явно; новые тесты не считаются регрессией
   старого кода и не скрываются из denominator.
4. Исправление кода имеет статус `implemented`, пока сопоставимый тест не дал
   `verified`.
5. Windows Foundation не выдаётся за Ubuntu/GNOME release validation.
6. Сырые prompts, абсолютные пользовательские пути и model prose не переносятся
   в версионируемый документ.
7. Лучший результат не выбирается вместо последнего: история содержит все
   существенные полные, blocked и infrastructure-sensitive запуски.
