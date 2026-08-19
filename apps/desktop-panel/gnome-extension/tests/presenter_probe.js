import System from 'system';

import {
    executionPresentation,
    monitorPresentation,
    systemPresentation,
    taskRowLabel,
} from '../panel-presenter.js';


try {
    const execution = executionPresentation({state: 'completed', steps: []});
    const system = systemPresentation({status: 'ok'}, {capabilities: []}, {});
    const task = taskRowLabel({activity: 'documents.search', state: 'running'});
    const monitor = monitorPresentation({supported: true, cpu: {}, memory: {}, battery: {}});
    if (execution.text !== 'Готово.' || system.runtime !== 'подключено' || !monitor.available || !task)
        throw new Error('unexpected presenter output');
    print(JSON.stringify({status: 'ok'}));
} catch (error) {
    printerr(error.message ?? 'presenter_probe_failed');
    System.exit(1);
}
