import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ai_native_intents import (
    CompilationState,
    ExecutionPlan,
    PlanStep,
    RiskClass,
    TaskContextStore,
)
from ai_native_orchestrator import (
    CompiledPlanStore,
    ExecutionOrchestrator,
    OrchestrationAuditLog,
    OrchestrationState,
    PlanStoreError,
    PlanValidationError,
)
from ai_native_query import QueryResult


class FakeQueryService:
    def __init__(self):
        self.queries = []
        self.snapshots = []

    def search(self, query):
        self.queries.append(query)
        return [
            QueryResult(
                "disk-1", "/private/math.pdf", "math.pdf", ".pdf", 1.0,
                "secret snippet", 1, 1, ("content",),
            )
        ]

    def save_snapshot(self, title, results):
        self.snapshots.append((title, results))
        return "collection-trusted-1"


class BrokenQueryService(FakeQueryService):
    def search(self, query):
        raise RuntimeError("database password and private path must not escape")


def step(identifier="search", **changes):
    value = PlanStep(
        step_id=f"step_{identifier}",
        operation_id=identifier,
        capability="documents.query.search",
        arguments={"text": "математика", "extensions": ("pdf",)},
        depends_on=(),
        risk=RiskClass.READ_ONLY,
        approval_required=False,
    )
    return replace(value, **changes)


def plan(*steps):
    steps = steps or (step(),)
    return ExecutionPlan(
        plan_id=str(uuid4()),
        intent_id=str(uuid4()),
        state=CompilationState.READY,
        steps=tuple(steps),
        required_capabilities=tuple(dict.fromkeys(item.capability for item in steps)),
        missing_capabilities=(),
        approval_required=any(item.approval_required for item in steps),
    )


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.query = FakeQueryService()
        self.context = TaskContextStore()
        self.events = []
        self.orchestrator = ExecutionOrchestrator(
            self.query, self.context, audit_sink=self.events.append
        )

    def test_executes_r0_search_saves_snapshot_and_updates_context(self):
        result = self.orchestrator.execute(plan())

        self.assertEqual(result.state, OrchestrationState.COMPLETED)
        self.assertEqual(result.steps[0].output.result_count, 1)
        self.assertEqual(result.active_collection_id, "collection-trusted-1")
        self.assertEqual(self.context.snapshot().active_collection_id, "collection-trusted-1")
        self.assertEqual(self.query.queries[0].limit, 50)
        self.assertEqual(len(self.query.snapshots), 1)

    def test_stops_after_search_before_r1_copy(self):
        search = step()
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "search", "destination": "desktop"},
            depends_on=(search.step_id,),
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        result = self.orchestrator.execute(plan(search, copy))

        self.assertEqual(result.state, OrchestrationState.AWAITING_APPROVAL)
        self.assertEqual(result.pending_approval_step_ids, ("step_copy",))
        self.assertEqual(len(self.query.queries), 1)
        self.assertEqual(result.steps[1].state, "awaiting_approval")

    def test_revalidates_risk_and_dependency_order(self):
        unsafe = step(risk=RiskClass.REVERSIBLE_WRITE, approval_required=True)
        with self.assertRaisesRegex(PlanValidationError, "search_risk_mismatch"):
            self.orchestrator.execute(plan(unsafe))

        invalid_dependency = step(depends_on=("step_later",))
        with self.assertRaisesRegex(PlanValidationError, "dependency_order_invalid"):
            self.orchestrator.execute(plan(invalid_dependency))
        self.assertFalse(self.query.queries)

    def test_rejects_capability_outside_v1_allowlist(self):
        browser = step(capability="browser.search.plan")
        with self.assertRaisesRegex(PlanValidationError, "capability_not_executable"):
            self.orchestrator.execute(plan(browser))

    def test_audit_contains_metadata_but_not_query_or_paths(self):
        self.orchestrator.execute(plan())
        serialized = json.dumps(self.events, ensure_ascii=False)
        self.assertNotIn("математика", serialized)
        self.assertNotIn("/private/math.pdf", serialized)
        self.assertNotIn("secret snippet", serialized)
        self.assertIn("result_count", serialized)

    def test_language_filter_is_explicitly_reported_as_advisory(self):
        result = self.orchestrator.execute(plan(step(arguments={"text": "math", "languages": ("ru",)})))
        self.assertEqual(result.steps[0].output.warnings, ("language_filter_not_yet_applied",))

    def test_unexpected_module_error_is_redacted_and_does_not_escape(self):
        events = []
        orchestrator = ExecutionOrchestrator(
            BrokenQueryService(), self.context, audit_sink=events.append
        )

        result = orchestrator.execute(plan())

        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[0].error_code, "capability_internal_error")
        serialized = json.dumps((result, events), default=str)
        self.assertNotIn("database password", serialized)


class PlanStoreTests(unittest.TestCase):
    def test_plan_is_claimed_only_once(self):
        store = CompiledPlanStore()
        value = plan()
        store.put(value)
        self.assertIs(store.claim(value.plan_id), value)
        with self.assertRaisesRegex(PlanStoreError, "already_claimed"):
            store.claim(value.plan_id)

    def test_capacity_evicts_oldest_plan(self):
        store = CompiledPlanStore(capacity=1)
        first, second = plan(), plan()
        store.put(first)
        store.put(second)
        with self.assertRaises(PlanStoreError):
            store.claim(first.plan_id)
        self.assertIs(store.claim(second.plan_id), second)


class AuditLogTests(unittest.TestCase):
    def test_jsonl_log_adds_schema_without_sensitive_fields(self):
        root = Path(__file__).resolve().parents[3] / "tmp" / "orchestrator-audit" / str(uuid4())
        path = root / "audit.jsonl"
        try:
            OrchestrationAuditLog(path).append({"event": "completed", "plan_id": "safe"})
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["schema"], "orchestration.audit.v1")
            self.assertIn("timestamp", value)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
