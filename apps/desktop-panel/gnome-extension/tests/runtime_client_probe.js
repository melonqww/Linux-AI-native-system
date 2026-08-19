import System from 'system';

import {RuntimeClient} from '../runtime-client.js';


const socketPath = ARGV[0];
if (!socketPath) {
    printerr('socket path is required');
    System.exit(2);
}

const client = new RuntimeClient({socketPath, timeoutMs: 5_000});
try {
    const response = await client.health();
    print(JSON.stringify(response));
} catch (error) {
    printerr(error.code ?? 'runtime_probe_failed');
    System.exit(1);
} finally {
    client.destroy();
}
