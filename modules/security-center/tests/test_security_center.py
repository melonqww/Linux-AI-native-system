import importlib
import json
import unittest
from dataclasses import FrozenInstanceError

import ai_native_security as security
from ai_native_security import SecurityModuleStatus


EXPECTED_STATUS = {
    "schema_version": 1,
    "module_id": "security.center",
    "module_version": "0.1.0",
    "state": "ready",
    "lifecycle": "on-demand",
    "capabilities": ["security.module.status"],
}


class SecurityCenterWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        security.worker_stop()

    def tearDown(self) -> None:
        security.worker_stop()

    def test_import_does_not_start_worker(self) -> None:
        importlib.reload(security)

        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_health()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_invoke("status", {})

    def test_lifecycle_exposes_health_and_status_only_after_start(self) -> None:
        security.worker_start()

        self.assertEqual(security.worker_health(), {"status": "ready"})
        self.assertEqual(security.worker_invoke("status", {}), EXPECTED_STATUS)

        security.worker_stop()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_health()

    def test_repeated_start_and_stop_are_idempotent(self) -> None:
        security.worker_start()
        security.worker_start()
        self.assertEqual(security.worker_invoke("status", {}), EXPECTED_STATUS)

        security.worker_stop()
        security.worker_stop()
        with self.assertRaisesRegex(RuntimeError, "security_worker_not_started"):
            security.worker_invoke("status", {})

    def test_unknown_operation_is_rejected(self) -> None:
        security.worker_start()

        with self.assertRaisesRegex(ValueError, "unknown_operation"):
            security.worker_invoke("scan", {})

    def test_non_empty_and_non_object_payloads_are_rejected(self) -> None:
        security.worker_start()

        for payload in ({"unexpected": True}, [], None, ""):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "invalid_payload"):
                    security.worker_invoke("status", payload)  # type: ignore[arg-type]

    def test_status_contract_is_immutable_bounded_and_json_safe(self) -> None:
        status = SecurityModuleStatus()

        with self.assertRaises(TypeError):
            SecurityModuleStatus(state="forged")  # type: ignore[call-arg]
        with self.assertRaises(FrozenInstanceError):
            status.state = "stopped"  # type: ignore[misc]
        output = status.to_dict()
        self.assertEqual(output, EXPECTED_STATUS)
        self.assertLessEqual(len(json.dumps(output, sort_keys=True)), 512)
        self.assertEqual(json.loads(json.dumps(output)), EXPECTED_STATUS)


if __name__ == "__main__":
    unittest.main()
