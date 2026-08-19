import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const PANEL_WIDTH = 468;
const DEFAULT_PANEL_HEIGHT = 420;
const MAX_PANEL_HEIGHT = 560;
const MIN_PANEL_HEIGHT = 360;
const TOGGLE_WIDTH = 26;
const SHELL_WIDTH = PANEL_WIDTH + TOGGLE_WIDTH;
const PANEL_HORIZONTAL_MARGIN = 20;
const PANEL_BOTTOM_MARGIN = 18;
const TOGGLE_DURATION = 260;
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

        const content = new St.BoxLayout({
            style_class: 'ai-tab-content',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._highlight = new St.Widget({
            style_class: 'ai-tab-highlight',
            x_expand: true,
            y_expand: true,
        });
        this._icon = new St.Icon({
            icon_name: icon,
            style_class: icon === 'preferences-system-symbolic'
                ? 'ai-tab-icon ai-tab-icon-settings'
                : 'ai-tab-icon',
        });
        this._label = new St.Label({
            text: label,
            style_class: 'ai-tab-label',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        content.add_child(this._icon);
        content.add_child(this._label);
        const layeredContent = new St.Widget({layout_manager: new Clutter.BinLayout(), x_expand: true, y_expand: true});
        layeredContent.add_child(this._highlight);
        layeredContent.add_child(content);
        this.set_child(layeredContent);
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
            reactive: true,
            x_expand: true,
        });
        entry.clutter_text.line_wrap = true;
        entry.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        entry.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        entry.clutter_text.single_line_mode = false;
        entry.clutter_text.editable = true;
        entry.clutter_text.activatable = true;
        const resizeEntry = () => {
            const text = entry.get_text();
            const estimatedLines = text.split('\n').reduce((count, line) =>
                count + Math.max(1, Math.ceil(line.length / 54)), 0);
            const lines = Math.min(4, Math.max(1, estimatedLines));
            entry.set_height(lines * 19 + 2);
        };
        entry.clutter_text.connect('text-changed', resizeEntry);
        composer.add_child(entry);
        resizeEntry();

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
        const confirmationIcon = new St.Icon({
            icon_name: 'security-high-symbolic',
            style_class: 'ai-confirmation-icon',
        });
        confirmationContent.add_child(confirmationIcon);
        const confirmationLabel = new St.Label({
            text: 'Подтверждать за меня',
            style_class: 'ai-confirmation-label',
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
        });
        confirmationContent.add_child(confirmationLabel);
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
        const modelLabel = new St.Label({
            text: 'Qwen 3.5 2B',
            style_class: 'ai-model-label',
            x_expand: true,
            x_align: Clutter.ActorAlign.END,
        });
        modelContent.add_child(modelLabel);
        const modelChevron = new St.Label({
            text: '⌄',
            style_class: 'ai-model-chevron',
        });
        modelContent.add_child(modelChevron);
        modelButton.set_child(modelContent);
        modelControl.add_child(modelButton);

        const modelMenu = new St.BoxLayout({vertical: true, style_class: 'ai-model-menu'});
        modelMenu.set_position(0, -116);
        modelMenu.set_size(145, 110);
        modelMenu.set_opacity(0);
        modelMenu.hide();
        const setModelMenuOpen = open => {
            modelChevron.ease({
                opacity: 0,
                duration: 90,
                onComplete: () => {
                    modelChevron.set_text(open ? '⌃' : '⌄');
                    modelChevron.ease({opacity: 255, duration: 90});
                },
            });
            if (open) {
                modelMenu.show();
                modelMenu.ease({opacity: 255, duration: 180});
            } else {
                modelMenu.ease({
                    opacity: 0,
                    duration: 140,
                    onComplete: () => modelMenu.hide(),
                });
            }
        };
        ['Qwen 3.5 2B', 'Gemma 2 2B', 'Qwen 3.5 4B'].forEach(model => {
            const option = new St.Button({label: model, style_class: 'ai-model-option'});
            option.connect('clicked', () => {
                modelLabel.set_text(model);
                setModelMenuOpen(false);
            });
            modelMenu.add_child(option);
        });
        modelControl.add_child(modelMenu);
        modelButton.connect('clicked', () => setModelMenuOpen(!modelMenu.visible));
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

});

const WorkspaceView = GObject.registerClass(
class WorkspaceView extends St.Widget {
    _init() {
        super._init({style_class: 'ai-empty-view', x_expand: true, y_expand: true});
    }
});

