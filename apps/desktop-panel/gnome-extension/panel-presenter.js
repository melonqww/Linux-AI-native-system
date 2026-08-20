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

export function monitorPresentation(snapshot) {
    if (snapshot?.supported !== true) {
        return {
            available: false,
            cpu: {value: null, detail: 'модуль недоступен'},
            memory: {value: null, detail: 'модуль недоступен'},
            battery: {value: null, detail: 'не обнаружена'},
            disks: [],
            processes: [],
            summary: 'системные метрики недоступны',
        };
    }
    const cpu = snapshot.cpu ?? {};
    const memory = snapshot.memory ?? {};
    const battery = snapshot.battery ?? {};
    const cpuTemperature = formatTemperature(cpu.temperature_celsius);
    const load = safeArray(cpu.load_average).slice(0, 3).map(value =>
        formatLoadPercent(value, cpu.logical_cpus),
    );
    const topology = [
        Number.isInteger(cpu.physical_cores) ? `ядер ${cpu.physical_cores}` : '',
        Number.isInteger(cpu.logical_cpus) ? `потоков ${cpu.logical_cpus}` : '',
        Number.isInteger(cpu.packages) ? `пакетов ${cpu.packages}` : '',
    ].filter(Boolean).join(' · ');
    const cpuDetails = [
        cpuTemperature,
        load.length ? `Нагрузка 1/5/15 мин: ${load.join(' / ')}` : '',
        topology,
    ].filter(Boolean).join(' · ') || 'датчики недоступны';
    const usedMemory = safeCount(memory.used_bytes);
    const totalMemory = safeCount(memory.total_bytes);
    const swapUsed = safeCount(memory.swap_used_bytes);
    const swapTotal = safeCount(memory.swap_total_bytes);
    const channelMode = safeText(memory.channel_mode, 'unknown');
    const channelLabel = {
        single: 'одноканальная',
        dual: 'двухканальная',
        triple: 'трёхканальная',
        quad: 'четырёхканальная',
        multi: 'многоканальная',
        unknown: 'канальность неизвестна',
    }[channelMode] ?? channelMode;
    const memoryDetails = totalMemory > 0
        ? `${formatBytes(usedMemory)} из ${formatBytes(totalMemory)}` +
            (swapTotal > 0 ? ` · swap ${formatBytes(swapUsed)}` : '') +
            ` · ${channelLabel}`
        : 'данные недоступны';
    const batteryDetails = battery.present === true
        ? [batteryStatusLabel(battery.status), formatTemperature(battery.temperature_celsius)]
            .filter(Boolean).join(' · ')
        : 'не обнаружена';
    const uptime = formatDuration(snapshot.uptime_seconds);
    return {
        available: true,
        cpu: {value: safePercent(cpu.usage_percent), detail: cpuDetails},
        memory: {value: safePercent(memory.usage_percent), detail: memoryDetails},
        battery: {
            value: battery.present === true ? safePercent(battery.percent) : null,
            detail: batteryDetails,
        },
        disks: safeArray(snapshot.disks).slice(0, 16).map(disk => ({
            label: diskLabel(disk),
            value: `${formatBytes(safeCount(disk?.free_bytes))} из ${formatBytes(safeCount(disk?.total_bytes))}`,
        })),
        processes: safeArray(snapshot.processes).slice(0, 20).map(process => ({
            pid: safeCount(process?.pid),
            name: safeText(process?.name, `PID ${safeCount(process?.pid)}`),
            cpu: `${safePercent(process?.cpu_percent) ?? 0}%`,
            memory: formatBytes(safeCount(process?.memory_bytes)),
        })),
        summary: uptime ? `работает ${uptime}` : 'данные обновлены',
    };
}

function diskLabel(disk) {
    const mount = safeText(disk?.mount_point, 'неизвестно');
    const windowsDrive = mount.match(/^([A-Za-z]):(?:\\|$)/);
    if (windowsDrive)
        return `Диск ${windowsDrive[1].toUpperCase()}`;
    const source = typeof disk?.source === 'string' ? disk.source : '';
    const sourceName = source.split('/').filter(Boolean).pop();
    return `Диск ${sourceName || mount}`;
}

function formatLoadPercent(value, logicalCpus) {
    if (typeof value !== 'number' || !Number.isFinite(value))
        return '—';
    const cpus = Number.isInteger(logicalCpus) && logicalCpus > 0 ? logicalCpus : 1;
    return `${Math.round(Math.max(0, value) / cpus * 100)}%`;
}

export function formatBytes(value) {
    const bytes = safeCount(value);
    if (bytes >= 1024 ** 3)
        return `${(bytes / 1024 ** 3).toFixed(1)} ГБ`;
    if (bytes >= 1024 ** 2)
        return `${Math.round(bytes / 1024 ** 2)} МБ`;
    if (bytes >= 1024)
        return `${Math.round(bytes / 1024)} КБ`;
    return `${bytes} Б`;
}

function safePercent(value) {
    return typeof value === 'number' && Number.isFinite(value)
        ? Math.round(Math.max(0, Math.min(100, value)))
        : null;
}

function formatTemperature(value) {
    return typeof value === 'number' && Number.isFinite(value)
        ? `${Math.round(value)}°C`
        : '';
}

function batteryStatusLabel(status) {
    return {
        charging: 'заряжается',
        discharging: 'от батареи',
        full: 'заряжена',
        'not charging': 'не заряжается',
        mixed: 'смешанное состояние',
    }[status] ?? 'состояние неизвестно';
}

function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0)
        return '';
    const totalMinutes = Math.floor(seconds / 60);
    const days = Math.floor(totalMinutes / 1440);
    const hours = Math.floor(totalMinutes % 1440 / 60);
    const minutes = totalMinutes % 60;
    if (days > 0)
        return `${days} д. ${hours} ч.`;
    if (hours > 0)
        return `${hours} ч. ${minutes} мин.`;
    return `${minutes} мин.`;
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
