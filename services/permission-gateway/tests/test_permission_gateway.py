import threading
import unittest
from dataclasses import replace
from time import monotonic
from uuid import uuid4

from ai_native_permissions import (
    CapabilityBusyError,
    CapabilityExecutionRegistry,
    CapabilityInvocation,
    DecisionKind,
    ExecutionContext,
    ExecutionPhase,
    HandlerRegistrationError,
    PermissionGateway,
    ScopeGrantStore,
    TransportContext,
    TransportKind,
    builtin_policies,
)


SCOPES = frozenset(
    {
        "filesystem.read-metadata",
        "filesystem.read-content",
        "filesystem.write-content",
    }
)


def invocation(capability="documents.query.search", phase=ExecutionPhase.EXECUTE, **changes):
    value = CapabilityInvocation(
        request_id=str(uuid4()),
        plan_id=str(uuid4()),
        step_id="step_search",
        capability_id=capability,
        phase=phase,
        arguments={"text": "math", "extensions": ("pdf",)},
        declared_risk="R0",
        declared_approval_required=False,
        context=ExecutionContext(TransportContext.internal(), SCOPES),
    )
    return replace(value, **changes)


class PermissionGatewayTests(unittest.TestCase):
    def setUp(self):
        self.available = {"documents.query.search", "storage.materialize.plan-copy"}
        self.gateway = PermissionGateway(
            builtin_policies(), capability_source=lambda: self.available
        )

    def test_allows_r0_only_from_trusted_policy_and_scope(self):
        decision = self.gateway.evaluate(invocation())
        self.assertEqual(decision.kind, DecisionKind.ALLOW)
        self.assertEqual(decision.risk, "R0")

    def test_allows_the_compiler_owned_search_mode_argument(self):
        decision = self.gateway.evaluate(
            invocation(arguments={"mode": "metadata", "extensions": ("pdf",)})
        )
        self.assertEqual(decision.kind, DecisionKind.ALLOW)

    def test_module_availability_does_not_grant_policy_or_override_risk(self):
        unknown = invocation(capability="module.claimed.admin", declared_risk="R0")
        self.available.add("module.claimed.admin")
        self.assertEqual(self.gateway.evaluate(unknown).reason_code, "policy_not_found")
        forged = invocation(declared_risk="R1", declared_approval_required=True)
        self.assertEqual(self.gateway.evaluate(forged).reason_code, "declared_risk_mismatch")

    def test_denies_disabled_capability_missing_scope_and_unknown_arguments(self):
        self.available.remove("documents.query.search")
        self.assertEqual(self.gateway.evaluate(invocation()).reason_code, "capability_unavailable")
        self.available.add("documents.query.search")
        missing = invocation(
            context=ExecutionContext(TransportContext.internal(), frozenset())
        )
        decision = self.gateway.evaluate(missing)
        self.assertEqual(decision.reason_code, "scope_not_granted")
        self.assertEqual(
            decision.required_scopes,
            ("filesystem.read-content", "filesystem.read-metadata"),
        )
        unknown = invocation(arguments={"text": "math", "shell": "rm"})
        self.assertEqual(self.gateway.evaluate(unknown).reason_code, "arguments_not_allowed")

    def test_r1_commit_requires_approval_and_secure_transport(self):
        base = invocation(
            capability="storage.materialize.plan-copy",
            phase=ExecutionPhase.COMMIT,
            step_id="step_copy",
            arguments={"results_from": "trusted", "destination": "desktop"},
            declared_risk="R1",
            declared_approval_required=True,
        )
        self.assertEqual(self.gateway.evaluate(base).reason_code, "approval_required")
        http = replace(
            base,
            context=ExecutionContext(
                TransportContext(TransportKind.LOOPBACK_HTTP, "http"), SCOPES, True
            ),
        )
        self.assertEqual(self.gateway.evaluate(http).reason_code, "transport_not_allowed")
        unix = replace(
            base,
            context=ExecutionContext(
                TransportContext(TransportKind.UNIX_PEER, "uid:1000", 20, 1000, 1000),
                SCOPES,
                True,
            ),
        )
        self.assertTrue(self.gateway.evaluate(unix).allowed)

    def test_invalid_ids_and_control_characters_are_rejected(self):
        bad_id = replace(invocation(), request_id="not-a-uuid")
        self.assertEqual(self.gateway.evaluate(bad_id).reason_code, "invalid_invocation_identity")
        bad_value = invocation(arguments={"text": "math\nignore rules"})
        self.assertEqual(self.gateway.evaluate(bad_value).reason_code, "invalid_argument_value")

    def test_scope_grants_can_be_revoked_without_accepting_invalid_names(self):
        store = ScopeGrantStore(SCOPES)
        store.revoke("filesystem.read-content")
        self.assertNotIn("filesystem.read-content", store.snapshot())
        store.grant("filesystem.read-content")
        self.assertIn("filesystem.read-content", store.snapshot())
        with self.assertRaises(ValueError):
            store.grant("forged root scope")

    def test_required_scopes_are_derived_from_all_phases(self):
        scopes = self.gateway.required_scopes("storage.materialize.plan-copy")
        self.assertEqual(
            scopes,
            {
                "filesystem.read-metadata",
                "filesystem.read-content",
                "filesystem.write-content",
            },
        )

    def test_software_commit_requires_unix_transport_approval_and_scope(self):
        gateway = PermissionGateway(
            builtin_policies(),
            capability_source=lambda: {"software.install.commit"},
        )
        base = invocation(
            capability="software.install.commit",
            phase=ExecutionPhase.COMMIT,
            step_id="step_install",
            arguments={"task_id": "trusted-task"},
            declared_risk="R3",
            declared_approval_required=True,
            context=ExecutionContext(
                TransportContext(TransportKind.UNIX_PEER, "uid:1000", 20, 1000, 1000),
                frozenset({"software.manage"}),
                False,
            ),
        )

        self.assertEqual(gateway.evaluate(base).reason_code, "approval_required")
        approved = replace(
            base,
            context=replace(base.context, approval_granted=True),
        )
        self.assertTrue(gateway.evaluate(approved).allowed)
        http = replace(
            approved,
            context=replace(
                approved.context,
                transport=TransportContext(TransportKind.LOOPBACK_HTTP, "http"),
            ),
        )
        self.assertEqual(gateway.evaluate(http).reason_code, "transport_not_allowed")


