import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {RuntimeClient, RuntimeRequestError} from './runtime-client.js';
import {
    approvalPresentation,
    compilationMessage,
    executionPresentation,
    monitorPresentation,
    runtimeErrorMessage,
    systemPresentation,
    systemUpdatePresentation,
    taskDetailPresentation,
    taskRowLabel,
} from './panel-presenter.js';

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
// The runtime's real Ollama model is qwen3:1.7b.  Keep the human-readable
// name here in sync with that backend default; model switching is not exposed
// until the runtime supports selecting a different model per run.
const WORKSPACE_MODEL_LABEL = 'Qwen 3 1.7B';
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
    _init(runtime, onTaskLedger = null) {
        super._init({
            vertical: true,
            style_class: 'ai-chat-view',
            x_expand: true,
            y_expand: true,
        });

        this._runtime = runtime;
        this._onTaskLedger = onTaskLedger;
        this._busy = false;
        this._disposed = false;
        this._workspaceRunId = null;
        this._workspacePollSourceId = 0;
        this._workspacePollInFlight = false;
        this._workspaceRunStages = new Map();
        this._workspacePollErrorShown = false;
        this._modelCatalogPollSourceId = 0;
        this._inferenceLifecycleInFlight = false;
        this._inferencePollDelayMs = 15_000;
        this._modelCatalog = null;
        this._providerStatus = null;
        this._baseModelPromptInFlight = false;
        this._baseModelPromptShown = false;
        this.connect('destroy', () => {
            this._disposed = true;
            if (this._workspacePollSourceId) {
                GLib.Source.remove(this._workspacePollSourceId);
                this._workspacePollSourceId = 0;
            }
            if (this._modelCatalogPollSourceId) {
                GLib.Source.remove(this._modelCatalogPollSourceId);
                this._modelCatalogPollSourceId = 0;
            }
        });
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

        this._dependencyNotices = this._buildDependencyNotices();
        this.add_child(this._dependencyNotices);

        this._composer = this._buildComposer();
        this.add_child(this._composer);
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._loadWorkspace();
            this._loadInferenceLifecycle();
            return GLib.SOURCE_REMOVE;
        });
    }

    _buildDependencyNotices() {
        const stack = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-dependency-stack',
            x_expand: true,
        });
        stack.hide();
        return stack;
    }

    _dependencyReason(reason) {
        const labels = {
            archive_digest_mismatch: 'не совпала контрольная сумма архива',
            archive_missing_executable: 'в архиве отсутствует исполняемый файл',
            download_incomplete: 'загрузка была прервана',
            insufficient_disk_space: 'недостаточно места на диске',
            install_failed: 'установщик завершился с ошибкой',
            install_interrupted: 'установка была прервана',
            platform_unsupported: 'эта платформа не поддерживается',
            provider_status_unavailable: 'модуль Ollama не ответил',
            model_catalog_unavailable: 'каталог Qwen и LLaMA не ответил',
            model_status_missing: 'модель отсутствует в ответе каталога',
            release_asset_missing: 'файл релиза не найден',
            release_digest_missing: 'у релиза отсутствует контрольная сумма',
            unsafe_download_redirect: 'получен небезопасный адрес загрузки',
            user_decision_required: 'требуется решение пользователя',
        };
        if (typeof reason !== 'string' || !reason)
            return 'неизвестная ошибка';
        return labels[reason] ?? reason.split('_').join(' ');
    }

    _errorNotice(title, body) {
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-dependency-notice ai-dependency-error',
            x_expand: true,
        });
        card.add_child(new St.Label({
            text: title,
            style_class: 'ai-dependency-title ai-dependency-error-title',
            x_expand: true,
        }));
        const description = new St.Label({
            text: body,
            style_class: 'ai-dependency-body',
            x_expand: true,
        });
        description.clutter_text.line_wrap = true;
        description.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        card.add_child(description);
        const actions = new St.BoxLayout({
            style_class: 'ai-dependency-actions',
            x_expand: true,
        });
        const okay = new St.Button({
            label: 'Хорошо',
            style_class: 'ai-dependency-ok',
            x_expand: false,
        });
        okay.connect('clicked', () => card.hide());
        actions.add_child(new St.Widget({style_class: 'ai-dependency-spacer', x_expand: true}));
        actions.add_child(okay);
        card.add_child(actions);
        return card;
    }

    _showDependencyError(card, title, body) {
        card.hide();
        this._dependencyNotices.add_child(this._errorNotice(title, body));
        this._dependencyNotices.show();
    }

    _dependencyNotice(model) {
        const displayName = typeof model.display_name === 'string'
            ? model.display_name
            : model.provider_model;
        const providerModel = typeof model.provider_model === 'string'
            ? model.provider_model
            : '';
        const state = typeof model.state === 'string' ? model.state : 'consent_required';
        const reason = typeof model.reason === 'string' ? model.reason : '';
        const isDownloading = state === 'starting' || state === 'downloading';
        const isOllamaMissing = reason === 'ollama_not_installed';
        let body;
        if (isOllamaMissing) {
            body = `Для загрузки ${displayName} нужна Ollama. Установите Ollama и повторите проверку.`;
        } else if (isDownloading) {
            const progress = Number.isInteger(model.progress_percent)
                ? ` ${model.progress_percent}%`
                : '';
            body = `Загрузка модели ${displayName} выполняется в фоне.${progress}`;
        } else if (state === 'error') {
            return this._errorNotice(
                'Ошибка загрузки модели',
                `Не удалось загрузить ${displayName}.\nПричина: ${this._dependencyReason(reason)}.`,
            );
        } else {
            body = `Модель ${displayName}${providerModel ? ` (${providerModel})` : ''} не установлена. Загрузить её автоматически?`;
        }
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-dependency-notice',
            x_expand: true,
        });
        card.add_child(new St.Label({
            text: 'Внимание',
            style_class: 'ai-dependency-title',
            x_expand: true,
        }));
        const description = new St.Label({
            text: body,
            style_class: 'ai-dependency-body',
            x_expand: true,
        });
        description.clutter_text.line_wrap = true;
        description.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        card.add_child(description);

        const actions = new St.BoxLayout({
            style_class: 'ai-dependency-actions',
            x_expand: true,
        });
        const hide = new St.Button({
            label: 'Скрыть',
            style_class: 'ai-dependency-hide',
        });
        const neverShow = new St.CheckButton({
            label: 'Не показывать',
            style_class: 'ai-dependency-check',
            can_focus: true,
        });
        const install = new St.Button({
            label: isOllamaMissing ? 'Нужна Ollama' : isDownloading ? 'Загрузка…' : 'Загрузить',
            style_class: 'ai-dependency-install',
        });
        const setBusy = busy => {
            hide.reactive = !busy;
            neverShow.reactive = !busy;
            install.reactive = !busy && !isOllamaMissing;
        };
        const respond = async decision => {
            setBusy(true);
            try {
                await this._runtime.respondToModel(model.model_id, decision);
                if (model.model_id === 'workspace.qwen')
                    this._baseModelPromptShown = false;
                card.hide();
                this._loadInferenceLifecycle();
            } catch (error) {
                this._showDependencyError(
                    card,
                    'Ошибка загрузки модели',
                    `Не удалось сохранить решение для ${displayName}.\nПричина: ${this._friendlyError(error)}.`,
                );
            }
        };
        hide.connect('clicked', () => respond('later'));
        neverShow.connect('clicked', () => {
            if (neverShow.checked)
                respond('never');
        });
        install.connect('clicked', () => respond('download'));
        if (isDownloading)
            setBusy(true);
        actions.add_child(hide);
        actions.add_child(neverShow);
        actions.add_child(new St.Widget({style_class: 'ai-dependency-spacer', x_expand: true}));
        actions.add_child(install);
        card.add_child(actions);
        return card;
    }

    _providerNotice(status) {
        const state = typeof status.state === 'string' ? status.state : 'consent_required';
        const reason = typeof status.reason === 'string' ? status.reason : '';
        const isInstalling = state === 'downloading' || state === 'installing';
        if (state === 'error' || state === 'unsupported') {
            return this._errorNotice(
                'Ошибка установки Ollama',
                `Не удалось подготовить Ollama.\nПричина: ${this._dependencyReason(reason)}.`,
            );
        }
        let body;
        if (isInstalling) {
            const progress = Number.isInteger(status.progress_percent)
                ? ` ${status.progress_percent}%`
                : '';
            body = `Установка Ollama выполняется в фоне.${progress}`;
        } else {
            body = 'Ollama не установлена. Установить её автоматически для загрузки локальных моделей?';
        }
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-dependency-notice',
            x_expand: true,
        });
        card.add_child(new St.Label({
            text: 'Внимание',
            style_class: 'ai-dependency-title',
            x_expand: true,
        }));
        const description = new St.Label({
            text: body,
            style_class: 'ai-dependency-body',
            x_expand: true,
        });
        description.clutter_text.line_wrap = true;
        description.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        card.add_child(description);
        const actions = new St.BoxLayout({
            style_class: 'ai-dependency-actions',
            x_expand: true,
        });
        const hide = new St.Button({label: 'Скрыть', style_class: 'ai-dependency-hide'});
        const neverShow = new St.CheckButton({
            label: 'Не показывать',
            style_class: 'ai-dependency-check',
            can_focus: true,
        });
        const install = new St.Button({
            label: isInstalling ? 'Установка…' : 'Установить Ollama',
            style_class: 'ai-dependency-install',
        });
        const setBusy = busy => {
            hide.reactive = !busy;
            neverShow.reactive = !busy;
            install.reactive = !busy;
        };
        const respond = async decision => {
            setBusy(true);
            try {
                await this._runtime.respondToOllamaProvider(decision);
                card.hide();
                this._loadInferenceLifecycle();
            } catch (error) {
                this._showDependencyError(
                    card,
                    'Ошибка установки Ollama',
                    `Не удалось сохранить решение для Ollama.\nПричина: ${this._friendlyError(error)}.`,
                );
            }
        };
        hide.connect('clicked', () => respond('later'));
        neverShow.connect('clicked', () => {
            if (neverShow.checked)
                respond('never');
        });
        install.connect('clicked', () => respond('install'));
        if (isInstalling)
            setBusy(true);
        actions.add_child(hide);
        actions.add_child(neverShow);
        actions.add_child(new St.Widget({style_class: 'ai-dependency-spacer', x_expand: true}));
        actions.add_child(install);
        card.add_child(actions);
        return card;
    }

    _renderDependencyNotices() {
        const models = Array.isArray(this._modelCatalog?.models) ? this._modelCatalog.models : [];
        const visibleCards = [];
        const provider = this._providerStatus;
        const providerReady = !provider || provider.installed === true ||
            provider.state === 'ready';
        if (provider && (provider.prompt_required === true ||
            ['downloading', 'installing', 'error', 'unsupported'].includes(provider.state))) {
            visibleCards.push(this._providerNotice(provider));
        }
        const visibleModels = providerReady ? models.filter(model => {
            if (!model || typeof model !== 'object' || typeof model.model_id !== 'string')
                return false;
            if (model.reason === 'ollama_not_installed')
                return false;
            if (model.effective_state === 'blocked')
                return false;
            const promptRequired = typeof model.effective_prompt_required === 'boolean'
                ? model.effective_prompt_required
                : model.prompt_required === true;
            if (promptRequired)
                return true;
            const state = typeof model.effective_state === 'string'
                ? model.effective_state
                : model.state;
            return ['starting', 'downloading', 'error', 'unavailable'].includes(state);
        }) : [];
        for (const model of visibleModels)
            visibleCards.push(this._dependencyNotice(model));
        this._dependencyNotices.destroy_all_children();
        for (const card of visibleCards)
            this._dependencyNotices.add_child(card);
        if (visibleCards.length > 0)
            this._dependencyNotices.show();
        else
            this._dependencyNotices.hide();

        this._scheduleModelCatalogPoll();
    }

    _renderModelCatalog(catalog) {
        this._modelCatalog = this._baseModelPromptShown
            ? this._withBaseModelPrompt(catalog)
            : catalog;
        this._renderDependencyNotices();
    }

    _scheduleModelCatalogPoll() {
        if (this._disposed || this._modelCatalogPollSourceId)
            return;
        this._modelCatalogPollSourceId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            this._inferencePollDelayMs,
            () => {
                this._modelCatalogPollSourceId = 0;
                if (this._disposed)
                    return GLib.SOURCE_REMOVE;
                this._loadInferenceLifecycle();
                return GLib.SOURCE_REMOVE;
            },
        );
    }

    async _loadInferenceLifecycle() {
        if (this._modelCatalogPollSourceId) {
            GLib.Source.remove(this._modelCatalogPollSourceId);
            this._modelCatalogPollSourceId = 0;
        }
        if (this._inferenceLifecycleInFlight || this._disposed)
            return;
        this._inferenceLifecycleInFlight = true;
        try {
            const snapshot = await this._runtime.inferenceStatus();
            if (this._disposed)
                return;
            this._providerStatus = snapshot?.provider ?? null;
            this._inferencePollDelayMs = [
                'provider_preparing',
                'models_preparing',
            ].includes(snapshot?.state) ? 1_000 : 15_000;
            const catalog = {
                schema_version: snapshot?.schema_version ?? 1,
                models: Array.isArray(snapshot?.models) ? snapshot.models : [],
            };
            this._modelCatalog = this._baseModelPromptShown
                ? this._withBaseModelPrompt(catalog)
                : catalog;
            this._renderDependencyNotices();
        } catch (error) {
            if (this._disposed)
                return;
            this._dependencyNotices.destroy_all_children();
            this._dependencyNotices.add_child(this._errorNotice(
                'Ошибка проверки локального ИИ',
                `Не удалось получить состояние Ollama, Qwen и LLaMA.\nПричина: ${this._friendlyError(error)}.`,
            ));
            this._dependencyNotices.show();
            this._inferencePollDelayMs = 5_000;
            this._scheduleModelCatalogPoll();
        } finally {
            this._inferenceLifecycleInFlight = false;
        }
    }

    _isBaseModelNotice(message) {
        if (!message || typeof message.content !== 'string')
            return false;
        return /базовая модель|base model/i.test(message.content);
    }

    _withBaseModelPrompt(catalog) {
        const models = Array.isArray(catalog?.models)
            ? catalog.models.map(model => ({...model}))
            : [];
        const qwen = models.find(model => model?.model_id === 'workspace.qwen');
        if (qwen) {
            qwen.prompt_required = true;
            qwen.state = 'consent_required';
            qwen.decision = 'unset';
            qwen.reason = 'user_decision_required';
        } else {
            models.unshift({
                model_id: 'workspace.qwen',
                provider: 'ollama',
                provider_model: 'qwen3:1.7b',
                display_name: WORKSPACE_MODEL_LABEL,
                required: true,
                prompt_required: true,
                state: 'consent_required',
                decision: 'unset',
                reason: 'user_decision_required',
            });
        }
        return {...(catalog ?? {schema_version: 1}), models};
    }

    async _revealBaseModelPrompt() {
        if (this._baseModelPromptShown || this._baseModelPromptInFlight || this._disposed)
            return;
        this._baseModelPromptShown = true;
        this._baseModelPromptInFlight = true;
        try {
            const lifecycleResult = await Promise.allSettled([
                this._runtime.inferenceStatus(),
            ]);
            if (this._disposed)
                return;
            if (lifecycleResult[0].status === 'fulfilled') {
                const snapshot = lifecycleResult[0].value;
                this._providerStatus = snapshot?.provider ?? null;
                this._modelCatalog = {
                    schema_version: snapshot?.schema_version ?? 1,
                    models: Array.isArray(snapshot?.models) ? snapshot.models : [],
                };
            }

            this._modelCatalog = this._withBaseModelPrompt(this._modelCatalog);
            this._renderDependencyNotices();
        } finally {
            this._baseModelPromptInFlight = false;
        }
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
        // Shell chrome does not always route keyboard focus to an St.Entry
        // after a pointer click.  Explicitly grabbing focus keeps the field
        // editable even when the panel was opened over another application.
        const focusEntry = () => {
            entry.grab_key_focus();
            entry.clutter_text.set_cursor_position(-1);
        };
        entry.connect('button-press-event', () => {
            focusEntry();
            return Clutter.EVENT_PROPAGATE;
        });
        entry.connect('key-focus-in', () => entry.add_style_class_name('focused'));
        entry.connect('key-focus-out', () => entry.remove_style_class_name('focused'));
        const entryScroll = new St.ScrollView({
            style_class: 'ai-entry-scroll',
            x_expand: true,
        });
        entryScroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);
        const entryScrollContent = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-entry-scroll-content',
            x_expand: true,
        });
        const resizeEntry = () => {
            const text = entry.get_text();
            const estimatedLines = text.split('\n').reduce((count, line) =>
                count + Math.max(1, Math.ceil(line.length / 54)), 0);
            const desiredHeight = Math.max(20, estimatedLines * 19 + 2);
            entry.set_height(desiredHeight);
            entryScrollContent.set_height(desiredHeight);
            entryScroll.set_height(Math.min(78, desiredHeight));
        };
        entry.clutter_text.connect('text-changed', resizeEntry);
        entryScrollContent.add_child(entry);
        entryScroll.set_child(entryScrollContent);
        const entryAdjustment = entryScroll.get_vadjustment();
        const keepEntryAtEnd = () => {
            const lower = Number(entryAdjustment.lower ?? 0);
            const upper = Number(entryAdjustment.upper ?? 0);
            const pageSize = Number(entryAdjustment.page_size ?? 0);
            const bottom = Math.max(
                lower,
                upper - pageSize,
            );
            entryAdjustment.value = bottom;
        };
        entryAdjustment.connect('notify::upper', keepEntryAtEnd);
        entryAdjustment.connect('notify::page-size', keepEntryAtEnd);
        composer.add_child(entryScroll);
        resizeEntry();
        keepEntryAtEnd();

        const actions = new St.BoxLayout({style_class: 'ai-composer-actions', x_expand: true});

        const modelControl = new St.Widget({
            style_class: 'ai-model-control',
            layout_manager: new Clutter.FixedLayout(),
        });
        modelControl.set_size(145, 34);
        const modelButton = new St.Button({
            style_class: 'ai-model ai-model-fixed',
            can_focus: false,
            reactive: false,
        });
        modelButton.set_size(145, 34);
        const modelContent = new St.BoxLayout({
            style_class: 'ai-model-content',
            x_expand: true,
        });
        const modelLabel = new St.Label({
            text: WORKSPACE_MODEL_LABEL,
            style_class: 'ai-model-label',
            x_expand: true,
            x_align: Clutter.ActorAlign.END,
        });
        modelContent.add_child(modelLabel);
        modelButton.set_child(modelContent);
        modelControl.add_child(modelButton);
        actions.add_child(new St.Widget({style_class: 'ai-composer-spacer', x_expand: true}));
        actions.add_child(modelControl);

        this._send = new St.Button({label: '↑', style_class: 'ai-send', can_focus: true});
        this._send.connect('clicked', () => this._submitEntry(entry));
        entry.clutter_text.connect('activate', () => this._submitEntry(entry));
        actions.add_child(this._send);
        composer.add_child(actions);
        return composer;
    }

    async _submitEntry(entry) {
        const text = entry.get_text().trim();
        if (!text || this._busy)
            return;
        this._append(this._user(text));
        entry.set_text('');
        this._setBusy(entry, true);
        try {
            const run = await this._runtime.workspaceSubmit(text);
            if (this._disposed)
                return;
            this._workspaceRunId = run.run_id;
            this._rememberWorkspaceRun(run);
            this._workspacePollErrorShown = false;
            this._startWorkspacePolling(entry);
        } catch (error) {
            if (!this._disposed) {
                this._append(this._assistant(this._friendlyError(error)));
                this._setBusy(entry, false);
            }
        }
    }

    async _loadWorkspace() {
        try {
            const [messages, runs] = await Promise.all([
                this._runtime.workspaceMessages(),
                this._runtime.workspaceRuns(100, false),
            ]);
            if (this._disposed)
                return;
            this._rememberWorkspaceRuns(runs.runs ?? []);
            const active = (runs.runs ?? []).find(run => !this._isTerminalStage(run.stage));
            this._workspaceRunId = active?.run_id ?? null;
            this._renderWorkspaceMessages(messages.messages ?? [], active);
            if (active)
                this._startWorkspacePolling(null);
        } catch (error) {
            if (!this._disposed)
                this._append(this._assistant(this._friendlyError(error)));
        }
    }

    _startWorkspacePolling(entry) {
        if (entry)
            this._workspaceEntry = entry;
        if (this._workspacePollSourceId)
            return;
        this._workspacePollSourceId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            700,
            () => {
                if (this._disposed || !this._workspaceRunId) {
                    this._workspacePollSourceId = 0;
                    return GLib.SOURCE_REMOVE;
                }
                this._pollWorkspace();
                return GLib.SOURCE_CONTINUE;
            },
        );
        this._pollWorkspace();
    }

    async _pollWorkspace() {
        if (this._workspacePollInFlight || this._disposed || !this._workspaceRunId)
            return;
        this._workspacePollInFlight = true;
        try {
            const [run, messages] = await Promise.all([
                this._runtime.workspaceRun(this._workspaceRunId),
                this._runtime.workspaceMessages(),
            ]);
            if (this._disposed)
                return;
            this._rememberWorkspaceRun(run);
            this._renderWorkspaceMessages(messages.messages ?? [], run);
            if (this._isTerminalStage(run.stage)) {
                this._workspaceRunId = null;
                if (this._workspacePollSourceId) {
                    GLib.Source.remove(this._workspacePollSourceId);
                    this._workspacePollSourceId = 0;
                }
                this._setBusy(this._workspaceEntry, false);
                this._workspaceEntry = null;
            }
        } catch (error) {
            if (!this._disposed && !this._workspacePollErrorShown) {
                this._workspacePollErrorShown = true;
                this._append(this._assistant(
                    this._friendlyError(error),
                    'ai-assistant-message ai-work-status',
                ));
            }
        } finally {
            this._workspacePollInFlight = false;
        }
    }

    _renderWorkspaceMessages(messages, run = null) {
        this._messages.destroy_all_children();
        for (const message of messages) {
            if (message.role === 'user') {
                this._messages.add_child(this._user(message.content));
                continue;
            }
            if (message.role === 'system' || message.kind === 'notice') {
                if (this._isBaseModelNotice(message))
                    this._revealBaseModelPrompt();
                this._messages.add_child(this._assistant(
                    message.content,
                    'ai-assistant-message ai-work-status',
                ));
                continue;
            }
            if (message.kind === 'task_result' && message.task_id) {
                const taskStage = run?.task_id === message.task_id
                    ? run.stage
                    : this._workspaceRunStages.get(message.task_id);
                this._messages.add_child(this._taskResult(
                    message.content,
                    message.task_id,
                    taskStage === 'completed',
                ));
                continue;
            }
            this._messages.add_child(this._assistant(message.content));
        }
        if (run && !this._isTerminalStage(run.stage)) {
            this._messages.add_child(this._assistant(
                run.stage_label || this._stageLabel(run.stage),
                'ai-assistant-message ai-work-status ai-pending',
            ));
            if (run.stage === 'awaiting_approval' && run.approval_request_id)
                this._messages.add_child(this._workspaceApprovalCard(run.approval_request_id));
        }
        this._scrollToBottom();
    }

    _taskResult(text, taskId, completed = false) {
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-task-result',
            x_expand: true,
        });
        card.add_child(this._assistant(text));
        if (completed && this._onTaskLedger) {
            const button = new St.Button({
                label: 'Открыть Task Ledger',
                style_class: 'ai-task-ledger-button',
                x_align: Clutter.ActorAlign.START,
            });
            button.connect('clicked', () => this._onTaskLedger(taskId));
            card.add_child(button);
        }
        return card;
    }

    _rememberWorkspaceRun(run) {
        if (!run || typeof run !== 'object')
            return;
        if (run.task_id)
            this._workspaceRunStages.set(run.task_id, run.stage);
    }

    _rememberWorkspaceRuns(runs) {
        for (const run of runs)
            this._rememberWorkspaceRun(run);
    }

    _workspaceApprovalCard(approvalRequestId) {
        const card = new St.BoxLayout({style_class: 'ai-plan-actions'});
        const confirm = new St.Button({label: 'Подтвердить', style_class: 'ai-plan-action'});
        const cancel = new St.Button({label: 'Отменить', style_class: 'ai-plan-action ai-plan-cancel'});
        const respond = async confirmed => {
            confirm.reactive = false;
            cancel.reactive = false;
            try {
                await this._runtime.workspaceApproval(approvalRequestId, confirmed);
                this._startWorkspacePolling(this._workspaceEntry);
            } catch (error) {
                if (!this._disposed)
                    this._append(this._assistant(this._friendlyError(error)));
            }
        };
        confirm.connect('clicked', () => respond(true));
        cancel.connect('clicked', () => respond(false));
        card.add_child(confirm);
        card.add_child(cancel);
        return card;
    }

    _scrollToBottom() {
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            if (!this._disposed) {
                const adjustment = this._scroll.get_vadjustment();
                adjustment.value = Math.max(
                    Number(adjustment.lower ?? 0),
                    Number(adjustment.upper ?? 0) - Number(adjustment.page_size ?? 0),
                );
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _isTerminalStage(stage) {
        return ['completed', 'failed', 'cancelled'].includes(stage);
    }

    _stageLabel(stage) {
        return {
            received: 'Запрос получен',
            understanding: 'Понимаю запрос…',
            planning: 'Создаю план…',
            executing: 'Выполняю задачу…',
            awaiting_approval: 'Ожидаю подтверждение…',
            summarizing: 'Формирую итог…',
        }[stage] ?? 'Обрабатываю запрос…';
    }

    _append(actor) {
        if (this._disposed)
            return;
        this._messages.add_child(actor);
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            if (!this._disposed) {
                const adjustment = this._scroll.get_vadjustment();
                adjustment.value = Math.max(
                    Number(adjustment.lower ?? 0),
                    Number(adjustment.upper ?? 0) - Number(adjustment.page_size ?? 0),
                );
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _setBusy(entry, busy) {
        this._busy = busy;
        this._send.reactive = !busy;
        if (entry)
            entry.reactive = !busy;
    }

    _renderExecution(result) {
        const presentation = executionPresentation(result);
        if (presentation.kind === 'approval') {
            this._append(this._approvalCard(presentation.request));
            return;
        }
        this._append(this._assistant(presentation.text));
    }

    _approvalCard(request) {
        const card = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-plan-card',
            x_expand: true,
        });
        const presentation = approvalPresentation(request);
        card.add_child(this._assistant(presentation.title, 'ai-plan-title'));
        card.add_child(this._assistant(presentation.details, 'ai-plan-details'));

        const actions = new St.BoxLayout({style_class: 'ai-plan-actions'});
        const confirm = new St.Button({label: presentation.confirmLabel, style_class: 'ai-plan-action'});
        const cancel = new St.Button({label: presentation.cancelLabel, style_class: 'ai-plan-action ai-plan-cancel'});
        const respond = async confirmed => {
            confirm.reactive = false;
            cancel.reactive = false;
            try {
                const result = await this._runtime.respondToApproval(
                    request.approval_request_id,
                    confirmed,
                );
                if (!this._disposed)
                    this._renderExecution(result);
            } catch (error) {
                if (!this._disposed)
                    this._append(this._assistant(this._friendlyError(error)));
            }
        };
        confirm.connect('clicked', () => respond(true));
        cancel.connect('clicked', () => respond(false));
        actions.add_child(confirm);
        actions.add_child(cancel);
        card.add_child(actions);
        return card;
    }

    _friendlyError(error) {
        return runtimeErrorMessage(error instanceof RuntimeRequestError ? error.code : null);
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
    const label = new St.Label(params);
    if (options.wrap) {
        label.clutter_text.line_wrap = true;
        label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    }
    if (options.ellipsize) {
        label.clutter_text.single_line_mode = true;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    }
    return label;
}

function metricRow(label, value, extraClass = '') {
    const row = new St.BoxLayout({
        style_class: `ai-sidebar-metric-row ${extraClass}`.trim(),
        x_expand: true,
    });
    row.add_child(sidebarLabel(label, 'ai-sidebar-metric-label', {x_expand: true, ellipsize: true}));
    row.add_child(sidebarLabel(value, 'ai-sidebar-metric-value'));
    return row;
}

function processRow(name, cpu, memory) {
    const row = new St.BoxLayout({style_class: 'ai-process-row', x_expand: true});
    row.add_child(sidebarLabel(`${name}:`, 'ai-process-name', {x_expand: true, ellipsize: true}));
    row.add_child(sidebarLabel(cpu, 'ai-process-cpu ai-process-column-cpu'));
    row.add_child(sidebarLabel(memory, 'ai-process-memory ai-process-column-memory'));
    return row;
}

function metricBlock(title, value, detail, extraClass = '') {
    const block = new St.BoxLayout({
        vertical: true,
        style_class: `ai-metric-block ${extraClass}`.trim(),
        x_align: Clutter.ActorAlign.CENTER,
    });
    const ring = createMetricRing(value);
    block.add_child(ring);
    block.add_child(sidebarLabel(title, 'ai-metric-title'));
    const detailLabel = sidebarLabel(detail, 'ai-metric-detail', {wrap: true});
    block.add_child(detailLabel);
    block.setMetric = (nextValue, nextDetail) => {
        ring.setMetric(nextValue);
        detailLabel.set_text(nextDetail);
    };
    return block;
}

function notifyUser(title, message) {
    if (typeof Main.notify === 'function')
        Main.notify(title, message);
    else
        log(`AI-native Linux: ${title}: ${message}`);
}

function launchSystemApp(argv) {
    try {
        Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
        return true;
    } catch (error) {
        logError(error, `AI-native Linux: не удалось запустить ${argv[0]}`);
        return false;
    }
}

function launchUpdateManager() {
    return launchSystemApp(['update-manager']) ||
        launchSystemApp(['gnome-software', '--mode=updates']);
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
    let numericValue = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
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
        const end = start + (Math.PI * 2 * (numericValue ?? 0) / 100);

        context.setLineWidth(8);
        context.setLineCap(1);
        context.setSourceRGBA(0.17, 0.17, 0.17, 0.95);
        context.arc(centerX, centerY, radius, 0, Math.PI * 2);
        context.stroke();

        if (numericValue !== null) {
            context.setSourceRGBA(...cpuColor(numericValue));
            context.arc(centerX, centerY, radius, start, end);
            context.stroke();
        }
        context.$dispose();
    });
    wrap.add_child(drawing);

    const valueLabel = sidebarLabel(
        numericValue === null ? '—' : `${numericValue}%`,
        'ai-ring-value', {
        x_align: Clutter.ActorAlign.CENTER,
        y_align: Clutter.ActorAlign.CENTER,
    });
    wrap.add_child(valueLabel);
    wrap.setMetric = nextValue => {
        numericValue = Number.isFinite(nextValue) ? Math.max(0, Math.min(100, nextValue)) : null;
        valueLabel.set_text(numericValue === null ? '—' : `${numericValue}%`);
        drawing.queue_repaint();
    };
    return wrap;
}

