"""A demonstrator for the policy → audit part of the MVP pipeline."""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from pathlib import Path
from uuid import uuid4

from .audit import JsonlAuditLog, create_audit_event
from .policy import PolicyEngine


_XDG_SECURITY_DIRECTORY = re.compile(
    r'^XDG_(?:DOWNLOAD|DESKTOP)_DIR="([^"\x00]{1,1024})"$'
)


def security_quick_relative_paths(
    home: Path,
    user_dirs_text: str | None = None,
) -> tuple[str, ...]:
    """Resolve bounded XDG risk locations without allowing paths outside home."""

    if user_dirs_text is None:
        try:
            user_dirs_text = (home / ".config" / "user-dirs.dirs").read_text(
                encoding="utf-8"
            )[:16_384]
        except (OSError, UnicodeError):
            user_dirs_text = ""
    candidates = ["Downloads", "Desktop", ".config/autostart"]
    for line in user_dirs_text.splitlines():
        match = _XDG_SECURITY_DIRECTORY.fullmatch(line.strip())
        if match is None:
            continue
        raw = match.group(1)
        path = (
            home / raw.removeprefix("$HOME/")
            if raw.startswith("$HOME/")
            else Path(raw)
        )
        try:
            relative = path.expanduser().resolve(strict=False).relative_to(home)
        except (OSError, ValueError):
            continue
        normalized = relative.as_posix()
        if normalized not in {"", "."}:
            candidates.append(normalized)
    return tuple(dict.fromkeys(candidates))


def security_scan_configuration(
    home: Path | None = None,
    temporary: Path | None = None,
) -> tuple[dict[str, Path], tuple[tuple[str, str], ...]]:
    """Return trusted roots and existing high-risk locations for panel scans."""

    home_root = (home or Path.home()).expanduser().resolve(strict=True)
    roots = {"home": home_root}
    quick_targets: list[tuple[str, str]] = []
    for relative in security_quick_relative_paths(home_root):
        candidate = home_root / relative
        try:
            candidate.lstat()
            candidate.resolve(strict=True).relative_to(home_root)
        except (OSError, ValueError):
            continue
        if candidate.is_dir() and not candidate.is_symlink():
            quick_targets.append(("home", relative))
    temp_root = temporary
    if temp_root is None and sys.platform.startswith("linux"):
        temp_root = Path(os.environ.get("TMPDIR", "/tmp"))
    if temp_root is not None:
        try:
            resolved_temp = temp_root.expanduser().resolve(strict=True)
        except OSError:
            resolved_temp = None
        if resolved_temp is not None and resolved_temp.is_dir():
            try:
                resolved_temp.relative_to(home_root)
            except ValueError:
                roots["temporary"] = resolved_temp
                quick_targets.append(("temporary", ""))
    return roots, tuple(quick_targets)


def combine_security_scans(
    target: str,
    mode: str,
    results: list[dict[str, object]],
) -> dict[str, object]:
    """Combine bounded module results without exposing configured root paths."""

    threats = sum(int(item.get("threat_files", 0)) for item in results)
    unknown = sum(int(item.get("unknown_files", 0)) for item in results)
    skipped = sum(int(item.get("skipped_files", 0)) for item in results)
    scanned = sum(int(item.get("scanned_files", 0)) for item in results)
    scanned_bytes = sum(int(item.get("scanned_bytes", 0)) for item in results)
    partial = not results or any(
        item.get("status") != "completed" for item in results
    )
    return {
        "schema_version": 1,
        "target": target,
        "mode": mode,
        "status": "partial" if partial else "completed",
        "verdict": (
            "malware_detected"
            if threats
            else "unknown"
            if partial or unknown
            else "no_threat_detected"
        ),
        "scanned_files": scanned,
        "scanned_bytes": scanned_bytes,
        "threat_files": threats,
        "unknown_files": unknown,
        "skipped_files": skipped,
        "scopes_scanned": len(results),
    }


