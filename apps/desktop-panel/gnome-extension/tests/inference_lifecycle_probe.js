import System from 'system';

import {RuntimeClient} from '../runtime-client.js';


const socketPath = ARGV[0];
if (!socketPath) {
    printerr('socket path is required');
    System.exit(2);
}

const client = new RuntimeClient({socketPath, timeoutMs: 5_000});
try {
    const health = await client.health();
    const inference = await client.inferenceStatus();
    print(JSON.stringify({health, inference}));
} catch (error) {
    printerr(error.code ?? 'inference_lifecycle_probe_failed');
    System.exit(1);
} finally {
    client.destroy();
}
