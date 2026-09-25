# File Operations R1

This module owns bounded user-facing file mutations. Its isolated service and
capability adapter are registered by the production runtime; the module is
enabled by default once its dependency and trusted policies are available.

## R1 boundary

The module defines operations to inspect a trusted selection, create one folder,
move results, rename exactly one item, and move results to the recoverable desktop
trash. Existing `storage.materialize.plan-copy` remains the copy operation and is
reused through the required `storage.catalog` dependency instead of being
duplicated.

Raw paths are not model arguments. Existing files enter through the server-owned
`results_from` reference; destinations use the closed roles `desktop`,
`documents`, `downloads`, or trusted `context.last_destination`. Folder and file
names are single names, not paths. Permanent deletion, overwrite and unrestricted
recursive actions are outside R1.

## Trusted risk contract

The manifest requests scopes but cannot set risk or bypass confirmation. Core
Permission Gateway policies assign:

- `files.items.inspect`: R0, read-only, no approval;
- `files.directory.create`: R1, approval required;
- `files.items.move`: R1, approval required;
- `files.items.rename`: R1, approval required;
- `files.items.trash`: R1, approval required.

Every write commit is limited to internal or authenticated Unix transport. An
operation is published only when its enabled manifest, trusted policy and
registered handler intersect; a manifest cannot grant itself execution.

## Isolated implementation

`FileOperationsService` accepts destination roots from trusted host
configuration and resolves existing items only through a server-owned selection
resolver. Prepare calls do not mutate the filesystem. Commit revalidates device,
inode, type, size and modification time, rejects symlinks/reparse points and
path escape, enforces bounded selections and prevents plan replay.

Move and rename use same-filesystem atomic moves. Cross-device moves fail closed
in R1 instead of silently degrading into copy-and-delete. Multi-item failures
attempt rollback. Trash uses a configurable Freedesktop-style `files/` + `info/`
root and writes `.trashinfo`; permanent deletion is absent. Plans are
intentionally process-local, so a restart invalidates uncommitted authority.

## Runtime integration

`FileOperationsCapabilityHandler` implements the shared executor lifecycle. R0
inspection returns a bounded capability-neutral result. Every mutation returns
an opaque prepared operation and a redacted approval request; only the core can
retain that preparation and dispatch its commit after consent. Decline, expiry
and failed policy checks discard the prepared plan without applying it.

The runtime resolves `results_from` through its server-owned search snapshot
map before the module sees a collection identifier. The module then resolves
that collection through `storage.catalog`; unavailable or empty selections fail
closed. No file-operation branch is added to intent compilation or routing.

Stage 3 includes focused integration coverage for visibility intersection,
approve, deny and search-to-move reference resolution. Broader user journeys
remain the separate laboratory stage.
