# Security Center

Security Center is the trusted, on-demand security module for AI-native Linux.
Version `0.1.0` is deliberately a foundation only: it proves module discovery,
worker lifecycle, strict invocation, and a bounded public status contract before
any privileged security behavior is introduced.

## Foundation scope

The module currently publishes one capability:

- `security.module.status`, protected by `security.read-status`;
- no `user_intent` route;
- a closed, empty input object;
- a deterministic, JSON-safe status response.

The worker exposes the standard module lifecycle functions:

```text
worker_start()
worker_health()
worker_invoke("status", {})
worker_stop()
```

Starting and stopping are idempotent. Health and invocation fail closed while
the worker is stopped. Unknown operations and every non-empty or non-object
payload are rejected.

## Explicitly excluded

This stage performs no file scanning, antivirus detection, quarantine, network
access, subprocess execution, AI inference, persistence, background monitoring,
or privileged system changes. Importing the package performs no I/O and does
not start the worker.

Later stages may add security behavior only behind separately reviewed
capabilities, permissions, bounded contracts, and tests.

## Run the fast tests

From the repository root:

```bash
python -m pytest modules/security-center/tests -q
```