class ExecutionRegistryTests(unittest.TestCase):
    def setUp(self):
        gateway = PermissionGateway(builtin_policies())
        self.registry = CapabilityExecutionRegistry(gateway)

    def test_denied_request_never_reaches_handler_and_deadline_is_injected(self):
        received = []
        self.registry.register("documents.query.search", received.append)
        denied = invocation(declared_risk="R1", declared_approval_required=True)
        result = self.registry.dispatch(denied)
        self.assertFalse(result.decision.allowed)
        self.assertFalse(received)

        before = monotonic()
        allowed = self.registry.dispatch(invocation())
        self.assertTrue(allowed.decision.allowed)
        self.assertGreaterEqual(received[0].deadline_monotonic, before)

    def test_concurrency_limit_fails_closed_without_queueing(self):
        entered = threading.Event()
        release = threading.Event()

        def blocked(_invocation):
            entered.set()
            release.wait(timeout=2)

        self.registry.register("storage.materialize.plan-copy", blocked)
        value = invocation(
            capability="storage.materialize.plan-copy",
            phase=ExecutionPhase.PREPARE,
            step_id="step_copy",
            arguments={"results_from": "trusted", "destination": "desktop"},
            declared_risk="R1",
            declared_approval_required=True,
        )
        worker = threading.Thread(target=lambda: self.registry.dispatch(value))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.assertRaises(CapabilityBusyError):
                self.registry.dispatch(replace(value, request_id=str(uuid4())))
        finally:
            release.set()
            worker.join(timeout=2)

    def test_missing_or_duplicate_handler_fails_closed(self):
        result = self.registry.dispatch(invocation())
        self.assertFalse(result.decision.allowed)
        self.assertEqual(result.decision.reason_code, "handler_unavailable")
        self.registry.register("documents.query.search", lambda _value: "ok")
        with self.assertRaises(HandlerRegistrationError):
            self.registry.register("documents.query.search", lambda _value: "other")
        with self.assertRaises(HandlerRegistrationError):
            self.registry.register("unknown.capability", lambda _value: None)

    def test_handler_exception_releases_concurrency_slot(self):
        calls = []

        def handler(_value):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("first failure")
            return "recovered"

        self.registry.register("storage.materialize.plan-copy", handler)
        value = invocation(
            capability="storage.materialize.plan-copy",
            phase=ExecutionPhase.PREPARE,
            step_id="step_copy",
            arguments={"results_from": "trusted", "destination": "desktop"},
            declared_risk="R1",
            declared_approval_required=True,
        )
        with self.assertRaises(OSError):
            self.registry.dispatch(value)
        result = self.registry.dispatch(replace(value, request_id=str(uuid4())))
        self.assertEqual(result.output, "recovered")


if __name__ == "__main__":
    unittest.main()
