import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../extension.js', import.meta.url), 'utf8');
const start = source.indexOf('    async _pollWorkspace() {');
const end = source.indexOf('\n    _renderWorkspaceMessages(', start);
assert.ok(start >= 0 && end > start);
const poll = new Function(`return ({${source.slice(start, end)}});`)()._pollWorkspace;

test('outages are deduplicated until a complete successful poll restores the connection', async () => {
    let failRun = true;
    let failMessages = false;
    let notices = 0;
    const view = {
        _workspaceRunId: 'run', _workspacePollErrorShown: false,
        _runtime: {
            workspaceRun: async () => {
                if (failRun) throw new Error('offline');
                return {stage: 'running'};
            },
            workspaceMessages: async () => {
                if (failMessages) throw new Error('offline');
                return {messages: []};
            },
        },
        _rememberWorkspaceRun() {}, _renderWorkspaceMessages() {},
        _isTerminalStage: () => false,
        _append() { notices++; }, _workspaceErrorNotice: text => text,
        _friendlyError: error => error.message,
    };
    await poll.call(view);
    await poll.call(view);
    assert.equal(notices, 1);
    failRun = false;
    failMessages = true;
    await poll.call(view);
    assert.equal(notices, 1, 'one successful endpoint is not a recovered poll');
    failMessages = false;
    await poll.call(view);
    assert.equal(view._workspacePollErrorShown, false);
    failRun = true;
    await poll.call(view);
    assert.equal(notices, 2, 'a new outage after recovery needs a new notice');
    await poll.call(view);
    assert.equal(notices, 2);
});
