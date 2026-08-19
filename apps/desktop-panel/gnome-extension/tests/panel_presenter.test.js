import assert from 'node:assert/strict';
import test from 'node:test';

import {
    activityLabel,
    approvalPresentation,
    compilationMessage,
    executionPresentation,
    formatBytes,
    monitorPresentation,
    runtimeErrorMessage,
    schedulerLabel,
    systemPresentation,
    taskDetailPresentation,
    taskRowLabel,
    taskStateLabel,
} from '../panel-presenter.js';


test('ready compilation with a plan proceeds without a message', () => {
    assert.equal(compilationMessage({
        state: 'ready',
        plan: {plan_id: 'plan-1'},
    }), null);
});

test('compiler clarification is shown verbatim', () => {
    assert.equal(compilationMessage({
        state: 'needs_clarification',
        clarification_question: 'Какие именно PDF нужны?',
    }), 'Какие именно PDF нужны?');
});

test('malformed ready compilation cannot execute', () => {
    assert.equal(
        compilationMessage({state: 'ready', plan: {}}),
        'Не удалось надёжно подготовить план. Уточните запрос.',
    );
});

test('search execution limits visible paths to twenty', () => {
    const results = Array.from({length: 23}, (_, index) => ({path: `/docs/${index}.pdf`}));
    const view = executionPresentation({
        state: 'completed',
        steps: [{output: {result_count: 23, results}}],
    });
    assert.equal(view.kind, 'message');
    assert.match(view.text, /^Найдено файлов: 23/);
    assert.match(view.text, /\/docs\/19\.pdf/);
    assert.doesNotMatch(view.text, /\/docs\/20\.pdf/);
    assert.match(view.text, /…и ещё 3$/);
});

test('copy execution reports count and destination', () => {
    assert.deepEqual(executionPresentation({
        state: 'completed',
        steps: [{output: {
            copied_count: 2,
            copied_paths: ['/Desktop/math/a.pdf', '/Desktop/math/b.pdf'],
            destination: '/home/user/Desktop/math',
        }}],
    }), {
        kind: 'message',
        text: 'Скопировано файлов: 2\nПапка: /home/user/Desktop/math',
    });
});

test('terminal and malformed execution states stay user-safe', () => {
    assert.deepEqual(executionPresentation({state: 'cancelled'}), {
        kind: 'message', text: 'Действие отменено.',
    });
    assert.deepEqual(executionPresentation({state: 'failed', traceback: 'secret'}), {
        kind: 'message', text: 'Задачу не удалось выполнить.',
    });
    assert.deepEqual(executionPresentation(null), {
        kind: 'message', text: 'Задачу не удалось выполнить.',
    });
    assert.equal(executionPresentation({state: 'completed', steps: []}).text, 'Готово.');
});

test('approval keeps only safe preview fields and five names', () => {
    const request = {
        approval_request_id: 'approval-1',
        item_count: 7,
        destination: '/home/user/Desktop/math',
        item_names: ['1.pdf', '2.pdf', '3.pdf', '4.pdf', '5.pdf', '6.pdf'],
        internal_token: 'must-not-leak',
    };
    assert.deepEqual(executionPresentation({
        state: 'awaiting_approval', approval_request: request,
    }), {kind: 'approval', request});
    const view = approvalPresentation(request);
    assert.equal(view.title, 'ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ · R1');
    assert.equal(view.confirmLabel, 'Подтвердить');
    assert.equal(view.cancelLabel, 'Отменить');
    assert.match(view.details, /Файлы: 1\.pdf, 2\.pdf, 3\.pdf, 4\.pdf, 5\.pdf…$/);
    assert.doesNotMatch(JSON.stringify(view), /internal_token|must-not-leak|6\.pdf/);
});

test('known runtime errors are translated and unknown details never leak', () => {
    const cases = {
        runtime_unavailable: 'Ядро не запущено или недоступно.',
        runtime_timeout: 'Ядро не ответило вовремя.',
        service_unavailable: 'Нужный компонент ядра сейчас недоступен.',
        intent_compiler_unavailable: 'Понимание естественных запросов сейчас недоступно.',
        invalid_request: 'Запрос не удалось обработать.',
        secure_transport_required: 'Действие требует защищённого соединения.',
        task_action_not_available: 'Это действие сейчас недоступно.',
    };
    for (const [code, message] of Object.entries(cases))
        assert.equal(runtimeErrorMessage(code), message);
    assert.equal(runtimeErrorMessage(null), 'Ядро временно недоступно.');
    assert.equal(runtimeErrorMessage('Traceback: /home/private/file.py'), 'Задачу не удалось выполнить.');
});

test('system status formats runtime, capabilities, index and scheduler', () => {
    assert.deepEqual(systemPresentation(
        {status: 'ok'},
        {capabilities: [{name: 'documents.search'}, {name: 'files.copy'}]},
        {
            content_index: {sources: 18},
            catalog: {entries: 340},
            scheduler: {state: 'updating', queued: 4},
        },
    ), {
        runtime: 'подключено',
        capabilities: '2',
        index: '18 документов · 340 файлов',
        scheduler: 'обновление · очередь 4',
    });
    assert.deepEqual(systemPresentation(null, null, null), {
        runtime: 'недоступно',
        capabilities: '0',
        index: '0 документов · 0 файлов',
        scheduler: 'нет данных · очередь 0',
    });
});

