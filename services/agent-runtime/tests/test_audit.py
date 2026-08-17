"""Tests for the metadata-only local audit log."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_native_linux.audit import JsonlAuditLog, create_audit_event
from ai_native_linux.policy import PolicyEngine


def allowed_decision():
    payload = {
        "request_id": str(uuid4()),
        "intent": "get_system_status",
        "arguments": {"metrics": ["disk"], "process_limit": 1},
        "reason": "Read-only test.",
    }
    return PolicyEngine().evaluate(payload)


class AuditLogTests(unittest.TestCase):
    def test_writes_metadata_only_event_as_jsonl(self) -> None:
        proposal, decision = allowed_decision()
        event = create_audit_event(intent=proposal.intent, decision=decision, result="success")

        path = Path("data") / "audit" / "events.jsonl"
        stream = mock_open()
        with patch.object(Path, "mkdir") as make_directory, patch.object(Path, "open", stream):
            JsonlAuditLog(path).append(event)
        written = "".join(call.args[0] for call in stream().write.call_args_list)
        record = json.loads(written)

        make_directory.assert_called_once_with(parents=True, exist_ok=True)
        self.assertEqual(record["request_id"], proposal.request_id)
        self.assertEqual(record["tool"], "ubuntu.system.status.read")
        self.assertNotIn("reason", record)
        self.assertNotIn("arguments", record)
