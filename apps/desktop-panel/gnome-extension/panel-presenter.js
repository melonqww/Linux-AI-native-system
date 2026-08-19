const ACTIVITY_LABELS = Object.freeze({
    'documents.search': 'Поиск документов',
    'files.copy': 'Копирование файлов',
    'documents.scan': 'Сканирование документов',
    'documents.ocr': 'Распознавание документов',
});

const TASK_STATE_LABELS = Object.freeze({
    planned: 'запланировано',
    running: 'выполняется',
    awaiting_approval: 'ожидает подтверждения',
    interrupted: 'прервано',
    completed: 'завершено',
    completed_with_skips: 'завершено с пропусками',
    failed: 'не выполнено',
    cancelled: 'отменено',
});

const SCHEDULER_LABELS = Object.freeze({
    idle: 'ожидание',
    updating: 'обновление',
    paused_load: 'пауза из-за нагрузки',
    degraded: 'ограниченный режим',
});

const RUNTIME_ERROR_MESSAGES = Object.freeze({
    runtime_unavailable: 'Ядро не запущено или недоступно.',
    runtime_timeout: 'Ядро не ответило вовремя.',
    service_unavailable: 'Нужный компонент ядра сейчас недоступен.',
    intent_compiler_unavailable: 'Понимание естественных запросов сейчас недоступно.',
    invalid_request: 'Запрос не удалось обработать.',
    secure_transport_required: 'Действие требует защищённого соединения.',
    task_action_not_available: 'Это действие сейчас недоступно.',
});

function safeArray(value) {
    return Array.isArray(value) ? value : [];
}

function safeCount(value) {
    return Number.isInteger(value) && value >= 0 ? value : 0;
}

function safeText(value, fallback = '—') {
    return typeof value === 'string' && value.length > 0 ? value : fallback;
}

export function compilationMessage(compilation) {
    if (compilation?.state === 'ready' && typeof compilation.plan?.plan_id === 'string')
        return null;
    return safeText(
        compilation?.clarification_question,
        'Не удалось надёжно подготовить план. Уточните запрос.',
    );
}

export function executionPresentation(result) {
    if (result?.state === 'awaiting_approval' && result.approval_request)
        return {kind: 'approval', request: result.approval_request};
    if (result?.state === 'cancelled')
        return {kind: 'message', text: 'Действие отменено.'};
    if (result?.state !== 'completed')
        return {kind: 'message', text: 'Задачу не удалось выполнить.'};

    const lines = [];
    for (const step of safeArray(result.steps)) {
        const output = step?.output;
        if (!output)
            continue;
        if (Array.isArray(output.results)) {
            const resultCount = Number.isInteger(output.result_count)
                ? output.result_count
                : output.results.length;
            lines.push(`Найдено файлов: ${resultCount}`);
            for (const item of output.results.slice(0, 20))
                lines.push(safeText(item?.path));
            if (output.results.length > 20)
                lines.push(`…и ещё ${output.results.length - 20}`);
        } else if (Array.isArray(output.copied_paths)) {
            const copiedCount = Number.isInteger(output.copied_count)
                ? output.copied_count
                : output.copied_paths.length;
            lines.push(`Скопировано файлов: ${copiedCount}`);
            lines.push(`Папка: ${safeText(output.destination)}`);
        }
    }
    return {kind: 'message', text: lines.length ? lines.join('\n') : 'Готово.'};
}

export function approvalPresentation(request) {
    const itemCount = safeCount(request?.item_count);
    const names = safeArray(request?.item_names)
        .filter(name => typeof name === 'string' && name.length > 0)
        .slice(0, 5);
    const lines = [
        `Скопировать файлов: ${itemCount}`,
        `Назначение: ${safeText(request?.destination)}`,
    ];
    if (names.length)
        lines.push(`Файлы: ${names.join(', ')}${itemCount > names.length ? '…' : ''}`);
    return {
        title: 'ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ · R1',
        details: lines.join('\n'),
        confirmLabel: 'Подтвердить',
        cancelLabel: 'Отменить',
    };
}

export function runtimeErrorMessage(code) {
    if (typeof code !== 'string')
        return 'Ядро временно недоступно.';
    return RUNTIME_ERROR_MESSAGES[code] ?? 'Задачу не удалось выполнить.';
}

export function systemPresentation(health, capabilities, index) {
    const scheduler = index?.scheduler ?? {};
    return {
        runtime: health?.status === 'ok' ? 'подключено' : 'недоступно',
        capabilities: `${safeArray(capabilities?.capabilities).length}`,
        index: `${safeCount(index?.content_index?.sources)} документов · ${safeCount(index?.catalog?.entries)} файлов`,
        scheduler: `${schedulerLabel(scheduler.state)} · очередь ${safeCount(scheduler.queued)}`,
    };
}

export function taskRowLabel(task) {
    return `${activityLabel(task?.activity)} · ${taskStateLabel(task?.state)} · ` +
        `${safeCount(task?.processed_count)} обработано · ${safeCount(task?.skipped_count)} пропущено`;
}

export function taskDetailPresentation(task) {
    return {
        title: `${activityLabel(task?.activity)} · ${taskStateLabel(task?.state)}`,
        counts: `Обработано: ${safeCount(task?.processed_count)} · ` +
            `успешно: ${safeCount(task?.succeeded_count)} · ` +
            `пропущено: ${safeCount(task?.skipped_count)}`,
        references: safeArray(task?.references).map(reference => ({
            text: `${safeText(reference?.display_name)}${reference?.available ? '' : ' · недоступен'}\n${safeText(reference?.locator)}`,
            available: reference?.available === true,
        })),
    };
}

export function activityLabel(activity) {
    return ACTIVITY_LABELS[activity] ?? 'Системная задача';
}

export function taskStateLabel(state) {
    return TASK_STATE_LABELS[state] ?? 'неизвестно';
}

export function schedulerLabel(state) {
    return SCHEDULER_LABELS[state] ?? 'нет данных';
}
