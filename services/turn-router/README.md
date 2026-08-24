# Turn Router

Model-neutral граница Workspace. Основной `CapabilityCandidateRouter` сравнивает
запрос с декларативными descriptions/examples только включённых модулей. Он
возвращает не план, а максимум три допустимых кандидата. Если кандидатов нет,
Workspace вызывает Qwen без tools; если есть — Qwen видит только их операции.

Старый model-classifier остаётся совместимым fallback-контрактом. Ни один router
не строит план и ничего не исполняет: это делают Intent Compiler, Permission
Gateway и Orchestrator.
