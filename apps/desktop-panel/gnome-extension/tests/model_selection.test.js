import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../extension.js', import.meta.url), 'utf8');
const start = source.indexOf('    _openModelSelector() {');
const end = source.indexOf('\n    async _submitEntry(', start);
assert.ok(start >= 0 && end > start);
class Actor {
    constructor(props = {}) { Object.assign(this, props); this.children = []; this.signals = {}; this.clutter_text = {}; }
    add_child(child) { this.children.push(child); }
    set_child(child) { this.children = [child]; }
    connect(name, callback) { this.signals[name] = callback; }
    set_policy() {}
    set_style(style) { this.style = style; }
    destroy_all_children() { this.children = []; }
}
class Dialog extends Actor {
    constructor(props) { super(props); this.contentLayout = new Actor(); }
    setButtons(buttons) { this.buttons = buttons; }
    open() { return true; }
    close() { this.signals.closed(); }
}
const timers = new Map();
let nextTimer = 1;
const GLib = {
    PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
    timeout_add(_priority, _delay, callback) {
        const id = nextTimer++;
        timers.set(id, callback);
        return id;
    },
    Source: {remove(id) { timers.delete(id); }},
};
const fireTimer = () => {
    assert.equal(timers.size, 1);
    const [id, callback] = timers.entries().next().value;
    timers.delete(id);
    callback();
};
const methods = new Function('St', 'ModalDialog', 'Clutter', 'Pango', 'formatBytes', 'GLib',
    `return ({${source.slice(start, end)}});`)(
    {Label: Actor, ScrollView: Actor, BoxLayout: Actor, Button: Actor, Icon: Actor, PolicyType: {}},
    {ModalDialog: Dialog}, {KEY_Escape: 1}, {EllipsizeMode: {END: 1}}, size => `${size} B`, GLib);
const flush = () => new Promise(resolve => setImmediate(resolve));
function view(runtime) {
    return {_runtime: runtime, _modelSelectionGeneration: 0, _modelAccentColor: '#123456',
        _modelLabel: {text: 'old', set_text(text) { this.text = text; }},
        _setBusy() {}, _loadInferenceLifecycle() { this.refreshed = true; }};
}
const catalog = busy => ({active_model: 'old', busy, models: [
    {name: 'old', display_name: 'old', size_bytes: 10},
    {name: 'new', display_name: 'new', size_bytes: 20},
]});
const rows = dialog => dialog.contentLayout.children[2].children[0].children;

test('custom dialog applies selection only after runtime success', async () => {
    let resolveSelection;
    let selected;
    const v = view({workspaceModels: async () => catalog(false), selectWorkspaceModel: name => {
        selected = name; return new Promise(resolve => { resolveSelection = resolve; });
    }});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    assert.equal(dialog.styleClass, 'ai-model-dialog');
    await flush();
    assert.equal(rows(dialog)[1].style, 'border-color: #123456;');
    const selection = rows(dialog)[2].signals.clicked();
    assert.equal(selected, 'new');
    assert.equal(v._modelLabel.text, 'old');
    assert.equal(v._modelSelectionInFlight, true);
    resolveSelection({active_model: 'new'});
    await selection;
    assert.equal(v._modelLabel.text, 'new');
    assert.equal(v._modelDialog, null);
    assert.equal(v._modelSelectionInFlight, false);
    assert.equal(v.refreshed, true);
});

test('busy or failed selection keeps current model and provides retry', async () => {
    const v = view({workspaceModels: async () => catalog(true), selectWorkspaceModel: async () => {
        throw {code: 'workspace_model_busy'};
    }});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    await flush();
    assert.equal(rows(dialog)[2].reactive, false);
    // Simulate the server conflict that can also occur after a previously idle catalog.
    await rows(dialog)[2].signals.clicked();
    assert.equal(v._modelLabel.text, 'old');
    assert.equal(v._modelSelectionInFlight, false);
    assert.match(rows(dialog)[0].text, /подтверждения/);
    assert.equal(rows(dialog)[1].label, 'Обновить список');
    dialog.close();
    assert.equal(timers.size, 0);
});

test('closing while loading discards the late response', async () => {
    let resolveCatalog;
    const v = view({workspaceModels: () => new Promise(resolve => { resolveCatalog = resolve; })});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    dialog.close();
    resolveCatalog({...catalog(false), active_model: 'late'});
    await flush();
    assert.equal(v._modelLabel.text, 'old');
    assert.equal(v._modelDialog, null);
});

test('empty list and connection failures have explicit states', async () => {
    for (const fail of [false, true]) {
        const v = view({workspaceModels: async () => {
            if (fail) throw new Error('offline');
            return {active_model: 'old', models: []};
        }});
        methods._openModelSelector.call(v);
        const dialog = v._modelDialog;
        await flush();
        assert.match(rows(dialog)[0].text, fail ? /подключение/ : /нет установленных/);
        dialog.close();
    }
});


test('busy dialog unlocks after the task finishes and stops polling', async () => {
    let busy = true;
    let calls = 0;
    const v = view({workspaceModels: async () => { calls++; return catalog(busy); }});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    await flush();
    assert.equal(rows(dialog)[2].reactive, false);
    assert.equal(timers.size, 1);
    busy = false;
    fireTimer();
    await flush();
    assert.equal(rows(dialog)[2].reactive, true);
    assert.equal(calls, 2);
    assert.equal(timers.size, 0);
    dialog.close();
});

test('closing a busy dialog cancels refresh and ignores an in-flight response', async () => {
    let resolveRefresh;
    let calls = 0;
    const v = view({workspaceModels: async () => {
        calls++;
        if (calls === 1) return catalog(true);
        return new Promise(resolve => { resolveRefresh = resolve; });
    }});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    await flush();
    fireTimer();
    // Manual refresh must not start another overlapping request.
    dialog.buttons[0].action();
    assert.equal(calls, 2);
    dialog.close();
    resolveRefresh({...catalog(false), active_model: 'late'});
    await flush();
    assert.equal(v._modelLabel.text, 'old');
    assert.equal(timers.size, 0);
});

test('partial inspection errors keep valid rows available and manual refresh recovers', async () => {
    let fail = true;
    const v = view({workspaceModels: async () => ({...catalog(false),
        errors: fail ? [{name: 'broken', code: 'model_inspection_unavailable'}] : [],
    })});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    await flush();
    assert.match(rows(dialog)[1].text, /Остальные доступны/);
    assert.equal(rows(dialog)[3].reactive, true);
    fail = false;
    await dialog.buttons[0].action();
    assert.equal(rows(dialog).length, 3);
    dialog.close();
});

test('failed inspections are not described as no installed models', async () => {
    const v = view({workspaceModels: async () => ({active_model: 'old', models: [],
        errors: [{name: 'broken', code: 'model_inspection_unavailable'}],
    })});
    methods._openModelSelector.call(v);
    const dialog = v._modelDialog;
    await flush();
    assert.match(rows(dialog)[0].text, /Не удалось проверить/);
    assert.doesNotMatch(rows(dialog)[0].text, /нет установленных/);
    dialog.close();
});
