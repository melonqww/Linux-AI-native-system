import System from 'system';

import {
    executionPresentation,
    systemPresentation,
    taskRowLabel,
} from '../panel-presenter.js';


try {
    const execution = executionPresentation({state: 'completed', steps: []});
    const system = systemPresentation({status: 'ok'}, {capabilities: []}, {});
    const task = taskRowLabel({activity: 'documents.search', state: 'running'});
    if (execution.text !== 'Готово.' || system.runtime !== 'подключено' || !task)
        throw new Error('unexpected presenter output');
    print(JSON.stringify({status: 'ok'}));
} catch (error) {
    printerr(error.message ?? 'presenter_probe_failed');
    System.exit(1);
}