function sidebarLabel(text, styleClass, options = {}) {
    const params = {
        text,
        style_class: styleClass,
        x_expand: options.x_expand ?? false,
        y_expand: options.y_expand ?? false,
    };
    if (options.x_align !== undefined)
        params.x_align = options.x_align;
    if (options.y_align !== undefined)
        params.y_align = options.y_align;
    return new St.Label(params);
}

function metricRow(label, value, extraClass = '') {
    const row = new St.BoxLayout({
        style_class: `ai-sidebar-metric-row ${extraClass}`.trim(),
        x_expand: true,
    });
    row.add_child(sidebarLabel(label, 'ai-sidebar-metric-label', {x_expand: true}));
    row.add_child(sidebarLabel(value, 'ai-sidebar-metric-value'));
    return row;
}

function processRow(name, details) {
    const row = new St.BoxLayout({style_class: 'ai-process-row', x_expand: true});
    row.add_child(sidebarLabel(name, 'ai-process-name', {x_expand: true}));
    row.add_child(sidebarLabel(details, 'ai-process-details'));
    return row;
}

function cpuColor(value) {
    if (value >= 80)
        return [0.92, 0.27, 0.24, 1.0];
    if (value >= 60)
        return [0.96, 0.55, 0.18, 1.0];
    if (value >= 40)
        return [0.92, 0.78, 0.24, 1.0];
    return [0.42, 0.78, 0.45, 1.0];
}

function createMetricRing(value) {
    const wrap = new St.Widget({
        style_class: 'ai-metric-ring-wrap',
        layout_manager: new Clutter.BinLayout(),
    });
    wrap.set_size(94, 94);

    const drawing = new St.DrawingArea({style_class: 'ai-metric-ring'});
    drawing.set_size(94, 94);
    drawing.connect('repaint', area => {
        const [width, height] = area.get_surface_size();
        if (!width || !height)
            return;
        const context = area.get_context();
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) / 2 - 9;
        const start = -Math.PI / 2;
        const end = start + (Math.PI * 2 * value / 100);

        context.setLineWidth(8);
        context.setLineCap(1);
        context.setSourceRGBA(0.17, 0.17, 0.17, 0.95);
        context.arc(centerX, centerY, radius, 0, Math.PI * 2);
        context.stroke();

        context.setSourceRGBA(...cpuColor(value));
        context.arc(centerX, centerY, radius, start, end);
        context.stroke();
        context.$dispose();
    });
    wrap.add_child(drawing);

    const valueLabel = sidebarLabel(`${value}%`, 'ai-ring-value', {
        x_align: Clutter.ActorAlign.CENTER,
        y_align: Clutter.ActorAlign.CENTER,
    });
    wrap.add_child(valueLabel);
    return wrap;
}

