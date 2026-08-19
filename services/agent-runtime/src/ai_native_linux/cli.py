"""A demonstrator for the policy → audit part of the MVP pipeline."""

from __future__ import annotations

import argparse
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
    parser.add_argument("--intent-model", default="qwen3:1.7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--intent-timeout", type=float, default=45.0)
    parser.add_argument("--intent-context-tokens", type=int, default=4_096)
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
        from ai_native_module_manager import ModuleProcessManager
        from ai_native_query import QueryRuntimeApplication, QueryService

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
        try:
            manager.start_for_capability("storage.watch.events")
            query_service = QueryService(
                storage_database=args.storage_database,
                index_database=args.index_database,
            )
            intent_pipeline = None
            task_context = None
            plan_store = None
            plan_executor = None
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
                context_store = TaskContextStore(locale=args.locale)
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
                )
            application = QueryRuntimeApplication(
                query_service,
                scheduler_status=lambda: manager.health_details("storage.watch"),
                intent_pipeline=intent_pipeline,
                task_context=task_context,
                plan_store=plan_store,
                plan_executor=plan_executor,
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
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server is not None:
                server.server_close()
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


if __name__ == "__main__":
    raise SystemExit(main())
