import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const PANEL_WIDTH = 460;
const DEFAULT_PANEL_HEIGHT = 420;
const MAX_PANEL_HEIGHT = 560;
const MIN_PANEL_HEIGHT = 360;
const TOGGLE_WIDTH = 26;
const SHELL_WIDTH = PANEL_WIDTH + TOGGLE_WIDTH;
const PANEL_MARGIN = 24;
const TAB_HEIGHT = 43;
const RUNTIME_URL = 'http://127.0.0.1:8765/v1/search';

class RuntimeClient {
    // The visual prototype must not depend on an optional Soup typelib.
    // Runtime transport will be plugged back in after the shell surface is
    // stable and the service package is installed on the target distro.
    search(_text) {
        return Promise.resolve([]);
    }

    destroy() {}
}

const TabButton = GObject.registerClass(
class TabButton extends St.Button {
    _init(label, icon) {
        super._init({style_class: 'ai-tab', can_focus: true, x_expand: true});
        this._active = false;
        this._hovered = false;

        const content = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            x_expand: true,
            y_expand: true,
        });
        this._highlight = new St.Widget({
            style_class: 'ai-tab-highlight',
            x_expand: true,
            y_expand: true,
        });
        this._label = new St.Label({
            text: `${icon}  ${label}`,
            style_class: 'ai-tab-label',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        content.add_child(this._highlight);
        content.add_child(this._label);
        this.set_child(content);
        this._highlight.set_opacity(0);
        this._highlight.set_scale(0.72, 0.72);

        this.connect('enter-event', () => {
            this._hovered = true;
            this._animateHighlight(true);
        });
        this.connect('leave-event', () => {
            this._hovered = false;
            this._animateHighlight(this._active);
        });
    }

    setActive(active) {
        this._active = active;
        this._animateHighlight(active || this._hovered);
    }

    _animateHighlight(visible) {
        this._highlight.ease({
            opacity: visible ? 255 : 0,
            scale_x: visible ? 1 : 0.72,
            scale_y: visible ? 1 : 0.72,
            duration: 320,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }
});

const ChatView = GObject.registerClass(
class ChatView extends St.BoxLayout {
    _init(runtime) {
        super._init({
            vertical: true,
            style_class: 'ai-chat-view',
            x_expand: true,
            y_expand: true,
        });

        this._runtime = runtime;
        this._messages = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-messages',
            x_expand: true,
        });
        this._addInitialMessages();

        this._scroll = new St.ScrollView({
            style_class: 'ai-scroll',
            x_expand: true,
            y_expand: true,
        });
        this._scroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);
        this._scroll.set_child(this._messages);
        this.add_child(this._scroll);

        this._composer = this._buildComposer();
        this.add_child(this._composer);
    }

    _assistant(text, styleClass = 'ai-assistant-message') {
        const message = new St.Label({
            text,
            style_class: styleClass,
            x_expand: true,
        });
        message.clutter_text.line_wrap = true;
        message.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        return message;
    }

    _user(text) {
        return new St.Label({
            text,
            style_class: 'ai-user-message',
            x_align: Clutter.ActorAlign.END,
        });
    }

    _addInitialMessages() {
        this._messages.add_child(this._assistant(
            'Привет. Я локальный помощник Ubuntu. Могу показать состояние системы, подготовить workspace или объяснить ошибку.',
        ));
        this._messages.add_child(this._user('Покажи состояние системы'));

        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-plan-card',
            x_expand: true,
        });
        card.add_child(new St.Label({
            text: 'БЕЗОПАСНЫЙ ПЛАН · R0',
            style_class: 'ai-plan-title',
        }));
        card.add_child(this._assistant(
            'Прочитаю информацию о диске, памяти и загрузке CPU. Ничего в системе не изменится.',
            'ai-plan-details',
        ));
        const runButton = new St.Button({
            label: 'Запустить проверку',
            style_class: 'ai-plan-action',
            x_align: Clutter.ActorAlign.START,
        });
        runButton.connect('clicked', () => this._simulateResponse());
        card.add_child(runButton);
        this._messages.add_child(card);
    }

    _buildComposer() {
        const composer = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-composer',
            x_expand: true,
        });
        const entry = new St.Entry({
            style_class: 'ai-entry',
            hint_text: 'Сообщение для вашего ИИ',
            can_focus: true,
            x_expand: true,
        });
        entry.clutter_text.line_wrap = true;
        entry.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        entry.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        composer.add_child(entry);

        const actions = new St.BoxLayout({style_class: 'ai-composer-actions', x_expand: true});
        const confirmation = new St.Button({
            style_class: 'ai-confirmation',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const confirmationContent = new St.BoxLayout({
            style_class: 'ai-confirmation-content',
            x_expand: true,
        });
        confirmationContent.set_spacing(6);
        confirmationContent.add_child(new St.Icon({
            icon_name: 'security-high-symbolic',
            style_class: 'ai-confirmation-icon',
        }));
        confirmationContent.add_child(new St.Label({
            text: 'Подтверждать за меня',
            style_class: 'ai-confirmation-label',
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
        }));
        confirmation.set_child(confirmationContent);
        confirmation.connect('clicked', () => {
            confirmation._enabled = !confirmation._enabled;
            if (confirmation._enabled)
                confirmation.add_style_class_name('enabled');
            else
                confirmation.remove_style_class_name('enabled');
        });
        actions.add_child(confirmation);

        const modelControl = new St.Widget({
            style_class: 'ai-model-control',
            layout_manager: new Clutter.FixedLayout(),
        });
        modelControl.set_size(145, 34);
        const modelButton = new St.Button({
            style_class: 'ai-model',
            can_focus: true,
        });
        modelButton.set_size(145, 34);
        const modelContent = new St.BoxLayout({
            style_class: 'ai-model-content',
            x_expand: true,
        });
        modelContent.set_spacing(6);
        const modelLabel = new St.Label({
            text: 'Qwen 3.5 2B',
            style_class: 'ai-model-label',
            x_expand: true,
            x_align: Clutter.ActorAlign.END,
        });
        modelContent.add_child(modelLabel);
        modelContent.add_child(new St.Label({
            text: '⌄',
            style_class: 'ai-model-chevron',
        }));
        modelButton.set_child(modelContent);
        modelControl.add_child(modelButton);

        const modelMenu = new St.BoxLayout({vertical: true, style_class: 'ai-model-menu'});
        modelMenu.set_position(0, -116);
        modelMenu.set_size(145, 110);
        modelMenu.hide();
        ['Qwen 3.5 2B', 'Gemma 2 2B', 'Qwen 3.5 4B'].forEach(model => {
            const option = new St.Button({label: model, style_class: 'ai-model-option'});
            option.connect('clicked', () => {
                modelLabel.set_text(model);
                modelMenu.hide();
            });
            modelMenu.add_child(option);
        });
        modelControl.add_child(modelMenu);
        modelButton.connect('clicked', () => modelMenu.visible = !modelMenu.visible);
        actions.add_child(modelControl);

        const send = new St.Button({label: '↑', style_class: 'ai-send', can_focus: true});
        send.connect('clicked', () => this._submitEntry(entry));
        entry.clutter_text.connect('activate', () => this._submitEntry(entry));
        actions.add_child(send);
        composer.add_child(actions);
        return composer;
    }

    _submitEntry(entry) {
        const text = entry.get_text().trim();
        if (!text)
            return;
        this._messages.add_child(this._user(text));
        entry.set_text('');
        this._runtime.search(text).then(results => {
            const answer = results.length
                ? results.map(item => item.path).join('\n')
                : 'Совпадений в доступном индексе не найдено.';
            this._messages.add_child(this._assistant(answer));
        }).catch(error => {
            this._messages.add_child(this._assistant(`Runtime недоступен: ${error.message}`));
        });
    }

    _simulateResponse() {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1100, () => {
            this._messages.add_child(this._assistant(
                'Готово. В рабочей версии здесь появится проверенный результат инструмента и запись в audit log.',
            ));
            return GLib.SOURCE_REMOVE;
        });
    }
});