test('system monitor snapshot formats metrics, disks and processes', () => {
    const view = monitorPresentation({
        supported: true,
        uptime_seconds: 90061,
        cpu: {
            usage_percent: 37.4,
            load_average: [1.25, 0.5, 0.25],
            temperature_celsius: 54.2,
        },
        memory: {
            usage_percent: 62.1,
            used_bytes: 5 * 1024 ** 3,
            total_bytes: 8 * 1024 ** 3,
            swap_used_bytes: 256 * 1024 ** 2,
            swap_total_bytes: 2 * 1024 ** 3,
        },
        battery: {present: true, percent: 82, status: 'discharging', temperature_celsius: 31},
        disks: [{mount_point: '/', free_bytes: 40 * 1024 ** 3, total_bytes: 100 * 1024 ** 3}],
        processes: [{pid: 42, name: 'gnome-shell', cpu_percent: 7.2, memory_bytes: 410 * 1024 ** 2}],
    });

    assert.equal(view.available, true);
    assert.deepEqual(view.cpu, {value: 37, detail: '54°C · load 1.25 / 0.5 / 0.25'});
    assert.deepEqual(view.memory, {value: 62, detail: '5.0 ГБ из 8.0 ГБ · swap 256 МБ'});
    assert.deepEqual(view.battery, {value: 82, detail: 'от батареи · 31°C'});
    assert.deepEqual(view.disks, [{label: '/ · свободно', value: '40.0 ГБ из 100.0 ГБ'}]);
    assert.deepEqual(view.processes, [{pid: 42, name: 'gnome-shell', cpu: '7%', memory: '410 МБ'}]);
    assert.equal(view.summary, 'работает 1 д. 1 ч.');
});

test('system monitor degrades deterministically when unavailable', () => {
    const view = monitorPresentation(null);
    assert.equal(view.available, false);
    assert.equal(view.cpu.value, null);
    assert.equal(view.memory.value, null);
    assert.equal(view.battery.detail, 'не обнаружена');
    assert.deepEqual(view.disks, []);
    assert.deepEqual(view.processes, []);
});

test('byte formatting is stable across system metric units', () => {
    assert.equal(formatBytes(0), '0 Б');
    assert.equal(formatBytes(1024), '1 КБ');
    assert.equal(formatBytes(5 * 1024 ** 2), '5 МБ');
    assert.equal(formatBytes(1.5 * 1024 ** 3), '1.5 ГБ');
    assert.equal(formatBytes(-1), '0 Б');
});

test('all public task and scheduler states have stable labels', () => {
    const states = {
        planned: 'запланировано', running: 'выполняется',
        awaiting_approval: 'ожидает подтверждения', interrupted: 'прервано',
        completed: 'завершено', completed_with_skips: 'завершено с пропусками',
        failed: 'не выполнено', cancelled: 'отменено',
    };
    for (const [state, label] of Object.entries(states))
        assert.equal(taskStateLabel(state), label);
    assert.equal(taskStateLabel('internal'), 'неизвестно');

    assert.equal(schedulerLabel('idle'), 'ожидание');
    assert.equal(schedulerLabel('updating'), 'обновление');
    assert.equal(schedulerLabel('paused_load'), 'пауза из-за нагрузки');
    assert.equal(schedulerLabel('degraded'), 'ограниченный режим');
    assert.equal(schedulerLabel('broken'), 'нет данных');
});

test('task list and detail expose user data without internal diagnostics', () => {
    const task = {
        activity: 'documents.ocr',
        state: 'completed_with_skips',
        processed_count: 25,
        succeeded_count: 23,
        skipped_count: 2,
        traceback: '/private/source.py:99',
        references: [
            {display_name: 'math.pdf', locator: '/docs/math.pdf', available: true},
            {display_name: 'broken.pdf', locator: '/docs/broken.pdf', available: false},
        ],
    };
    assert.equal(activityLabel(task.activity), 'Распознавание документов');
    assert.equal(
        taskRowLabel(task),
        'Распознавание документов · завершено с пропусками · 25 обработано · 2 пропущено',
    );
    const detail = taskDetailPresentation(task);
    assert.equal(detail.title, 'Распознавание документов · завершено с пропусками');
    assert.equal(detail.counts, 'Обработано: 25 · успешно: 23 · пропущено: 2');
    assert.deepEqual(detail.references, [
        {text: 'math.pdf\n/docs/math.pdf', available: true},
        {text: 'broken.pdf · недоступен\n/docs/broken.pdf', available: false},
    ]);
    assert.doesNotMatch(JSON.stringify(detail), /traceback|source\.py/);
});

test('unknown and malformed task data has deterministic fallbacks', () => {
    assert.equal(activityLabel('plugin.unknown'), 'Системная задача');
    assert.equal(taskRowLabel(null), 'Системная задача · неизвестно · 0 обработано · 0 пропущено');
    assert.deepEqual(taskDetailPresentation(null), {
        title: 'Системная задача · неизвестно',
        counts: 'Обработано: 0 · успешно: 0 · пропущено: 0',
        references: [],
    });
});
