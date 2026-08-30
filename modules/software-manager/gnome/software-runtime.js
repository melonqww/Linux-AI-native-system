import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

const DESKTOP_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.desktop$/;

export class GnomeSoftwareRuntimeAdapter {
    constructor() {
        this._apps = Shell.AppSystem.get_default();
        this._shellSettings = new Gio.Settings({schema_id: 'org.gnome.shell'});
    }

    _lookup(desktopId) {
        if (!DESKTOP_ID.test(desktopId))
            throw new Error('invalid_desktop_id');
        const app = this._apps.lookup_app(desktopId);
        if (app === null)
            throw new Error('desktop_application_not_found');
        return app;
    }

    launch(desktopId) {
        this._lookup(desktopId).activate();
        // activate() accepting the request is not proof that the app started.
        // Call status() until GNOME reports RUNNING or the coordinator times out.
    }

    status(desktopId) {
        const app = this._lookup(desktopId);
        const state = app.get_state();
        const windows = app.get_windows();
        const pids = app.get_pids();
        if (state === Shell.AppState.RUNNING)
            return {state: 'running', window_count: windows.length, process_count: pids.length};
        if (state === Shell.AppState.STARTING)
            return {state: 'starting', window_count: windows.length, process_count: pids.length};
        return {state: 'stopped', window_count: 0, process_count: 0};
    }

    close(desktopId) {
        const app = this._lookup(desktopId);
        if (!app.request_quit())
            throw new Error('application_close_rejected');
        // The coordinator verifies the stopped state after this request.
    }

    set_pinned(desktopId, pinned) {
        this._lookup(desktopId);
        const key = 'favorite-apps';
        const current = this._shellSettings.get_strv(key);
        const next = pinned
            ? [...new Set([...current, desktopId])]
            : current.filter(item => item !== desktopId);
        if (!this._shellSettings.set_strv(key, next))
            return false;
        const saved = this._shellSettings.get_strv(key);
        return pinned ? saved.includes(desktopId) : !saved.includes(desktopId);
    }
}
