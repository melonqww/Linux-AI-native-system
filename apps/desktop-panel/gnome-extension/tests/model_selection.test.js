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
const methods = new Function('St', 'ModalDialog', 'Clutter', 'Pango', 'formatBytes',
    `return ({${source.slice(start, end)}});`)(
    {Label: Actor, ScrollView: Actor, BoxLayout: Actor, Button: Actor, Icon: Actor, PolicyType: {}},
    {ModalDialog: Dialog}, {KEY_Escape: 1}, {EllipsizeMode: {END: 1}}, size => `${size} B`);
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
