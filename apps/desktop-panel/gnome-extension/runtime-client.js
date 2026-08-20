import Gio from 'gi://Gio';
import GLib from 'gi://GLib';


const IPC_VERSION = 1;
const MAX_MESSAGE_BYTES = 64 * 1024;
const DEFAULT_TIMEOUT_MS = 10_000;


export class RuntimeRequestError extends Error {
    constructor(code, status = 0, retryable = false) {
        super(code);
        this.name = 'RuntimeRequestError';
        this.code = code;
        this.status = status;
        this.retryable = retryable;
    }
}


export class RuntimeClient {
    constructor({socketPath = null, timeoutMs = DEFAULT_TIMEOUT_MS} = {}) {
        const runtimeDirectory = GLib.get_user_runtime_dir();
        if (!socketPath && !runtimeDirectory)
            throw new RuntimeRequestError('runtime_directory_unavailable');
        this._socketPath = socketPath ?? GLib.build_filenamev([
            runtimeDirectory,
            'ai-native-linux',
            'runtime.sock',
        ]);
        this._timeoutMs = Math.max(1_000, Math.min(30_000, timeoutMs));
        this._active = new Set();
        this._destroyed = false;
    }

    health() {
        return this.request('GET', '/v1/health');
    }

    capabilities() {
        return this.request('GET', '/v1/capabilities');
    }

    indexStatus() {
        return this.request('GET', '/v1/index-status');
    }

    systemStatus(options = null) {
        if (options && typeof options === 'object' && !Array.isArray(options))
            return this.request('POST', '/v1/system-status', options);
        return this.request('GET', '/v1/system-status');
    }

    tasks() {
        return this.request('GET', '/v1/tasks');
    }

    taskDetail(taskId) {
        return this.request('POST', '/v1/tasks/detail', {task_id: taskId});
    }

    compileIntent(text) {
        return this.request('POST', '/v1/intent/compile', {text});
    }

    executePlan(planId) {
        return this.request('POST', '/v1/plan/execute', {plan_id: planId});
    }

    respondToApproval(approvalRequestId, confirmed) {
        return this.request('POST', '/v1/approval/respond', {
            approval_request_id: approvalRequestId,
            confirmed,
        });
    }

    async request(method, path, body = {}) {
        if (this._destroyed)
            throw new RuntimeRequestError('runtime_client_destroyed');
        this._validateRequest(method, path, body);
        const requestId = GLib.uuid_string_random();
        const frame = JSON.stringify({
            version: IPC_VERSION,
            request_id: requestId,
            method,
            path,
            body,
        }) + '\n';
        const encoded = new TextEncoder().encode(frame);
        if (encoded.byteLength > MAX_MESSAGE_BYTES)
            throw new RuntimeRequestError('request_too_large');

        const cancellable = new Gio.Cancellable();
        this._active.add(cancellable);
        let timedOut = false;
        let timeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            this._timeoutMs,
            () => {
                timeoutId = 0;
                timedOut = true;
                cancellable.cancel();
                return GLib.SOURCE_REMOVE;
            },
        );
        let connection = null;
        try {
            const client = new Gio.SocketClient();
            client.set_timeout(Math.ceil(this._timeoutMs / 1_000));
            const address = new Gio.UnixSocketAddress({path: this._socketPath});
            connection = await this._connect(client, address, cancellable);
            await this._write(connection.get_output_stream(), encoded, cancellable);
            const line = await this._readLine(connection.get_input_stream(), cancellable);
            return this._decodeResponse(line, requestId);
        } catch (error) {
            if (error instanceof RuntimeRequestError)
                throw error;
            if (timedOut)
                throw new RuntimeRequestError('runtime_timeout', 0, true);
            if (this._destroyed)
                throw new RuntimeRequestError('runtime_client_destroyed');
            throw new RuntimeRequestError('runtime_unavailable', 0, true);
        } finally {
            if (timeoutId)
                GLib.Source.remove(timeoutId);
            this._active.delete(cancellable);
            try {
                connection?.close(null);
            } catch (_error) {
                // The one-request connection may already be closed by runtime.
            }
        }
    }

    destroy() {
        this._destroyed = true;
        for (const cancellable of this._active)
            cancellable.cancel();
        this._active.clear();
    }

    _connect(client, address, cancellable) {
        return new Promise((resolve, reject) => {
            client.connect_async(address, cancellable, (source, result) => {
                try {
                    resolve(source.connect_finish(result));
                } catch (error) {
                    reject(error);
                }
            });
        });
    }

    _write(stream, encoded, cancellable) {
        return new Promise((resolve, reject) => {
            stream.write_all_async(
                encoded,
                GLib.PRIORITY_DEFAULT,
                cancellable,
                (source, result) => {
                    try {
                        const [written, bytesWritten] = source.write_all_finish(result);
                        if (!written || bytesWritten !== encoded.byteLength)
                            throw new RuntimeRequestError('runtime_write_incomplete');
                        resolve();
                    } catch (error) {
                        reject(error);
                    }
                },
            );
        });
    }

    _readLine(stream, cancellable) {
        const data = new Gio.DataInputStream({base_stream: stream});
        return new Promise((resolve, reject) => {
            data.read_line_async(
                GLib.PRIORITY_DEFAULT,
                cancellable,
                (source, result) => {
                    try {
                        const [line, length] = source.read_line_finish_utf8(result);
                        if (line === null)
                            throw new RuntimeRequestError('runtime_response_missing');
                        if (length > MAX_MESSAGE_BYTES)
                            throw new RuntimeRequestError('runtime_response_too_large');
                        resolve(line);
                    } catch (error) {
                        reject(error);
                    }
                },
            );
        });
    }

    _decodeResponse(line, requestId) {
        let envelope;
        try {
            envelope = JSON.parse(line);
        } catch (_error) {
            throw new RuntimeRequestError('runtime_response_invalid');
        }
        if (!envelope || Array.isArray(envelope) || typeof envelope !== 'object')
            throw new RuntimeRequestError('runtime_response_invalid');
        const fields = Object.keys(envelope).sort().join(',');
        if (fields !== 'body,request_id,status,version' ||
            envelope.version !== IPC_VERSION ||
            envelope.request_id !== requestId ||
            !Number.isInteger(envelope.status))
            throw new RuntimeRequestError('runtime_response_invalid');
        if (envelope.status < 200 || envelope.status >= 300) {
            const error = envelope.body?.error;
            const code = typeof error?.code === 'string' ? error.code : 'runtime_request_failed';
            throw new RuntimeRequestError(code, envelope.status, error?.retryable === true);
        }
        return envelope.body;
    }

    _validateRequest(method, path, body) {
        if (!['GET', 'POST'].includes(method) ||
            typeof path !== 'string' ||
            !path.startsWith('/v1/') ||
            path.length > 256 ||
            !body ||
            Array.isArray(body) ||
            typeof body !== 'object' ||
            (method === 'GET' && Object.keys(body).length !== 0))
            throw new RuntimeRequestError('runtime_request_invalid');
    }
}
