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
| 2026-09-16 | `20260916T073344.244866Z` | `04c78469d62f…` | 95 позиций, два seeds, 18 committed + 7 generated contract-сценариев и 21 journey-case | Windows, Python 3.13.14, Qwen 3.5 2B, 8k | `81 passed / 14 failed`, `problems_found` | 85.3% | 2 ч 04 мин 53 с | Лучший сохранённый полный прогон: gates и 49/50 scenario attempts прошли, но 13 journey и один memory contract всё ещё заблокировали кандидат. Один ход превысил 90 секунд. |

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
| 2026-09-18 | Диагностика и реальный mixed regression | Безопасная классификация MODEL/ROUTER/COMPILER/POLICY/EXECUTOR/INDEX/CONTAINMENT; новый четырёхходовой повторный PDF + bread сценарий | [ADR-018](../../Architecture/decisions/ADR-018-goal-driven-user-journey-lab.md), [ADR-020](../../Architecture/decisions/ADR-020-autonomous-foundation-validation.md) | Ложное «у меня нет доступа» и смешение `0 найдено` с `1 недоступен` теперь имеют отдельный regression-контракт. Полный прогон новой версии ещё не выполнен. |

## Что лаборатория уже принесла проекту

| Наблюдение | Как оно проявилось | Реакция проекта | Текущий evidence status |
|---|---|---|---|
| Prerequisite может сломаться до модели | Два запуска остановились после `lab-tests_failed`, оставив 85/89 случаев `not_run` | Foundation различает `blocked/incomplete` и `problems_found`; live-часть не стартует после провала обязательного gate | **verified** сохранёнными blocked runs |
| Среда исполнения влияет на результат | Два запуска с fingerprint `9669270c9a04…` дали 42 infrastructure errors, а затем 0 errors | Infrastructure failure хранится отдельно от model/router/compiler failure; повтор не переписывает прошлый результат | **observed**, причина каждого error остаётся в локальных logs |
| Идеальная фраза не покрывает пользователя | Journey failures концентрировались на typo, cautious, impatient, verbose и no-punctuation вариантах | Добавлены персональные мутации, semantic selector и многошаговые цели | **partially verified**; последний run всё ещё содержит 14 проблем |
| Правильное число файлов может скрывать неправильный смысл | Тематический и точный поиск раньше могли смешиваться | Добавлены разные intent-контракты и точные result paths для semantic/exact пары | **verified** в последнем сохранённом run для обоих seeds |
| Безопасность нельзя усреднять | Deny, timeout, prompt injection и executor failure могут потеряться в общем pass-rate | Safety-теги требуют 100%, containment имеет приоритет, forbidden effects проверяются отдельно | **verified** в сохранённых traces, но не заменяет Linux smoke |
| Ответ модели может противоречить системе | Ручной диалог показал `0 найдено / 1 недоступен`, после чего Qwen заявил об отсутствии доступа и потерял повторный mixed search | Добавлен scenario `repeat-mixed-search-grounding`, метрика `inaccessible`, запрет ложных access claims и performance bound | **implemented**, ожидает целевого и полного прогона |
| UNKNOWN затрудняет исправление | В run 2026-09-16 семь failures остались без надёжного владельца | Evidence extractor теперь использует typed checks, compiler diagnostics, model events и executor error codes; prose и пути исключены | **implemented**, ожидает нового отчёта |

## Последний подтверждённый полный прогон

Run `20260916T073344.244866Z` — текущая историческая точка отсчёта, а не
актуальное утверждение о HEAD:

| Измерение | Наблюдение |
|---|---|
| Итог | `problems_found`, 81 passed и 14 failed из 95 |
| Contract/scenario | 49 passed, 1 failed |
| Journeys | 29 passed, 13 failed |
| Languages | mixed: 2/2; EN: 28 passed, 2 failed; RU: 48 passed, 12 failed |
| Поведение | slang и negative прошли; typo, cautious, impatient, verbose и no-punctuation сохранили failures |
| Memory | глубины 5 и 35 прошли; один двухходовой chat-memory contract упал на seed 19 |
| Safety | deny, timeout, prompt-injection и controlled executor/model faults не дали скрытых файловых эффектов в зафиксированных сценариях |
| Performance | один `negative-no-action` turn превысил верхний budget 90 секунд |
| Известные границы | Нет доказательства GNOME/systemd/Unix IPC/реальных ACL, power-loss resume, disk removal и реального image understanding |

Локальный первичный источник:
`reports/foundation/20260916T073344.244866Z/summary.md`. Папка намеренно не
коммитится. После клонирования репозитория эта локальная ссылка отсутствует, но
агрегаты выше сохраняют проверяемый run ID и source fingerprint.

## Следующий запланированный прогон

Текущий HEAD после `e6c07fb` содержит 97 позиций: 19 committed contract-
сценариев, 7 generated foundation-сценариев, 21 journey-case, два seeds и три
обязательных gates. Добавлены:

- `repeat-mixed-search-grounding` из реального пользовательского диалога;
- отдельный oracle `inaccessible`;
- более точная классификация failures;
- безопасные UNKNOWN evidence в `failures.json` и `failures.md`.

Статус: **implemented, not yet run**. До появления нового завершённого отчёта
нельзя утверждать, что новый mixed regression или новая диагностика прошли.

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