const SidebarView = GObject.registerClass(
class SidebarView extends St.ScrollView {
    _init() {
        super._init({
            style_class: 'ai-sidebar-view',
            x_expand: true,
            y_expand: true,
        });
        this.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);

        const content = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-sidebar-content',
            x_expand: true,
        });
        this.set_child(content);

        content.add_child(this._buildStatusCard());
        content.add_child(this._buildTaskCard());
        content.add_child(this._buildActionsCard());
        content.add_child(this._buildHistoryButton());
    }

    _card(title) {
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-sidebar-card',
            x_expand: true,
        });
        card.add_child(sidebarLabel(title, 'ai-sidebar-title'));
        return card;
    }

    _buildStatusCard() {
        const card = this._card('Состояние системы');
        const main = new St.BoxLayout({style_class: 'ai-status-main', x_expand: true});
        main.add_child(createMetricRing(37));

        const details = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-status-details',
            x_expand: true,
        });
        details.add_child(sidebarLabel('Загрузка ЦП', 'ai-sidebar-kicker'));
        details.add_child(sidebarLabel('54°C', 'ai-sidebar-temperature'));
        details.add_child(metricRow('RAM', '62%'));
        details.add_child(sidebarLabel('4.9 из 7.8 ГБ · 46°C', 'ai-sidebar-caption'));
        main.add_child(details);
        card.add_child(main);

        card.add_child(sidebarLabel('Диски', 'ai-sidebar-kicker'));
        card.add_child(metricRow('/ · свободно', '42.1 ГБ · 38°C'));
        card.add_child(metricRow('D: · свободно', '118 ГБ · 41°C'));
        card.add_child(metricRow('Батарея', '82% · от батареи'));
        return card;
    }

    _buildTaskCard() {
        const card = this._card('Мини-диспетчер задач');
        card.add_child(processRow('Firefox', '12% · 820 МБ'));
        card.add_child(processRow('gnome-shell', '7% · 410 МБ'));
        card.add_child(processRow('Терминал', '3% · 160 МБ'));
        const button = new St.Button({
            label: 'Показать все процессы',
            style_class: 'ai-sidebar-action',
            x_align: Clutter.ActorAlign.START,
        });
        card.add_child(button);
        return card;
    }

    _buildActionsCard() {
        const card = this._card('Быстрые системные действия');
        ['Проверить состояние', 'Открыть ошибки служб', 'Открыть настройки сети'].forEach(label => {
            card.add_child(new St.Button({
                label,
                style_class: 'ai-sidebar-action ai-sidebar-action-wide',
                x_expand: true,
            }));
        });
        return card;
    }

    _buildHistoryButton() {
        return new St.Button({
            label: 'Последние действия',
            style_class: 'ai-sidebar-history',
            x_expand: true,
        });
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
        this._collapsedTranslation = PANEL_WIDTH + PANEL_HORIZONTAL_MARGIN;

        this._content = new St.Widget({
            style_class: 'ai-panel-content',
            layout_manager: new Clutter.FixedLayout(),
        });
        this._content.set_position(TOGGLE_WIDTH, 0);
        this._content.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT);
        this.add_child(this._content);

        this._toggle = new St.Button({style_class: 'ai-toggle', can_focus: true});
        this._toggleLabel = new St.Label({
            text: '›',
            style_class: 'ai-toggle-label',
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._toggle.set_child(this._toggleLabel);
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
        [this._sidebar, this._workspace, this._settings].forEach(view => {
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

        this._sidebar = new SidebarView();
        this._sidebar.set_position(0, 0);
        this._sidebar.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._views.add_child(this._sidebar);
        this._workspace = new ChatView(this._runtime);
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
            new TabButton('Боковая панель', 'view-sidebar-symbolic'),
            new TabButton('Рабочая область', 'folder-symbolic'),
            new TabButton('Настройки', 'preferences-system-symbolic'),
        ];
        const tabWidth = Math.floor((PANEL_WIDTH - 14) / this._tabButtons.length);
        this._tabButtons.forEach((button, index) => {
            button.set_width(tabWidth);
            button.set_height(TAB_HEIGHT - 3);
            button.connect('clicked', () => this._selectTab(index));
            this._tabs.add_child(button);
        });
        this._selectTab(1);
        this.setPanelHeight(DEFAULT_PANEL_HEIGHT);
    }

    _selectTab(index) {
        const views = [this._sidebar, this._workspace, this._settings];
        views.forEach((view, viewIndex) => view.visible = viewIndex === index);
        this._tabButtons.forEach((button, buttonIndex) => button.setActive(buttonIndex === index));
    }

    _togglePanel() {
        this._collapsed = !this._collapsed;
        this._toggleLabel.set_text(this._collapsed ? '‹' : '›');
        const target = this._collapsed ? this._collapsedTranslation : 0;
        const toggleTarget = this._collapsed ? -4 : 0;
        this.ease({
            translation_x: target,
            duration: TOGGLE_DURATION,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                // Keep the final position exact even if Shell interrupts the
                // transition during a monitor/layout update.
                this.translation_x = target;
            },
        });
        this._toggle.ease({
            translation_x: toggleTarget,
            duration: TOGGLE_DURATION,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                this._toggle.translation_x = toggleTarget;
            },
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
            monitor.x + monitor.width - PANEL_WIDTH - PANEL_HORIZONTAL_MARGIN - TOGGLE_WIDTH,
            monitor.y + monitor.height - height - PANEL_BOTTOM_MARGIN,
        );
        // Keep only the toggle visible when collapsed.  The extra margin is
        // intentional: translating by PANEL_WIDTH alone leaves the panel's
        // right margin visible on every resolution.
        this._collapsedTranslation = PANEL_WIDTH + PANEL_HORIZONTAL_MARGIN;
        this._panel.translation_x = this._collapsed ? this._collapsedTranslation : 0;
        this._panel._toggle.translation_x = this._collapsed ? -4 : 0;
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
