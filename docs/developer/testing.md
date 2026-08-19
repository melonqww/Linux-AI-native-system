# Проверка проекта без локальной Linux-машины

Основной набор запускается одинаково на Windows и Ubuntu:

```powershell
python -m pip install pytest pypdf
python -m pytest -q
python -m compileall -q services modules apps tests
node --check apps/desktop-panel/gnome-extension/extension.js
```

Корневой `conftest.py` автоматически подключает `src` всех monorepo-пакетов.

## Что проверяется

- manifests, уникальность module ID, зависимости и безопасные entrypoint paths;
- импорт и process lifecycle всех first-party modules;
- отсутствие `os.system`, `os.popen` и `subprocess(..., shell=True)` в production-
  Python;
- permissions, path containment, symlinks и исключение чувствительных файлов;
- одноразовое подтверждение и rollback materialize/copy;
- loopback-only runtime bridge и валидация JSON;
- закрытая intent schema, RU/EN планы, доверенный task context и prompt injection;
- битые, слишком большие, многостраничные и требующие OCR PDF;
- coalescing/overflow очереди, load pause и инкрементальные create/modify/delete;
- чистый декодер inotify на каждой ОС;
- настоящий inotify event на Ubuntu runner.

Workflow `.github/workflows/ci.yml` запускает эти проверки на `windows-latest` и
`ubuntu-24.04` после push и в pull request. Linux-only тест локально на Windows
показывается как `skipped`; успешный Ubuntu job является обязательной фактической
проверкой адаптера.

Тесты значительно снижают риск регрессий, но не доказывают абсолютную безопасность.
Перед привилегированными модулями дополнительно потребуются threat model, sandbox-
профили и отдельные security tests.
