import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ai_native_capabilities import CapabilityRegistry
from ai_native_file_operations import FileOperationsService, capability_handlers
from ai_native_intents import (
    CompilationState,
    ExecutionPlan,
    PlanStep,
    RiskClass,
    TaskContextStore,
    build_operation_definitions,
)
from ai_native_orchestrator import ExecutionOrchestrator, OrchestrationState
from ai_native_permissions import TransportContext, TransportKind
from ai_native_query import QueryResult


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _Query:
    def __init__(self, source: Path) -> None:
        self.source = source

    def search(self, _query):
        return [
            QueryResult(
                "disk-1",
                str(self.source),
                self.source.name,
                self.source.suffix,
                1.0,
                None,
                1,
                1,
                ("metadata",),
            )
        ]

    def save_snapshot(self, _title, _results):
        return "trusted-search-results"


def _step(identifier: str, capability: str, arguments: dict[str, object], **changes):
    value = PlanStep(
        step_id=f"step_{identifier}",
        operation_id=identifier,
        capability=capability,
        arguments=arguments,
        depends_on=(),
        risk=RiskClass.READ_ONLY,
        approval_required=False,
    )
    return replace(value, **changes)


def _plan(*steps: PlanStep) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=str(uuid4()),
        intent_id=str(uuid4()),
        state=CompilationState.READY,
        steps=steps,
        required_capabilities=tuple(dict.fromkeys(item.capability for item in steps)),
        missing_capabilities=(),
        approval_required=any(item.approval_required for item in steps),
    )


class FileOperationsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = (PROJECT_ROOT / "tmp" / "file-integration" / str(uuid4())).resolve()
        self.desktop = self.root / "Desktop"
        self.documents = self.root / "Documents"
        self.downloads = self.root / "Downloads"
        for directory in (self.desktop, self.documents, self.downloads):
            directory.mkdir(parents=True, exist_ok=True)
        self.source = self.documents / "report.pdf"
        self.source.write_bytes(b"pdf")
        self.selections = {"trusted-search-results": (self.source,)}
        service = FileOperationsService(
            destination_roots={
                "desktop": self.desktop,
                "documents": self.documents,
                "downloads": self.downloads,
            },
            trash_root=self.root / "Trash",
            selection_resolver=lambda key: self.selections[key],
        )
        self.handlers = capability_handlers(service)
        self.context = TaskContextStore()
        self.executor = ExecutionOrchestrator(
            _Query(self.source),
            self.context,
            capability_source=lambda: (
                "documents.query.search",
                *self.handlers,
            ),
            capability_handlers=self.handlers,
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_enabled_handlers_are_visible_through_executor_intersection(self):
        self.assertEqual(
            set(self.executor.available_capabilities()),
            {
                "documents.query.search",
                "files.items.inspect",
                "files.directory.create",
                "files.items.move",
                "files.items.rename",
                "files.items.trash",
            },
        )

    def test_search_then_inspect_returns_verified_metadata_without_host_path(self):
        search = _step(
            "search", "documents.query.search", {"mode": "metadata", "extensions": ("pdf",)}
        )
        inspect = _step(
            "inspect", "files.items.inspect", {"results_from": "search"},
            depends_on=("step_search",),
        )

        completed = self.executor.execute(_plan(search, inspect))

        self.assertEqual(completed.state, OrchestrationState.COMPLETED, completed)
        output = completed.steps[-1].output
        self.assertEqual(output.action, "inspect_files")
        self.assertEqual(output.item_count, 1)
        self.assertIn("report.pdf", output.details["summary_ru"])
        self.assertIn("3 bytes", output.details["summary_en"])
        self.assertNotIn(str(self.root), repr(output.details))

    def test_inspect_unpermitted_collection_reports_stable_code(self):
        archive = self.root / "Archive"
        archive.mkdir()
        outside = archive / "private.pdf"
        outside.write_bytes(b"private")
        self.selections["trusted-search-results"] = (outside,)
        search = _step(
            "search", "documents.query.search", {"mode": "metadata", "extensions": ("pdf",)}
        )
        inspect = _step(
            "inspect", "files.items.inspect", {"results_from": "search"},
            depends_on=("step_search",),
        )

        result = self.executor.execute(_plan(search, inspect))

        self.assertEqual(result.state, OrchestrationState.FAILED)
        self.assertEqual(result.steps[-1].error_code, "selection_outside_allowed_roots")
        self.assertNotIn(str(outside), repr(result.diagnostics))

    def test_registry_catalog_policy_and_handler_form_one_operation_catalog(self):
        registry = CapabilityRegistry(self.root / "capabilities.sqlite3")
        report = registry.sync([PROJECT_ROOT / "services", PROJECT_ROOT / "modules"])
        self.assertEqual(report.issues, ())
        executor = ExecutionOrchestrator(
            _Query(self.source),
            TaskContextStore(),
            capability_source=registry.available_capabilities,
            capability_handlers=self.handlers,
        )

        definitions = build_operation_definitions(
            registry.capability_contracts(),
            available_capabilities=executor.available_capabilities(),
            policy_source=executor.permission_gateway.policy,
        )

        self.assertEqual(
            {definition.operation for definition in definitions},
            {
                "search_documents",
                "copy_results",
                "inspect_files",
                "create_directory",
                "move_results",
                "rename_item",
                "trash_results",
            },
        )

    def test_create_directory_waits_for_consent_and_commits_once(self):
        create = _step(
            "create",
            "files.directory.create",
            {"destination": "desktop", "directory_name": "Private"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        pending = self.executor.execute(_plan(create))

        self.assertEqual(pending.state, OrchestrationState.AWAITING_APPROVAL)
        self.assertEqual(pending.approval_request.item_names, ("Private",))
        self.assertFalse((self.desktop / "Private").exists())

        completed = self.executor.respond_to_approval(
            pending.approval_request.approval_request_id, confirmed=True
        )
        self.assertEqual(completed.state, OrchestrationState.COMPLETED)
        self.assertEqual(completed.steps[-1].output.action, "create_directory")
        self.assertTrue((self.desktop / "Private").is_dir())

    def test_decline_cancels_prepared_operation_without_effect(self):
        create = _step(
            "create",
            "files.directory.create",
            {"destination": "desktop", "directory_name": "Nope"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        pending = self.executor.execute(_plan(create))

        cancelled = self.executor.respond_to_approval(
            pending.approval_request.approval_request_id, confirmed=False
        )

        self.assertEqual(cancelled.state, OrchestrationState.CANCELLED)
        self.assertFalse((self.desktop / "Nope").exists())

    def test_loopback_transport_cannot_commit_a_file_mutation(self):
        create = _step(
            "create",
            "files.directory.create",
            {"destination": "desktop", "directory_name": "Blocked"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )
        pending = self.executor.execute(_plan(create))

        denied = self.executor.respond_to_approval(
            pending.approval_request.approval_request_id,
            confirmed=True,
            transport_context=TransportContext(
                TransportKind.LOOPBACK_HTTP, "panel-test"
            ),
        )

        self.assertEqual(denied.state, OrchestrationState.FAILED)
        self.assertIn("policy_transport_not_allowed", denied.diagnostics)
        self.assertFalse((self.desktop / "Blocked").exists())

    def test_trusted_last_destination_is_accepted_without_model_owned_path(self):
        prior = self.desktop / "Prior"
        prior.mkdir()
        self.context.set_last_destination(str(prior))
        create = _step(
            "create",
            "files.directory.create",
            {"destination": str(prior), "directory_name": "Child"},
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )

        pending = self.executor.execute(_plan(create))
        completed = self.executor.respond_to_approval(
            pending.approval_request.approval_request_id, confirmed=True
        )

        self.assertEqual(completed.state, OrchestrationState.COMPLETED)
        self.assertTrue((prior / "Child").is_dir())

    def test_search_reference_is_resolved_by_core_before_module_move(self):
        search = _step(
            "search",
            "documents.query.search",
            {"extensions": ("pdf",), "mode": "metadata"},
        )
        move = _step(
            "move",
            "files.items.move",
            {"results_from": "search", "destination": "desktop"},
            depends_on=(search.step_id,),
            risk=RiskClass.REVERSIBLE_WRITE,
            approval_required=True,
        )

        pending = self.executor.execute(_plan(search, move))
        self.assertEqual(pending.state, OrchestrationState.AWAITING_APPROVAL)
        self.assertEqual(pending.approval_request.item_names, ("report.pdf",))

        completed = self.executor.respond_to_approval(
            pending.approval_request.approval_request_id, confirmed=True
        )
        self.assertEqual(completed.state, OrchestrationState.COMPLETED)
        self.assertFalse(self.source.exists())
        self.assertTrue((self.desktop / "report.pdf").is_file())


if __name__ == "__main__":
    unittest.main()
