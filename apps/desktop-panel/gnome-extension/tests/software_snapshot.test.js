import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

// Exercise the actual asynchronous view method without requiring a GNOME session.
const source = readFileSync(new URL('../extension.js', import.meta.url), 'utf8');
const start = source.indexOf('    async _loadSoftwareSnapshot(body, section, generation) {');
const end = source.indexOf('\n    _renderSoftwareSnapshot(', start);
assert.ok(start >= 0 && end > start);
class RuntimeRequestError extends Error {}
class Actor {
    constructor() { this.children = []; }
    add_child(child) { this.children.push(child); }
    get_children() { return this.children; }
    destroy() {}
}
const methods = new Function('RuntimeRequestError', 'St',
    `return ({${source.slice(start, end)}});`)(RuntimeRequestError, {
    BoxLayout: Actor,
    Label: Actor,
});

for (const section of ['catalog', 'library']) {
    test(`${section} recovers after a connection error with unchanged data`, async () => {
        const snapshot = {catalog: [], tasks: [], backups: []};
        let fail = false;
        let screen;
        let renders = 0;
        const view = {
            _softwareGeneration: 1,
            _runtime: {softwareSnapshot: async () => {
                if (fail) throw new RuntimeRequestError('connection_lost');
                return snapshot;
            }},
            _softwareShellSignature: () => '',
            _renderSoftwareSnapshot: () => { screen = 'catalog'; renders++; },
            _renderModelSetupCard: () => {},
        };
        const body = new Actor();
        body.add_child = () => { screen = 'error'; };
        const load = () => methods._loadSoftwareSnapshot.call(view, body, section, 1);
        await load();
        assert.equal(screen, 'catalog');
        await load();
        assert.equal(renders, 1, 'unchanged successful data should not redraw');
        fail = true;
        await load();
        assert.equal(screen, 'error');
        fail = false;
        await load();
        assert.equal(screen, 'catalog');
        assert.equal(renders, 2, 'recovery must replace the error screen');
    });
}
