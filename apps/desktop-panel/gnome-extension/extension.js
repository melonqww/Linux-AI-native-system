import St from 'gi://St';
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const PANEL_WIDTH = 460;
const PANEL_HEIGHT = 560;
const TOGGLE_WIDTH = 28;
const PANEL_MARGIN = 24;

const TabButton = GObject.registerClass(
class TabButton extends St.Button {
    _init(label, icon) {
        super._init({
            style_class: 'ai-tab',
            can_focus: true,
            x_expand: true,
            label: `${icon}  ${label}`,
        });
    }
});

const ChatView = GObject.registerClass(
class ChatView extends St.BoxLayout {
    _init() {
        super._init({
            vertical: true,
            style_class: 'ai-chat-view',
            x_expand: true,
            y_expand: true,
        });

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
        // GNOME 46 exposes ScrollView as a single-child container.
        this._scroll.set_child(this._messages);
        this.add_child(this._scroll);

        this._composer = this._buildComposer();
        this.add_child(this._composer);
    }

    _addInitialMessages() {
        const intro = new St.Label({
            text: 'Привет. Я локальный помощник Ubuntu. Могу показать состояние системы, подготовить workspace или объяснить ошибку.',
            style_class: 'ai-assistant-message',
            x_expand: true,
        });
        intro.clutter_text.line_wrap = true;
        this._messages.add_child(intro);

        this._messages.add_child(new St.Button({
            label: 'Покажи состояние системы',
            style_class: 'ai-user-message',
            x_align: Clutter.ActorAlign.END,
        }));

        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-plan-card',
            x_expand: true,
        });
        card.add_child(new St.Label({
            text: 'БЕЗОПАСНЫЙ ПЛАН · R0',
            style_class: 'ai-plan-title',
        }));
        const details = new St.Label({
            text: 'Прочитаю информацию о диске, памяти и загрузке CPU. Ничего в системе не изменится.',
            style_class: 'ai-plan-details',
            x_expand: true,
        });
        details.clutter_text.line_wrap = true;
        card.add_child(details);
        card.add_child(new St.Button({
            label: 'Запустить проверку',
            style_class: 'ai-plan-action',
            x_align: Clutter.ActorAlign.START,
        }));
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
        composer.add_child(entry);

        const actions = new St.BoxLayout({
            style_class: 'ai-composer-actions',
            x_expand: true,
        });
        actions.add_child(new St.Label({
            text: '◈  Подтверждать за меня',
            style_class: 'ai-confirmation',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        actions.add_child(new St.Button({
            label: 'Qwen 3.5 2B⌄',
            style_class: 'ai-model',
        }));
        const send = new St.Button({
            label: '↑',
            style_class: 'ai-send',
            can_focus: true,
        });
        send.connect('clicked', () => {
            const text = entry.get_text().trim();
            if (!text)
                return;
            this._messages.add_child(new St.Button({
                label: text,
                style_class: 'ai-user-message',
                x_align: Clutter.ActorAlign.END,
            }));
            entry.set_text('');
        });
        actions.add_child(send);
        composer.add_child(actions);
        return composer;
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
    _init() {
        super._init({
            style_class: 'ai-native-shell',
            reactive: true,
            layout_manager: new Clutter.FixedLayout(),
        });
        this.set_size(PANEL_WIDTH, PANEL_HEIGHT);
        this._collapsed = false;

        this._content = new St.Widget({
            style_class: 'ai-panel-content',
            layout_manager: new Clutter.FixedLayout(),
        });
        this._content.set_position(TOGGLE_WIDTH, 0);
        this._content.set_size(PANEL_WIDTH - TOGGLE_WIDTH, PANEL_HEIGHT);
        this.add_child(this._content);

        this._toggle = new St.Button({
            style_class: 'ai-toggle',
            label: '>',
            can_focus: true,
        });
        this._toggle.set_size(TOGGLE_WIDTH, 42);
        this._toggle.set_position(0, Math.floor((PANEL_HEIGHT - 42) / 2));
        this._toggle.connect('clicked', () => this._togglePanel());
        this.add_child(this._toggle);

        this._buildTabs();
    }

    _buildTabs() {
        this._tabs = new St.BoxLayout({style_class: 'ai-tabs', x_expand: true});
        this._tabs.set_position(0, 0);
        this._tabs.set_size(PANEL_WIDTH - TOGGLE_WIDTH, 44);
        this._content.add_child(this._tabs);

        this._views = new St.Widget({layout_manager: new Clutter.FixedLayout()});
        this._views.set_position(0, 44);
        this._views.set_size(PANEL_WIDTH - TOGGLE_WIDTH, PANEL_HEIGHT - 44);
        this._content.add_child(this._views);

        this._chat = new ChatView();
        this._chat.set_position(0, 0);
        this._chat.set_size(PANEL_WIDTH - TOGGLE_WIDTH, PANEL_HEIGHT - 44);
        this._views.add_child(this._chat);

        this._workspace = new WorkspaceView();
        this._workspace.set_position(0, 0);
        this._workspace.set_size(PANEL_WIDTH - TOGGLE_WIDTH, PANEL_HEIGHT - 44);
        this._workspace.hide();
        this._views.add_child(this._workspace);

        this._settings = new WorkspaceView();
        this._settings.set_position(0, 0);
        this._settings.set_size(PANEL_WIDTH - TOGGLE_WIDTH, PANEL_HEIGHT - 44);
        this._settings.hide();
        this._views.add_child(this._settings);

        this._tabButtons = [
            new TabButton('Боковая панель', '☰'),
            new TabButton('Рабочая область', '▣'),
            new TabButton('Настройки', '⚙'),
        ];
        this._tabButtons.forEach((button, index) => {
            button.connect('clicked', () => this._selectTab(index));
            this._tabs.add_child(button);
        });
        this._selectTab(0);
    }

    _selectTab(index) {
        const views = [this._chat, this._workspace, this._settings];
        views.forEach((view, viewIndex) => view.visible = viewIndex === index);
        this._tabButtons.forEach((button, buttonIndex) => {
            if (buttonIndex === index)
                button.add_style_class_name('active');
            else
                button.remove_style_class_name('active');
        });
    }

    _togglePanel() {
        this._collapsed = !this._collapsed;
        this._toggle.set_label(this._collapsed ? '<' : '>');
        this.ease({
            translation_x: this._collapsed ? PANEL_WIDTH - TOGGLE_WIDTH : 0,
            duration: 420,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }
});

export default class AiNativeLinuxExtension extends Extension {
    enable() {
        this._panel = new Panel();
        Main.layoutManager.addChrome(this._panel, {
            trackFullscreen: false,
            affectsStruts: false,
        });
        this._monitorChangedId = Main.layoutManager.connect('monitors-changed', () => this._positionPanel());
        this._positionPanel();
    }

    _positionPanel() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._panel)
            return;
        this._panel.set_position(
            monitor.x + monitor.width - PANEL_WIDTH - PANEL_MARGIN,
            monitor.y + monitor.height - PANEL_HEIGHT - PANEL_MARGIN,
        );
    }

    disable() {
        if (this._monitorChangedId) {
            Main.layoutManager.disconnect(this._monitorChangedId);
            this._monitorChangedId = 0;
        }
        this._panel?.destroy();
        this._panel = null;
    }
}