const SidebarView = GObject.registerClass(
class SidebarView extends St.Widget {
    _init(runtime) {
        super._init({
            style_class: 'ai-sidebar-view',
            layout_manager: new Clutter.BinLayout(),
            x_expand: true,
            y_expand: true,
        });
        this._runtime = runtime;
        this._refreshing = false;
        this._disposed = false;
        this.connect('destroy', () => {
            this._disposed = true;
            if (this._refreshSourceId) {
                GLib.Source.remove(this._refreshSourceId);
                this._refreshSourceId = 0;
            }
            this._stopProcessOverlayRefresh();
        });
        this._refreshSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            3,
            () => {
                if (this._disposed)
                    return GLib.SOURCE_REMOVE;
                if (this.visible)
                    this.refresh();
                return GLib.SOURCE_CONTINUE;
            },
        );
        this._body = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            y_expand: true,
        });
        this.add_child(this._body);
        this._scroll = new St.ScrollView({
            style_class: 'ai-sidebar-scroll',
            x_expand: true,
            y_expand: true,
        });
        this._scroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);

        this._content = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-sidebar-content',
            x_expand: true,
        });
        this._scroll.set_child(this._content);
        this._body.add_child(this._scroll);

        this._content.add_child(this._buildStatusCard());
        this._content.add_child(this._buildTaskCard());
        this._content.add_child(this._buildActionsCard());
        this._historyCard = this._buildHistoryCard();
        this._historyCard.hide();
        this._historyButton = this._buildHistoryButton();
        this._body.add_child(this._historyButton);
        this._overlay = this._buildOverlay();
        this.add_child(this._overlay);
        this._processes = [];
        this._showAllProcesses = false;
        this._processSort = 'cpu';
        this._processOrder = 'desc';
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
        this._runtimeState = this._boundMetricRow(card, 'Ядро', 'проверка…');
        this._capabilityState = this._boundMetricRow(card, 'Возможности', '—');
        this._indexState = this._boundMetricRow(card, 'Индекс', '—');
        this._schedulerState = this._boundMetricRow(card, 'Фоновая обработка', '—');
        card.add_child(sidebarLabel('Системный монитор', 'ai-sidebar-kicker'));
        this._monitorSummary = sidebarLabel('подключение…', 'ai-sidebar-caption');
        card.add_child(this._monitorSummary);
        const metrics = new St.BoxLayout({style_class: 'ai-status-main', x_expand: true});
        this._cpuMetric = metricBlock('Загрузка ЦП', null, 'модуль не подключён');
        this._memoryMetric = metricBlock('Оперативная память', null, 'модуль не подключён');
        this._batteryMetric = metricBlock('Батарея', null, 'модуль не подключён', 'ai-battery-metric');
        metrics.add_child(this._cpuMetric);
        metrics.add_child(this._memoryMetric);
        metrics.add_child(this._batteryMetric);
        card.add_child(metrics);
        card.add_child(sidebarLabel('Свободное место на дисках', 'ai-sidebar-kicker'));
        this._diskList = new St.BoxLayout({vertical: true, x_expand: true});
        card.add_child(this._diskList);
        return card;
    }

    _boundMetricRow(card, label, initial) {
        const row = new St.BoxLayout({style_class: 'ai-sidebar-metric-row', x_expand: true});
        row.add_child(sidebarLabel(label, 'ai-sidebar-metric-label', {x_expand: true}));
        const value = sidebarLabel(initial, 'ai-sidebar-metric-value');
        row.add_child(value);
        card.add_child(row);
        return value;
    }

    _buildTaskCard() {
        const card = this._card('Мини-диспетчер задач');
        const header = new St.BoxLayout({style_class: 'ai-process-header', x_expand: true});
        header.add_child(sidebarLabel('Приложение', 'ai-process-heading', {x_expand: true}));
        header.add_child(sidebarLabel('ЦП', 'ai-process-heading ai-process-heading-cpu'));
        header.add_child(sidebarLabel('Память', 'ai-process-heading ai-process-heading-memory'));
        card.add_child(header);
        this._processList = new St.BoxLayout({vertical: true, x_expand: true});
        card.add_child(this._processList);
        this._processCaption = sidebarLabel(
            'Данные появятся после подключения модуля системного мониторинга.',
            'ai-sidebar-caption',
        );
        card.add_child(this._processCaption);
        this._processButton = new St.Button({
            label: 'Показать все процессы',
            style_class: 'ai-sidebar-action',
            x_align: Clutter.ActorAlign.START,
            reactive: true,
        });
        this._processButton.connect('clicked', () => {
            this._openProcessesOverlay();
        });
        card.add_child(this._processButton);
        return card;
    }

    _buildActionsCard() {
        const card = this._card('Быстрые системные действия');
        const refreshButton = new St.Button({
            label: 'Обновить системные данные',
            style_class: 'ai-sidebar-action ai-sidebar-action-wide',
            x_expand: true,
        });
        refreshButton.connect('clicked', () => this.refresh());
        card.add_child(refreshButton);

        const settingsButton = new St.Button({
            label: 'Открыть настройки',
            style_class: 'ai-sidebar-action ai-sidebar-action-wide',
            x_expand: true,
        });
        settingsButton.connect('clicked', () => {
            if (!launchSystemApp(['gnome-control-center']))
                notifyUser('Настройки Ubuntu', 'Системные настройки недоступны.');
        });
        card.add_child(settingsButton);

        const updatesButton = new St.Button({
            label: 'Проверить обновления Ubuntu',
            style_class: 'ai-sidebar-action ai-sidebar-action-wide',
            x_expand: true,
        });
        updatesButton.connect('clicked', () => this._checkUbuntuUpdates(updatesButton));
        card.add_child(updatesButton);
        return card;
    }

    async _checkUbuntuUpdates(button) {
        if (button._checking)
            return;
        button._checking = true;
        button.label = 'Проверяю обновления…';
        button.reactive = false;
        try {
            const result = await this._runtime.checkSystemUpdates();
            const presentation = systemUpdatePresentation(result);
            notifyUser('Обновления Ubuntu', presentation.message);
            if (!presentation.openManager)
                return;
            if (!launchUpdateManager())
                notifyUser('Обновления Ubuntu', 'Менеджер обновлений недоступен.');
        } catch (error) {
            notifyUser(
                'Обновления Ubuntu',
                'Ядро не смогло проверить обновления. Открываю менеджер обновлений.',
            );
            if (!launchUpdateManager())
                notifyUser('Обновления Ubuntu', 'Менеджер обновлений недоступен.');
            logError(error, 'AI-native Linux: backend update check failed');
        } finally {
            button._checking = false;
            button.reactive = true;
            button.label = 'Проверить обновления Ubuntu';
        }
    }

    _buildHistoryCard() {
        const card = this._card('Последние действия');
        this._taskList = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-task-list',
            x_expand: true,
        });
        card.add_child(this._taskList);
        return card;
    }

    _buildHistoryButton() {
        const button = new St.Button({
            label: 'Последние действия',
            style_class: 'ai-sidebar-history',
            x_expand: true,
        });
        button.connect('clicked', () => {
            this._openHistoryOverlay();
        });
        return button;
    }

    openTaskLedger(taskId) {
        const hasTask = typeof taskId === 'string' && taskId.length > 0;
        this._openHistoryOverlay(!hasTask);
        if (hasTask)
            this._showTaskDetail(taskId);
    }

    _buildOverlay() {
        const overlay = new St.Widget({
            style_class: 'ai-sidebar-overlay',
            layout_manager: new Clutter.BinLayout(),
            x_expand: true,
            y_expand: true,
        });
        const panel = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-sidebar-overlay-panel',
            x_expand: true,
            y_expand: true,
        });
        const header = new St.BoxLayout({style_class: 'ai-sidebar-overlay-header', x_expand: true});
        this._overlayTitle = sidebarLabel('Последние действия', 'ai-sidebar-title', {x_expand: true});
        header.add_child(this._overlayTitle);
        const close = new St.Button({label: '×', style_class: 'ai-sidebar-overlay-close'});
        close.connect('clicked', () => this._closeOverlay());
        header.add_child(close);
        panel.add_child(header);
        this._overlayScroll = new St.ScrollView({
            style_class: 'ai-sidebar-overlay-scroll',
            x_expand: true,
            y_expand: true,
        });
        this._overlayScroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);
        this._overlayContent = new St.BoxLayout({
            vertical: true,
            style_class: 'ai-sidebar-overlay-content',
            x_expand: true,
        });
        this._overlayScroll.set_child(this._overlayContent);
        panel.add_child(this._overlayScroll);
        overlay.add_child(panel);
        overlay.hide();
        return overlay;
    }

    _closeOverlay() {
        this._stopProcessOverlayRefresh();
        this._overlayMode = null;
        this._overlayProcessList = null;
        this._overlay.hide();
        this._historyCard.hide();
    }

    _prepareOverlay(title) {
        this._stopProcessOverlayRefresh();
        this._overlayMode = null;
        this._overlayProcessList = null;
        this._overlayTitle.set_text(title);
        this._overlayContent.destroy_all_children();
        this._overlay.show();
    }

    _openHistoryOverlay(loadTasks = true) {
        this._prepareOverlay('Последние действия');
        this._taskList = new St.BoxLayout({vertical: true, style_class: 'ai-task-list', x_expand: true});
        this._overlayContent.add_child(this._taskList);
        if (loadTasks)
            this.refreshTasks();
    }

    _openProcessesOverlay() {
        this._prepareOverlay('Все процессы');
        this._overlayMode = 'processes';
        const toolbar = new St.BoxLayout({style_class: 'ai-process-overlay-toolbar', x_expand: true});
        toolbar.add_child(sidebarLabel('Сортировать:', 'ai-sidebar-caption', {x_expand: true}));
        const cpu = new St.Button({label: 'ЦП', style_class: 'ai-process-sort'});
        const memory = new St.Button({label: 'Память', style_class: 'ai-process-sort'});
        cpu.connect('clicked', () => this._sortProcesses('cpu'));
        memory.connect('clicked', () => this._sortProcesses('memory'));
        toolbar.add_child(cpu);
        toolbar.add_child(memory);
        this._overlayContent.add_child(toolbar);
        const header = new St.BoxLayout({style_class: 'ai-process-header', x_expand: true});
        header.add_child(sidebarLabel('Приложение', 'ai-process-heading', {x_expand: true}));
        header.add_child(sidebarLabel('ЦП', 'ai-process-heading ai-process-heading-cpu'));
        header.add_child(sidebarLabel('Память', 'ai-process-heading ai-process-heading-memory'));
        this._overlayContent.add_child(header);
        this._overlayProcessList = new St.BoxLayout({vertical: true, x_expand: true});
        this._overlayContent.add_child(this._overlayProcessList);
        this._renderOverlayProcesses(this._processes);
        this._sortProcesses(this._processSort, true);
        this._startProcessOverlayRefresh();
    }

    _startProcessOverlayRefresh() {
        this._stopProcessOverlayRefresh();
        this._processOverlayRefreshSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            3,
            () => {
                if (this._disposed || !this._overlay.visible || this._overlayMode !== 'processes') {
                    this._processOverlayRefreshSourceId = 0;
                    return GLib.SOURCE_REMOVE;
                }
                this._sortProcesses(this._processSort, true);
                return GLib.SOURCE_CONTINUE;
            },
        );
    }

    _stopProcessOverlayRefresh() {
        if (this._processOverlayRefreshSourceId) {
            GLib.Source.remove(this._processOverlayRefreshSourceId);
            this._processOverlayRefreshSourceId = 0;
        }
    }

    async _sortProcesses(sort, initial = false) {
        if (!initial && sort === this._processSort)
            this._processOrder = this._processOrder === 'desc' ? 'asc' : 'desc';
        else if (sort !== this._processSort)
            this._processOrder = 'desc';
        this._processSort = sort;
        try {
            const response = await this._runtime.systemStatus({
                process_limit: 50,
                process_sort: this._processSort,
                process_order: this._processOrder,
            });
            if (!this._disposed)
                this._renderOverlayProcesses(monitorPresentation(response).processes);
        } catch (_error) {
            if (!this._disposed)
                this._renderOverlayProcesses(this._processes);
        }
    }

    _renderOverlayProcesses(processes) {
        if (this._overlayMode !== 'processes' || !this._overlayProcessList)
            return;
        this._overlayProcessList.destroy_all_children();
        if (!processes.length) {
            this._overlayProcessList.add_child(sidebarLabel('Процессы недоступны.', 'ai-sidebar-caption'));
            return;
        }
        for (const process of processes)
            this._overlayProcessList.add_child(processRow(`${process.name} (${process.pid})`, process.cpu, process.memory));
    }

    async refresh() {
        if (this._refreshing)
            return;
        this._refreshing = true;
        try {
            const [coreResult, monitorResult] = await Promise.allSettled([
                Promise.all([
                this._runtime.health(),
                this._runtime.capabilities(),
                this._runtime.indexStatus(),
                this._runtime.tasks(),
                ]),
                this._runtime.systemStatus(),
            ]);
            if (this._disposed)
                return;
            if (coreResult.status === 'fulfilled') {
                const [health, capabilities, index, tasks] = coreResult.value;
                const status = systemPresentation(health, capabilities, index);
                this._runtimeState.set_text(status.runtime);
                this._capabilityState.set_text(status.capabilities);
                this._indexState.set_text(status.index);
                this._schedulerState.set_text(status.scheduler);
                this._renderTasks(tasks.tasks ?? []);
            } else {
                this._runtimeState.set_text('недоступно');
                this._capabilityState.set_text('—');
                this._indexState.set_text('—');
                this._schedulerState.set_text('—');
                this._renderTasks([]);
            }
            this._renderMonitor(monitorResult.status === 'fulfilled' ? monitorResult.value : null);
        } finally {
            this._refreshing = false;
        }
    }

    _renderMonitor(snapshot) {
        const view = monitorPresentation(snapshot);
        this._monitorSummary.set_text(view.summary);
        this._cpuMetric.setMetric(view.cpu.value, view.cpu.detail);
        this._memoryMetric.setMetric(view.memory.value, view.memory.detail);
        this._batteryMetric.setMetric(view.battery.value, view.battery.detail);
        this._diskList.destroy_all_children();
        if (!view.disks.length) {
            this._diskList.add_child(sidebarLabel('Диски недоступны.', 'ai-sidebar-caption'));
        } else {
            for (const disk of view.disks)
                this._diskList.add_child(metricRow(disk.label, disk.value, 'ai-disk-row'));
        }
        this._processes = view.processes;
        this._showAllProcesses = false;
        this._renderProcesses();
    }

    _renderProcesses() {
        this._processList.destroy_all_children();
        const visible = this._showAllProcesses ? this._processes : this._processes.slice(0, 5);
        for (const process of visible)
            this._processList.add_child(processRow(`${process.name} (${process.pid})`, process.cpu, process.memory));
        this._processCaption.visible = this._processes.length === 0;
        this._processButton.reactive = this._processes.length > 0;
        this._processButton.visible = this._processes.length > 0;
        this._processButton.label = 'Показать все процессы';
    }

    async refreshTasks() {
        try {
            const response = await this._runtime.tasks();
            if (!this._disposed)
                this._renderTasks(response.tasks ?? []);
        } catch (_error) {
            if (!this._disposed)
                this._renderTasks([]);
        }
    }

    _renderTasks(tasks) {
        this._taskList.destroy_all_children();
        if (!tasks.length) {
            this._taskList.add_child(sidebarLabel(
                'Последних действий пока нет.',
                'ai-sidebar-caption',
            ));
            return;
        }
        for (const task of tasks.slice(0, 10)) {
            const button = new St.Button({
                label: taskRowLabel(task),
                style_class: 'ai-task-row',
                x_expand: true,
            });
            button.connect('clicked', () => this._showTaskDetail(task.task_id));
            this._taskList.add_child(button);
        }
    }

    async _showTaskDetail(taskId) {
        try {
            const task = await this._runtime.taskDetail(taskId);
            if (this._disposed)
                return;
            this._taskList.destroy_all_children();
            const back = new St.Button({label: '← К списку', style_class: 'ai-task-row'});
            back.connect('clicked', () => this.refreshTasks());
            this._taskList.add_child(back);
            const presentation = taskDetailPresentation(task);
            this._taskList.add_child(sidebarLabel(
                presentation.title,
                'ai-sidebar-title',
            ));
            this._taskList.add_child(sidebarLabel(
                presentation.counts,
                'ai-sidebar-caption',
            ));
            for (const reference of presentation.references) {
                this._taskList.add_child(sidebarLabel(
                    reference.text,
                    'ai-task-reference',
                    {wrap: true},
                ));
            }
        } catch (_error) {
            if (!this._disposed)
                this._renderTasks([]);
        }
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

        this._sidebar = new SidebarView(this._runtime);
        this._sidebar.set_position(0, 0);
        this._sidebar.set_size(PANEL_WIDTH, DEFAULT_PANEL_HEIGHT - TAB_HEIGHT);
        this._views.add_child(this._sidebar);
        this._workspace = new ChatView(
            this._runtime,
            taskId => this._openTaskLedger(taskId),
        );
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
        if (index === 0)
            this._sidebar.refresh();
    }

    _openTaskLedger(taskId) {
        this._selectTab(0);
        this._sidebar.openTaskLedger(taskId);
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
