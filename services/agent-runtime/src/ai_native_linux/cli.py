"""A demonstrator for the policy → audit part of the MVP pipeline."""

from __future__ import annotations

import argparse
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
    parser.add_argument("--serve-panel", action="store_true", help="serve the loopback panel API")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--storage-database", type=Path, default=Path("data/storage-catalog.sqlite3"))
    parser.add_argument("--index-database", type=Path, default=Path("data/document-index.sqlite3"))
    parser.add_argument(
        "--audit-file",
        type=Path,
        default=Path("data/audit-events.jsonl"),
        help="local JSONL file for redacted audit events",
    )
    args = parser.parse_args()
    if args.serve_panel:
        from ai_native_query import QueryRuntimeApplication, QueryService

        from .bridge import create_server

        application = QueryRuntimeApplication(
            QueryService(
                storage_database=args.storage_database,
                index_database=args.index_database,
            )
        )
        server = create_server(application, host=args.host, port=args.port)
        print(f"Panel runtime: http://{args.host}:{server.server_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
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