const WorkspaceView = GObject.registerClass(
class WorkspaceView extends St.Widget {
    _init() {
        super._init({style_class: 'ai-empty-view', x_expand: true, y_expand: true});
    }
});

const Panel = GObject.registerClass(
class Panel extends St.Widget {
    _init(runtime) {
        super._init({
            style_class: 'ai-native-shell',
            reactive: true,
            layout_manager: new Clutter.FixedLayout(),
        });
        this._runtime = runtime;
        this.set_size(SHELL_WIDTH, DEFAULT_PANEL_HEIGHT);
        this._collapsed = false;

        this._content = new St.Widget({
            style_class: 'ai-panel-content',
            layout_manager: new Clutter.FixedLayout(),
        });
        this._content.set_position(TOGGLE_WIDTH, 0);
        this._content.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT);
        this.add_child(this._content);

        this._toggle = new St.Button({style_class: 'ai-toggle', label: '›', can_focus: true});
        this._toggle.set_size(TOGGLE_WIDTH, 40);
        this._toggle.set_position(0, Math.floor((DEFAULT_PANEL_HEIGHT - 40) / 2));
        this._toggle.connect('clicked', () => this._togglePanel());
        this.add_child(this._toggle);
        this._buildTabs();
    }

    setPanelHeight(height) {
        this.set_size(SHELL_WIDTH, height);
        this._content.set_size(PANEL_WIDTH, height);
        this._toggle.set_position(0, Math.floor((height - 40) / 2));
        if (!this._views)
            return;
        this._views.set_size(PANEL_WIDTH, height - TAB_HEIGHT);
        [this._chat, this._workspace, this._settings].forEach(view => {
            view.set_size(PANEL_WIDTH, height - TAB_HEIGHT);
        });
    }

    _buildTabs() {
        this._tabs = new St.BoxLayout({style_class: 'ai-tabs', x_expand: true});
        this._tabs.set_position(0, 0);
        this._tabs.set_size(PANEL_WIDTH, TAB_HEIGHT);
        this._content.add_child(this._tabs);

        this._views = new St.Widget({layout_manager: new Clutter.FixedLayout()});
        this._views.set_position(0, TAB_HEIGHT);
        this._views.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._content.add_child(this._views);

        this._chat = new ChatView(this._runtime);
        this._chat.set_position(0, 0);
        this._chat.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._views.add_child(this._chat);
        this._workspace = new WorkspaceView();
        this._workspace.set_position(0, 0);
        this._workspace.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._workspace.hide();
        this._views.add_child(this._workspace);
        this._settings = new WorkspaceView();
        this._settings.set_position(0, 0);
        this._settings.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._settings.hide();
        this._views.add_child(this._settings);

        this._tabButtons = [
            new TabButton('Боковая панель', '☰'),
            new TabButton('Рабочая область', '▣'),
            new TabButton('Настройки', '⚙'),
        ];
        const tabWidth = Math.floor((PANEL_WIDTH - 14) / this._tabButtons.length);
        this._tabButtons.forEach((button, index) => {
            button.set_width(tabWidth);
            button.set_height(TAB_HEIGHT - 3);
            button.connect('clicked', () => this._selectTab(index));
            this._tabs.add_child(button);
        });
        this._selectTab(0);
        this.setPanelHeight(DEFAULT_PANEL_HEIGHT);
    }

    _selectTab(index) {
        const views = [this._chat, this._workspace, this._settings];
        views.forEach((view, viewIndex) => view.visible = viewIndex === index);
        this._tabButtons.forEach((button, buttonIndex) => button.setActive(buttonIndex === index));
    }

    _togglePanel() {
        this._collapsed = !this._collapsed;
        this._toggle.set_label(this._collapsed ? '‹' : '›');
        this.ease({
            translation_x: this._collapsed ? PANEL_WIDTH : 0,
            duration: 420,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }
});

export default class AiNativeLinuxExtension extends Extension {
    _attachPanel() {
        this._panel = new Panel(this._runtime);
        Main.layoutManager.addChrome(this._panel, {trackFullscreen: false, affectsStruts: false});
        this._monitorChangedId = Main.layoutManager.connect('monitors-changed', () => this._positionPanel());
        this._positionPanel();
    }

    _attachFallback(error) {
        logError(error, 'AI-native Linux: panel construction failed');
        // Keep the extension ACTIVE even when a single Shell API changes.  A
        // visible fallback makes the failure diagnosable and avoids leaving a
        // stale ERROR state with no UI at all.
        this._panel = new St.Button({
            label: 'AI-native Linux: ошибка панели',
            style_class: 'ai-native-fallback',
            reactive: true,
        });
        this._panel.connect('clicked', () => log('AI-native Linux: fallback is alive'));
        Main.layoutManager.addChrome(this._panel, {trackFullscreen: false, affectsStruts: false});
        const monitor = Main.layoutManager.primaryMonitor;
        if (monitor)
            this._panel.set_position(monitor.x + monitor.width - 330, monitor.y + 24);
    }

    enable() {
        try {
            this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
            this._stylesheet = this.dir.get_child('stylesheet.css');
            if (this._stylesheet) {
                this._theme.load_stylesheet(this._stylesheet);
                this._stylesheetLoaded = true;
            }
        } catch (error) {
            // A theme parser/API mismatch must not prevent the native panel
            // from loading.  GNOME will log the style error, while the panel
            // remains usable with its safe default theme.
            logError(error, 'AI-native Linux: stylesheet load failed');
            this._stylesheetLoaded = false;
            this._stylesheet = null;
            this._theme = null;
        }
        this._runtime = new RuntimeClient();
        try {
            this._attachPanel();
        } catch (error) {
            this._attachFallback(error);
        }
    }

    _positionPanel() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._panel)
            return;
        const height = Math.min(
            MAX_PANEL_HEIGHT,
            Math.max(MIN_PANEL_HEIGHT, Math.round(monitor.height * 0.52)),
        );
        this._panel.setPanelHeight(height);
        this._panel.set_position(
            monitor.x + monitor.width - PANEL_WIDTH - PANEL_MARGIN - TOGGLE_WIDTH,
            monitor.y + monitor.height - height - PANEL_MARGIN,
        );
    }

    disable() {
        if (this._monitorChangedId) {
            Main.layoutManager.disconnect(this._monitorChangedId);
            this._monitorChangedId = 0;
        }
        this._panel?.destroy();
        this._runtime?.destroy();
        if (this._stylesheetLoaded && this._stylesheet) {
            this._theme.unload_stylesheet(this._stylesheet);
            this._stylesheetLoaded = false;
        }
        this._stylesheet = null;
        this._theme = null;
        this._panel = null;
        this._runtime = null;
    }
}