def demo_payload() -> dict[str, object]:
    """A fixed, safe request used before the Ubuntu adapter is available."""
    return {
        "request_id": str(uuid4()),
        "intent": "get_system_status",
        "arguments": {"metrics": ["disk", "memory", "cpu"], "process_limit": 5},
        "reason": "Portfolio demo: show a read-only system status request.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-native Linux portfolio MVP")
    parser.add_argument(
        "--demo", action="store_true", help="validate the fixed read-only demo request"
    )
    parser.add_argument(
        "--serve-panel", action="store_true", help="serve the panel runtime API"
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "unix", "http"),
        default="auto",
        help="auto uses authenticated Unix IPC on Linux and loopback HTTP elsewhere",
    )
    parser.add_argument(
        "--socket-path", type=Path, help="override the Linux Unix socket path"
    )
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--storage-database", type=Path, default=Path("data/storage-catalog.sqlite3")
    )
    parser.add_argument(
        "--index-database", type=Path, default=Path("data/document-index.sqlite3")
    )
    parser.add_argument(
        "--registry-database", type=Path, default=Path("data/capabilities.sqlite3")
    )
    parser.add_argument(
        "--task-ledger-database", type=Path, default=Path("data/task-ledger.sqlite3")
    )
    parser.add_argument(
        "--memory-database", type=Path, default=Path("data/task-memory.sqlite3")
    )
    parser.add_argument(
        "--workspace-database", type=Path, default=Path("data/workspace.sqlite3")
    )
    parser.add_argument("--intent-model", default="qwen3.5:2b")
    parser.add_argument("--semantic-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--intent-timeout", type=float, default=45.0)
    parser.add_argument("--intent-context-tokens", type=int, default=8_192)
    parser.add_argument("--locale", default="ru", choices=("ru", "en"))
    parser.add_argument("--no-intent-compiler", action="store_true")
    parser.add_argument(
        "--module-root",
        action="append",
        type=Path,
        dest="module_roots",
        help="manifest root; defaults to ./services and ./modules",
    )
    parser.add_argument(
        "--audit-file",
        type=Path,
        default=Path("data/audit-events.jsonl"),
        help="local JSONL file for redacted audit events",
    )
    args = parser.parse_args()
    if args.serve_panel:
        from ai_native_capabilities import CapabilityRegistry
        from ai_native_module_manager import ModuleProcessError, ModuleProcessManager
        from ai_native_query import (
            QueryRuntimeApplication,
            QueryService,
            workspace_model_readiness,
        )
        from ai_native_workspace import WorkspaceStore

        roots = args.module_roots or [Path("services"), Path("modules")]
        registry = CapabilityRegistry(args.registry_database)
        report = registry.sync(roots)
        if report.issues:
            parser.error(f"module manifest errors: {report.issues}")
        security_roots, security_quick_targets = security_scan_configuration()
        manager = ModuleProcessManager(
            registry,
            storage_database=args.storage_database,
            index_database=args.index_database,
            security_scan_roots=security_roots,
            security_quick_targets=security_quick_targets,
        )
        server = None
        workspace_controller = None
        try:
            manager.start_for_capability("storage.watch.events")
            system_monitor_status = None
            try:
                monitor_module_id = manager.start_for_capability(
                    "system.monitor.snapshot"
                )
            except ModuleProcessError:
                print("System monitor: unavailable")
            else:
                system_monitor_status = lambda payload: manager.invoke(
                    monitor_module_id,
                    "snapshot",
                    {"process_limit": 20, **payload},
                )

            def system_updates_check(payload: dict[str, object]) -> dict[str, object]:
                module_id = manager.start_for_capability("system.updates.check")
                return manager.invoke(module_id, "check", payload, timeout=30)

            from ai_native_turns import OllamaEmbeddingProvider

            query_service = QueryService(
                storage_database=args.storage_database,
                index_database=args.index_database,
                coverage_source=lambda: manager.health_details("storage.watch"),
                semantic_provider=OllamaEmbeddingProvider(
                    model=args.semantic_model,
                    base_url=args.ollama_url,
                ),
            )
            intent_pipeline = None
            task_context = None
            plan_store = None
            plan_executor = None
            from ai_native_ledger import TaskLedger

            task_ledger = TaskLedger(args.task_ledger_database)
            workspace = WorkspaceStore(args.workspace_database)
            workspace.purge_expired()
            ollama_provider_status = None
            ollama_provider_decision = None
            try:
                provider_module_id = manager.start_for_capability(
                    "provider.ollama.status"
                )
                ollama_provider_status = lambda: manager.invoke(
                    provider_module_id, "status", timeout=15
                )
                ollama_provider_decision = lambda payload: manager.invoke(
                    provider_module_id, "respond", payload, timeout=15
                )
            except ModuleProcessError:
                print("Ollama provider installer: unavailable")
            model_status = None
            model_catalog = None
            model_decision = None
            try:
                model_module_id = manager.start_for_capability("model.local.ensure")
                model_status = lambda: manager.invoke(
                    model_module_id, "ensure", timeout=15
                )
                model_catalog = lambda: manager.invoke(
                    model_module_id, "catalog", timeout=15
                )
                model_decision = lambda payload: manager.invoke(
                    model_module_id, "respond", payload, timeout=15
                )
            except ModuleProcessError:
                print("Model manager: unavailable")
                model_status = lambda: {
                    "state": "unavailable",
                    "reason": "model_manager_unavailable",
                }
            software_snapshot = None
            software_module_id = None
            try:
                software_module_id = manager.start_for_capability(
                    "software.catalog.read"
                )
                software_snapshot = lambda: manager.invoke(
                    software_module_id, "snapshot", timeout=15
                )
                software_prepare = lambda payload: manager.invoke(
                    software_module_id, "prepare", payload, timeout=15
                )
                software_respond = lambda payload: manager.invoke(
                    software_module_id, "respond", payload, timeout=60
                )
                software_control = lambda payload: manager.invoke(
                    software_module_id, "control", payload, timeout=60
                )
                software_restore = lambda payload: manager.invoke(
                    software_module_id, "restore", payload, timeout=60
                )
            except ModuleProcessError:
                print("Software manager: unavailable")
                software_prepare = None
                software_respond = None
                software_control = None
                software_restore = None
            security_snapshot = None
            security_scan = None
            security_jobs = None
            security_quarantine = None
            try:
                security_module_id = manager.start_for_capability(
                    "security.module.status"
                )

                def security_snapshot():
                    return {
                        "schema_version": 1,
                        "module": manager.invoke(
                            security_module_id, "status", timeout=15
                        ),
                        "posture": manager.invoke(
                            security_module_id, "posture_scan", timeout=30
                        ),
                        "findings": manager.invoke(
                            security_module_id,
                            "findings_list",
                            {"state": "active", "limit": 10},
                            timeout=15,
                        ),
                        "quarantine": manager.invoke(
                            security_module_id,
                            "quarantine_list",
                            {"state": "quarantined", "limit": 10},
                            timeout=15,
                        ),
                    }

                def security_scan(payload):
                    target = payload["target"]
                    if target == "file":
                        result = manager.invoke(
                            security_module_id,
                            "scan",
                            {
                                "resource_id": "home",
                                "relative_path": payload["relative_path"],
                            },
                            timeout=30,
                        )
                        return combine_security_scans(
                            target,
                            "single",
                            [
                                {
                                    "status": result.get("status"),
                                    "threat_files": int(
                                        result.get("verdict") == "malware_detected"
                                    ),
                                    "unknown_files": int(
                                        result.get("verdict") == "unknown"
                                    ),
                                    "skipped_files": int(
                                        result.get("status") == "rejected"
                                    ),
                                    "scanned_files": int(
                                        result.get("status") == "completed"
                                    ),
                                    "scanned_bytes": result.get("size_bytes", 0) or 0,
                                }
                            ],
                        )
                    if target == "folder":
                        targets = [("home", payload["relative_path"])]
                        mode = payload["mode"]
                    elif target == "quick":
                        targets = list(security_quick_targets)
                        mode = "quick"
                    else:
                        targets = [(resource_id, "") for resource_id in security_roots]
                        mode = "full"
                    results = [
                        manager.invoke(
                            security_module_id,
                            "scan_profile",
                            {
                                "resource_id": resource_id,
                                "relative_path": relative_path,
                                "mode": mode,
                            },
                            timeout=60,
                        )
                        for resource_id, relative_path in targets
                    ]
                    return combine_security_scans(target, mode, results)

                def security_jobs(operation, payload):
                    if operation == "start":
                        target = payload["target"]
                        if target == "file":
                            job_payload = {
                                "target": "file",
                                "mode": "single",
                                "resource_id": "home",
                                "relative_path": payload["relative_path"],
                            }
                        elif target == "folder":
                            job_payload = {
                                "target": "folder",
                                "mode": payload["mode"],
                                "scopes": [
                                    {
                                        "resource_id": "home",
                                        "relative_path": payload["relative_path"],
                                    }
                                ],
                            }
                        else:
                            targets = (
                                security_quick_targets
                                if target == "quick"
                                else tuple(
                                    (resource_id, "")
                                    for resource_id in security_roots
                                )
                            )
                            job_payload = {
                                "target": target,
                                "mode": target,
                                "scopes": [
                                    {
                                        "resource_id": resource_id,
                                        "relative_path": relative_path,
                                    }
                                    for resource_id, relative_path in targets
                                ],
                            }
                        return manager.invoke(
                            security_module_id, "job_start", job_payload, timeout=15
                        )
                    worker_operation = {
                        "status": "job_status",
                        "cancel": "job_cancel",
                        "history": "job_history",
                        "settings_get": "job_settings_get",
                        "settings_update": "job_settings_update",
                    }[operation]
                    return manager.invoke(
                        security_module_id, worker_operation, payload, timeout=15
                    )

                def security_quarantine(operation, payload):
                    worker_operation = {
                        "prepare": "quarantine_prepare",
                        "commit": "quarantine_commit",
                        "restore": "quarantine_restore",
                        "cancel": "quarantine_cancel",
                    }[operation]
                    return manager.invoke(
                        security_module_id,
                        worker_operation,
                        payload,
                        timeout=30,
                    )
            except ModuleProcessError:
                print("Security Center: unavailable")
            if not args.no_intent_compiler:
                from ai_native_intents import (
                    IntentCompiler,
                    OllamaModelProvider,
                    TaskContextStore,
                    build_operation_definitions,
                )

                provider = OllamaModelProvider(
                    model=args.intent_model,
                    base_url=args.ollama_url,
                    timeout_seconds=args.intent_timeout,
                    context_tokens=args.intent_context_tokens,
                )
                model_health = provider.health()
                state = (
                    "ready"
                    if model_health.available
                    else f"unavailable ({model_health.reason})"
                )
                print(f"Intent model {args.intent_model}: {state}")
                context_store = TaskContextStore(
                    locale=args.locale,
                    database=args.memory_database,
                )
                task_context = context_store.snapshot
                from ai_native_orchestrator import (
                    CompiledPlanStore,
                    DestinationResolver,
                    ExecutionOrchestrator,
                    OrchestrationAuditLog,
                )
                from ai_native_storage import ApprovalAuthority, MaterializeService

                plan_store = CompiledPlanStore()
                plan_executor = ExecutionOrchestrator(
                    query_service,
                    context_store,
                    audit_sink=OrchestrationAuditLog(args.audit_file).append,
                    materialize_service=MaterializeService(
                        args.storage_database, ApprovalAuthority()
                    ),
                    destination_resolver=DestinationResolver(),
                    capability_source=lambda: (
                        *registry.available_capabilities(),
                        "documents.query.search",
                    ),
                    task_ledger=task_ledger,
                )

                def operation_source():
                    return build_operation_definitions(
                        registry.capability_contracts(),
                        available_capabilities=plan_executor.available_capabilities(),
                        policy_source=plan_executor.permission_gateway.policy,
                    )

                intent_pipeline = IntentCompiler(
                    provider,
                    operation_source=operation_source,
                )
                from ai_native_workspace import WorkspaceRuntime
                from ai_native_turns import (
                    CapabilityCandidateRouter,
                    CapabilityDescriptor,
                    HybridCapabilityRouter,
                    OllamaEmbeddingProvider,
                    SemanticCapabilitySelector,
                    TurnRouter,
                )

                operation_definitions = operation_source()
                capability_descriptors = tuple(
                    CapabilityDescriptor(
                        definition.capability_id,
                        definition.operation,
                        definition.description,
                        definition.examples,
                    )
                    for definition in operation_definitions
                )
                lexical_router = CapabilityCandidateRouter(capability_descriptors)
                semantic_selector = SemanticCapabilitySelector(
                    capability_descriptors,
                    OllamaEmbeddingProvider(
                        model=args.semantic_model,
                        base_url=args.ollama_url,
                    ),
                )
                capability_router = HybridCapabilityRouter(
                    lexical_router,
                    semantic_selector,
                )

                workspace_controller = WorkspaceRuntime(
                    workspace,
                    provider,
                    intent_pipeline,
                    plan_executor,
                    context_store.snapshot,
                    model_status=lambda: workspace_model_readiness(
                        ollama_provider_status, model_status
                    ),
                    turn_router=TurnRouter(provider),
                    capability_router=capability_router,
                )
                for capability in plan_executor.available_capabilities():
                    required_scopes = plan_executor.permission_gateway.required_scopes(
                        capability
                    )
                    for provider_info in registry.providers(capability):
                        provider_module = registry.get_module(provider_info.module_id)
                        missing = required_scopes - set(
                            provider_module.manifest.requested_permissions
                        )
                        if missing:
                            parser.error(
                                f"module {provider_info.module_id} does not declare "
                                f"required policy scopes for {capability}: {sorted(missing)}"
                            )
            application = QueryRuntimeApplication(
                query_service,
                scheduler_status=lambda: manager.health_details("storage.watch"),
                system_monitor_status=system_monitor_status,
                system_updates_check=system_updates_check,
                intent_pipeline=intent_pipeline,
                task_context=task_context,
                plan_store=plan_store,
                plan_executor=plan_executor,
                task_ledger=task_ledger,
                workspace=workspace,
                workspace_controller=workspace_controller,
                model_catalog=model_catalog,
                model_decision=model_decision,
                ollama_provider_status=ollama_provider_status,
                ollama_provider_decision=ollama_provider_decision,
                software_snapshot=software_snapshot,
                software_prepare=software_prepare,
                software_respond=software_respond,
                software_control=software_control,
                software_restore=software_restore,
                security_snapshot=security_snapshot,
                security_scan=security_scan,
                security_jobs=security_jobs,
                security_quarantine=security_quarantine,
            )
            transport = (
                "unix"
                if args.transport == "auto" and sys.platform.startswith("linux")
                else "http"
                if args.transport == "auto"
                else args.transport
            )
            if transport == "unix":
                from .unix_socket import create_unix_server

                server = create_unix_server(application, args.socket_path)
                print(f"Panel runtime: unix://{server.socket_path}")
            else:
                from .bridge import create_server

                server = create_server(application, host=args.host, port=args.port)
                print(
                    f"Panel runtime (development fallback): http://{args.host}:{server.server_port}"
                )
            if software_module_id is not None:
                manager.invoke(software_module_id, "activate", timeout=15)
            # Recovery is deliberately delayed until this process has acquired
            # the unique runtime transport. A losing second instance must not
            # mark the active daemon's tasks as interrupted.
            task_ledger.recover_after_restart()
            task_ledger.purge_expired()
            workspace.recover_after_restart(
                message=(
                    "Выполнение остановлено после перезапуска системы."
                    if args.locale == "ru"
                    else "Execution stopped after the system restarted."
                )
            )
            previous_sigterm = None
            if hasattr(signal, "SIGTERM"):
                previous_sigterm = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, _interrupt_runtime)
            try:
                server.serve_forever()
            finally:
                if previous_sigterm is not None:
                    signal.signal(signal.SIGTERM, previous_sigterm)
        except KeyboardInterrupt:
            pass
        finally:
            if server is not None:
                server.server_close()
            if workspace_controller is not None:
                workspace_controller.close()
            manager.stop_all()
        return 0
    if not args.demo:
        parser.error("choose --demo or --serve-panel")

    proposal, decision = PolicyEngine().evaluate(demo_payload())
    event = create_audit_event(
        intent=proposal.intent, decision=decision, result="validated_no_execution"
    )
    JsonlAuditLog(args.audit_file).append(event)

    print("AI-native Linux — policy demo")
    print(f"Intent: {proposal.intent}")
    print(f"Tool: {decision.tool}")
    print(f"Risk level: {decision.risk_level.value}")
    print(f"Approval required: {decision.approval_required}")
    print(f"Audit event: {event.event_id}")
    print(f"Audit file: {args.audit_file}")
    print("No system tool was executed.")
    return 0


def _interrupt_runtime(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    raise SystemExit(main())
