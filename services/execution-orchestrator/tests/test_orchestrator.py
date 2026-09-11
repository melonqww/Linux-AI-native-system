import json
import shutil
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace
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
    DestinationResolver,
    ExecutionOrchestrator,
    OrchestrationAuditLog,
    OrchestrationState,
    PlanStoreError,
    PlanValidationError,
)
from ai_native_query import QueryResult, QueryService
from ai_native_scheduler import CoalescingEventQueue, IndexScheduler, ResourceBudget
from ai_native_permissions import TransportContext, TransportKind
from ai_native_ledger import TaskLedger, TaskState
from ai_native_storage import (
    ApprovalAuthority,
    CollectionItem,
    MaterializeService,
    VolumeRegistry,
)
from ai_native_storage.contracts import DiscoveredVolume


class SingleVolumeDiscovery:
    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self):
        return [
            DiscoveredVolume(
                "system-volume",
                "System",
                str(self.root),
                "test-device",
                "testfs",
                True,
                False,
                False,
            )
        ]


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
        arguments={
            "text": "математика",
            "content_match": "semantic",
            "extensions": ("pdf",),
        },
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
        self.root = Path(__file__).resolve().parents[3] / "tmp" / "orchestrator" / str(uuid4())
        self.desktop = self.root / "Desktop"
        self.desktop.mkdir(parents=True)
        self.source = self.root / "math.pdf"
        self.source.write_bytes(b"trusted math pdf")
        self.query = FakeQueryService()
        self.context = TaskContextStore()
        self.events = []
        self.materialize = MaterializeService(
            self.root / "storage.sqlite3", ApprovalAuthority()
        )
        self.materialize.collections.resolve = lambda _collection_id: [
            CollectionItem(
                "disk-1", str(self.source), "stable", self.source.name, available=True
            )
        ]
        self.orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            audit_sink=self.events.append,
            materialize_service=self.materialize,
            destination_resolver=DestinationResolver(
                home=self.root, roles={"desktop": self.desktop}
            ),
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

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
        self.assertEqual(result.approval_request.item_count, 1)
        self.assertEqual(result.approval_request.destination, str(self.desktop))
        self.assertEqual(result.approval_request.item_names, ("math.pdf",))
        self.assertNotIn(str(self.source), json.dumps(result.approval_request, default=str))
        self.assertFalse((self.desktop / "math.pdf").exists())

    def test_destination_child_is_validated_by_system_not_model(self):
        resolver = DestinationResolver(home=self.root, roles={"desktop": self.desktop})
        with self.assertRaises(ValueError):
            resolver.resolve("desktop", directory_name="../escape")
        with self.assertRaises(ValueError):
            resolver.resolve("C:/forged", trusted_last_destination=str(self.desktop))
        self.assertEqual(
            resolver.resolve(str(self.desktop), trusted_last_destination=str(self.desktop)),
            self.desktop,
        )

    def test_confirmation_executes_copy_once_and_updates_context(self):
        search = step()
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "search", "destination": "desktop"},
            depends_on=(search.step_id,),
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        waiting = self.orchestrator.execute(plan(search, copy))

        result = self.orchestrator.respond_to_approval(
            waiting.approval_request.approval_request_id, confirmed=True
        )

        self.assertEqual(result.state, OrchestrationState.COMPLETED)
        self.assertEqual(result.steps[-1].output.copied_count, 1)
        self.assertEqual((self.desktop / "math.pdf").read_bytes(), b"trusted math pdf")
        self.assertEqual(self.context.snapshot().last_destination, str(self.desktop))
        with self.assertRaises(ValueError):
            self.orchestrator.respond_to_approval(
                waiting.approval_request.approval_request_id, confirmed=True
            )

    def test_decline_cancels_without_writing(self):
        self.context.set_active_results("collection-trusted-1")
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "collection-trusted-1", "destination": "desktop"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        waiting = self.orchestrator.execute(plan(copy))

        result = self.orchestrator.respond_to_approval(
            waiting.approval_request.approval_request_id, confirmed=False
        )

        self.assertEqual(result.state, OrchestrationState.CANCELLED)
        self.assertFalse((self.desktop / "math.pdf").exists())
        self.assertIsNone(self.context.snapshot().last_destination)

    def test_r1_commit_is_denied_again_if_transport_is_not_secure(self):
        self.context.set_active_results("collection-trusted-1")
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "collection-trusted-1", "destination": "desktop"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        waiting = self.orchestrator.execute(plan(copy))

        result = self.orchestrator.respond_to_approval(
            waiting.approval_request.approval_request_id,
            confirmed=True,
            transport_context=TransportContext(
                TransportKind.LOOPBACK_HTTP, "loopback-http"
            ),
        )

        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[-1].error_code, "policy_transport_not_allowed")
        self.assertFalse((self.desktop / "math.pdf").exists())

    def test_changed_source_after_preview_fails_and_rolls_back(self):
        self.context.set_active_results("collection-trusted-1")
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "collection-trusted-1", "destination": "desktop"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        waiting = self.orchestrator.execute(plan(copy))
        self.source.write_bytes(b"changed after preview")

        result = self.orchestrator.respond_to_approval(
            waiting.approval_request.approval_request_id, confirmed=True
        )

        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[-1].error_code, "integrity_check_failed")
        self.assertFalse((self.desktop / "math.pdf").exists())

    def test_revalidates_risk_and_dependency_order(self):
        unsafe = step(risk=RiskClass.REVERSIBLE_WRITE, approval_required=True)
        with self.assertRaisesRegex(PlanValidationError, "step_risk_mismatch"):
            self.orchestrator.execute(plan(unsafe))

        invalid_dependency = step(depends_on=("step_later",))
        with self.assertRaisesRegex(PlanValidationError, "dependency_order_invalid"):
            self.orchestrator.execute(plan(invalid_dependency))
        self.assertFalse(self.query.queries)

    def test_copy_reference_must_be_bound_to_its_dependency(self):
        search = step()
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "search", "destination": "desktop"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        with self.assertRaisesRegex(PlanValidationError, "result_dependency_missing"):
            self.orchestrator.execute(plan(search, copy))

    def test_rejects_capability_outside_v1_allowlist(self):
        browser = step(capability="browser.search.plan")
        with self.assertRaisesRegex(PlanValidationError, "capability_has_no_trusted_policy"):
            self.orchestrator.execute(plan(browser))

    def test_gateway_denies_missing_scope_before_handler(self):
        orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            materialize_service=self.materialize,
            destination_resolver=DestinationResolver(
                home=self.root, roles={"desktop": self.desktop}
            ),
            granted_scopes=frozenset(),
        )

        result = orchestrator.execute(plan())

        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[0].error_code, "policy_scope_not_granted")
        self.assertFalse(self.query.queries)

    def test_gateway_rechecks_module_availability_after_compile(self):
        available = {"documents.query.search", "storage.materialize.plan-copy"}
        orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            materialize_service=self.materialize,
            destination_resolver=DestinationResolver(
                home=self.root, roles={"desktop": self.desktop}
            ),
            capability_source=lambda: available,
        )
        value = plan()
        available.remove("documents.query.search")

        result = orchestrator.execute(value)

        self.assertEqual(result.steps[0].error_code, "policy_capability_unavailable")
        self.assertFalse(self.query.queries)

    def test_gateway_rechecks_scope_revocation_after_compile(self):
        value = plan()
        self.orchestrator.scope_grants.revoke("filesystem.read-content")

        result = self.orchestrator.execute(value)

        self.assertEqual(result.steps[0].error_code, "policy_scope_not_granted")
        self.assertFalse(self.query.queries)

    def test_audit_contains_metadata_but_not_query_or_paths(self):
        self.orchestrator.execute(plan())
        serialized = json.dumps(self.events, ensure_ascii=False)
        self.assertNotIn("математика", serialized)
        self.assertNotIn("/private/math.pdf", serialized)
        self.assertNotIn("secret snippet", serialized)
        self.assertIn("result_count", serialized)
        decisions = [event for event in self.events if event["event"] == "permission.decision"]
        self.assertEqual(decisions[0]["decision"], "allow")
        self.assertEqual(decisions[0]["risk"], "R0")
        self.assertNotIn("arguments", decisions[0])

    def test_unimplemented_language_filter_is_rejected_instead_of_ignored(self):
        result = self.orchestrator.execute(plan(step(arguments={"text": "math", "languages": ("ru",)})))
        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[0].error_code, "policy_arguments_not_allowed")
        self.assertFalse(self.query.queries)

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
        self.assertTrue(
            any(
                event["event"] == "permission.decision"
                and event["decision"] == "allow"
                for event in events
            )
        )

    def test_task_ledger_receives_only_user_lifecycle(self):
        ledger = TaskLedger(self.root / "tasks.sqlite3")
        orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            materialize_service=self.materialize,
            destination_resolver=DestinationResolver(
                home=self.root, roles={"desktop": self.desktop}
            ),
            task_ledger=ledger,
        )

        result = orchestrator.execute(plan())
        task = ledger.get(result.run_id)

        self.assertEqual(task.state, TaskState.COMPLETED)
        self.assertEqual(task.activity, "documents.search")
        self.assertEqual(task.processed_count, 1)
        self.assertEqual(task.references[0].display_name, "math.pdf")
        self.assertEqual(
            ledger.event_names(result.run_id),
            ("created", "started", "item_succeeded", "completed"),
        )

    def test_enrolled_disk_indexes_real_pdf_result_with_coverage_and_ledger_link(self):
        documents = self.root / "indexed-documents"
        documents.mkdir()
        pdf = documents / "mathematics.pdf"
        pdf.write_bytes(b"injected PDF")
        storage_db = self.root / "pipeline-storage.sqlite3"
        index_db = self.root / "pipeline-index.sqlite3"
        VolumeRegistry(
            storage_db, discovery=SingleVolumeDiscovery(documents)
        ).refresh()

        class Extractor:
            def extract(self, _path):
                return SimpleNamespace(text="математика алгебра формула")

        scheduler = IndexScheduler(
            storage_database=storage_db,
            index_database=index_db,
            queue=CoalescingEventQueue(debounce_seconds=0),
            budget=ResourceBudget(max_batch=2, load_probe=lambda: 0),
            pdf_extractor=Extractor(),
        )
        scheduler.request_rescan("system-volume")
        for cycle in range(1, 20):
            coverage = scheduler.process_once(now=cycle)
            if coverage.coverage_complete:
                break
        query = QueryService(
            storage_database=storage_db,
            index_database=index_db,
            coverage_source=lambda: asdict(scheduler.status()),
        )
        ledger = TaskLedger(self.root / "pipeline-tasks.sqlite3")
        orchestrator = ExecutionOrchestrator(
            query, TaskContextStore(), task_ledger=ledger
        )

        result = orchestrator.execute(plan())
        output = result.steps[0].output
        task = ledger.get(result.run_id)

        self.assertEqual(result.state, OrchestrationState.COMPLETED)
        self.assertEqual(output.result_count, 1)
        self.assertTrue(output.coverage.complete)
        self.assertEqual(task.processed_count, 1)
        self.assertEqual(task.references[0].locator, str(pdf))

    def test_task_ledger_tracks_approval_and_terminal_user_cancel(self):
        ledger = TaskLedger(self.root / "tasks.sqlite3")
        orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            materialize_service=self.materialize,
            destination_resolver=DestinationResolver(
                home=self.root, roles={"desktop": self.desktop}
            ),
            task_ledger=ledger,
        )
        self.context.set_active_results("collection-trusted-1")
        copy = step(
            "copy",
            capability="storage.materialize.plan-copy",
            arguments={"results_from": "collection-trusted-1", "destination": "desktop"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )

        waiting = orchestrator.execute(plan(copy))
        self.assertEqual(ledger.get(waiting.run_id).state, TaskState.AWAITING_APPROVAL)
        result = orchestrator.respond_to_approval(
            waiting.approval_request.approval_request_id, confirmed=False
        )

        task = ledger.get(result.run_id)
        self.assertEqual(task.state, TaskState.CANCELLED)
        self.assertFalse(task.can_continue)
        self.assertEqual(
            ledger.event_names(result.run_id),
            ("created", "started", "awaiting_approval", "cancelled"),
        )

    def test_task_ledger_failure_does_not_store_internal_reason(self):
        ledger = TaskLedger(self.root / "tasks.sqlite3")
        orchestrator = ExecutionOrchestrator(
            BrokenQueryService(), self.context, task_ledger=ledger
        )

        result = orchestrator.execute(plan())
        task = ledger.get(result.run_id)

        self.assertEqual(task.state, TaskState.FAILED)
        serialized = json.dumps(
            {"task": task, "events": ledger.event_names(result.run_id)},
            default=str,
        )
        self.assertNotIn("database password", serialized)
        self.assertNotIn("capability_internal_error", serialized)
        self.assertEqual(ledger.event_names(result.run_id), ("created", "started", "failed"))


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
