"""A demonstrator for the policy → audit part of the MVP pipeline."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from uuid import uuid4

from .audit import JsonlAuditLog, create_audit_event
from .policy import PolicyEngine


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
    parser.add_argument("--demo", action="store_true", help="validate the fixed read-only demo request")
    parser.add_argument("--serve-panel", action="store_true", help="serve the panel runtime API")
    parser.add_argument(
        "--transport",
        choices=("auto", "unix", "http"),
        default="auto",
        help="auto uses authenticated Unix IPC on Linux and loopback HTTP elsewhere",
    )
    parser.add_argument("--socket-path", type=Path, help="override the Linux Unix socket path")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--storage-database", type=Path, default=Path("data/storage-catalog.sqlite3"))
    parser.add_argument("--index-database", type=Path, default=Path("data/document-index.sqlite3"))
    parser.add_argument("--registry-database", type=Path, default=Path("data/capabilities.sqlite3"))
    parser.add_argument("--task-ledger-database", type=Path, default=Path("data/task-ledger.sqlite3"))
    parser.add_argument("--memory-database", type=Path, default=Path("data/task-memory.sqlite3"))
    parser.add_argument("--workspace-database", type=Path, default=Path("data/workspace.sqlite3"))
    parser.add_argument("--intent-model", default="qwen3:1.7b")
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
        from ai_native_query import QueryRuntimeApplication, QueryService
        from ai_native_workspace import WorkspaceStore

        roots = args.module_roots or [Path("services"), Path("modules")]
        registry = CapabilityRegistry(args.registry_database)
        report = registry.sync(roots)
        if report.issues:
            parser.error(f"module manifest errors: {report.issues}")
        manager = ModuleProcessManager(
            registry,
            storage_database=args.storage_database,
            index_database=args.index_database,
        )
        server = None
        workspace_controller = None
        try:
            manager.start_for_capability("storage.watch.events")
            system_monitor_status = None
            try:
                monitor_module_id = manager.start_for_capability("system.monitor.snapshot")
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

            query_service = QueryService(
                storage_database=args.storage_database,
                index_database=args.index_database,
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
            if not args.no_intent_compiler:
                from ai_native_intents import (
                    IntentCompiler,
                    OllamaModelProvider,
                    TaskContextStore,
                )

                provider = OllamaModelProvider(
                    model=args.intent_model,
                    base_url=args.ollama_url,
                    timeout_seconds=args.intent_timeout,
                    context_tokens=args.intent_context_tokens,
                )
                model_health = provider.health()
                state = "ready" if model_health.available else f"unavailable ({model_health.reason})"
                print(f"Intent model {args.intent_model}: {state}")
                intent_pipeline = IntentCompiler(
                    provider,
                    capability_source=registry.available_capabilities,
                )
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
                from ai_native_workspace import WorkspaceRuntime

                workspace_controller = WorkspaceRuntime(
                    workspace,
                    provider,
                    intent_pipeline,
                    plan_executor,
                    context_store.snapshot,
                    model_status=model_status,
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
            )
            transport = (
                "unix" if args.transport == "auto" and sys.platform.startswith("linux")
                else "http" if args.transport == "auto"
                else args.transport
            )
            if transport == "unix":
                from .unix_socket import create_unix_server

                server = create_unix_server(application, args.socket_path)
                print(f"Panel runtime: unix://{server.socket_path}")
            else:
                from .bridge import create_server

                server = create_server(application, host=args.host, port=args.port)
                print(f"Panel runtime (development fallback): http://{args.host}:{server.server_port}")
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
    event = create_audit_event(intent=proposal.intent, decision=decision, result="validated_no_execution")
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
