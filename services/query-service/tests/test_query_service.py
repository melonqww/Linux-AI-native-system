import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


PROJECT_ROOT = Path(__file__).resolve().parents[3]
for source in (
    PROJECT_ROOT / "services" / "storage-catalog" / "src",
    PROJECT_ROOT / "services" / "indexer" / "src",
    PROJECT_ROOT / "modules" / "documents-pdf" / "src",
):
    sys.path.insert(0, str(source))

from ai_native_intents import CompilationResult, CompilationState, TaskContext
from ai_native_permissions import TransportContext
from ai_native_query import (
    ContentAvailability,
    DocumentQuery,
    QueryRuntimeApplication,
    QueryService,
    SearchMode,
)
from ai_native_storage import PermissionLevel, VolumeRegistry
from ai_native_storage.contracts import DiscoveredVolume


class Discovery:
    def __init__(self, volume: DiscoveredVolume) -> None:
        self.volume = volume

    def discover(self):
        return [self.volume]


class MultiDiscovery:
    def __init__(self, volumes):
        self.volumes = volumes

    def discover(self):
        return list(self.volumes)


class FakeIntentPipeline:
    def __init__(self):
        self.context = None

    def compile_and_plan(self, text, *, context):
        self.context = context
        return CompilationResult(CompilationState.NEEDS_CLARIFICATION, None, None, text)


class FakePlanStore:
    def __init__(self):
        self.plan = None

    def put(self, plan):
        self.plan = plan

    def claim(self, plan_id):
        return self.plan


class FakeExecutor:
    def available_capabilities(self):
        return ("storage.materialize.plan-copy",)

    def execute(self, plan, *, transport_context=None):
        return {"executed": plan}

    def respond_to_approval(
        self, approval_request_id, *, confirmed, transport_context=None
    ):
        return {"approval_request_id": approval_request_id, "confirmed": confirmed}


class FakeWorkspace:
    def __init__(self):
        self.message_limit = None
        self.run_options = None

    def list_messages(self, *, limit=200):
        self.message_limit = limit
        return ("message",)

    def list_runs(self, *, limit=20, active_only=False):
        self.run_options = (limit, active_only)
        return ("run",)

    def run(self, run_id):
        return f"run:{run_id}"


class FakeWorkspaceController:
    def __init__(self):
        self.transport = None

    def submit(self, text, *, transport_context):
        self.transport = transport_context
        return f"submitted:{text}"

    def respond_to_approval(
        self, approval_request_id, *, confirmed, transport_context
    ):
        self.transport = transport_context
        return (approval_request_id, confirmed)


class QueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = PROJECT_ROOT / "tmp" / "query-service-tests" / str(uuid4())
        self.documents = self.root / "documents"
        self.documents.mkdir(parents=True)
        self.storage_db = self.root / "storage.sqlite3"
        self.index_db = self.root / "index.sqlite3"
        volume = DiscoveredVolume(
            "test-volume", "Documents", str(self.documents), "test", "testfs", True, False, False
        )
        VolumeRegistry(self.storage_db, discovery=Discovery(volume)).refresh()
        self.service = QueryService(
            storage_database=self.storage_db,
            index_database=self.index_db,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def make_pdf(self, text: str, name: str = "study.pdf") -> Path:
        path = self.documents / name
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_reference = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)
        return path

    def test_ingests_pdf_searches_content_and_saves_snapshot(self) -> None:
        self.make_pdf("Mathematics algebra geometry")
        self.make_pdf("Cooking recipes and ingredients", "cooking.pdf")
        self.service.catalog.scan_volume("test-volume")

        report = self.service.ingest_pdfs()
        results = self.service.search(DocumentQuery(text="algebra", extensions=("pdf",)))
        collection_id = self.service.save_snapshot("Math PDF", results)

        self.assertEqual(report.indexed, 2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "study.pdf")
        self.assertIn("PDF page 1", results[0].snippet or "")
        self.assertEqual(len(self.service.collections.resolve(collection_id)), 1)

    def test_runtime_adapter_validates_payload(self) -> None:
        application = QueryRuntimeApplication(self.service)
        self.assertIn("documents.query.search", application.capabilities())
        self.assertIn("catalog", application.index_status())
        page = application.search_page(
            {"text": "", "mode": "metadata", "extensions": ["pdf"], "offset": 0}
        )
        self.assertEqual(page.total_matches, 0)
        with self.assertRaises(ValueError):
            application.search({"text": [], "limit": 20})
        with self.assertRaises(ValueError):
            application.search_page({"text": "", "unexpected": True})

    def test_runtime_exposes_storage_enrollment_contract(self) -> None:
        application = QueryRuntimeApplication(self.service)

        response = application.storage_volumes({"refresh": True})
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["volumes"][0]["volume_id"], "test-volume")
        self.assertFalse(response["volumes"][0]["permission_required"])
        self.assertIn("storage.volumes.read", application.capabilities())
        self.assertIn("storage.volumes.enroll", application.capabilities())
        disabled = application.storage_permission(
            {"volume_id": "test-volume", "permission": "none"}
        )
        self.assertEqual(disabled["permission"], PermissionLevel.NONE)

    def test_metadata_mode_lists_all_pdfs_without_content_terms(self) -> None:
        self.make_pdf("Mathematics algebra geometry")
        self.make_pdf("Cooking recipes", "cooking.pdf")
        self.service.catalog.scan_volume("test-volume")

        results = self.service.search(
            DocumentQuery(mode=SearchMode.METADATA, extensions=("pdf",))
        )

        self.assertEqual({item.name for item in results}, {"study.pdf", "cooking.pdf"})
        self.assertTrue(all(item.sources == ("metadata",) for item in results))

    def test_metadata_results_explain_broken_pdf_content_state(self) -> None:
        self.make_pdf("Mathematics algebra geometry")
        broken = self.documents / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\ntruncated")
        self.service.catalog.scan_volume("test-volume")

        report = self.service.ingest_pdfs()
        results = self.service.search(
            DocumentQuery(mode=SearchMode.METADATA, extensions=("pdf",), limit=10)
        )
        by_name = {result.name: result for result in results}

        self.assertEqual(report.discovered, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(by_name["study.pdf"].content_state, ContentAvailability.INDEXED)
        self.assertEqual(by_name["broken.pdf"].content_state, ContentAvailability.UNAVAILABLE)
        self.assertEqual(by_name["broken.pdf"].content_reason, "damaged")
        self.assertEqual(by_name["broken.pdf"].size_bytes, broken.stat().st_size)

    def test_hybrid_mode_applies_name_as_filter_to_content_hits(self) -> None:
        self.make_pdf("Shared algebra theorem", "mathematics.pdf")
        self.make_pdf("Shared algebra theorem", "cooking.pdf")
        self.service.catalog.scan_volume("test-volume")
        self.service.ingest_pdfs()

        results = self.service.search(
            DocumentQuery(
                mode=SearchMode.HYBRID,
                text="algebra",
                name_contains=("cooking",),
                extensions=("pdf",),
            )
        )

        self.assertEqual([result.name for result in results], ["cooking.pdf"])

    def test_search_page_reports_exact_total_and_stable_metadata_pages(self) -> None:
        for number in range(5):
            (self.documents / f"document-{number}.txt").write_text(
                f"content {number}", encoding="utf-8"
            )
        self.service.catalog.scan_volume("test-volume")

        page = self.service.search_page(
            DocumentQuery(
                mode=SearchMode.METADATA,
                extensions=("txt",),
                limit=2,
                offset=2,
            )
        )

        self.assertEqual(page.total_matches, 5)
        self.assertTrue(page.total_is_exact)
        self.assertEqual(page.offset, 2)
        self.assertEqual(
            [result.name for result in page.results],
            ["document-2.txt", "document-3.txt"],
        )

    def test_content_page_deduplicates_chunks_and_reports_exact_total(self) -> None:
        long_document = self.documents / "long.txt"
        long_document.write_text(
            "algebra theorem\n" * 1_000, encoding="utf-8"
        )
        (self.documents / "short.txt").write_text("algebra", encoding="utf-8")
        self.service.catalog.scan_volume("test-volume")
        self.service.indexer.index_directory(self.documents)

        page = self.service.search_page(
            DocumentQuery(mode=SearchMode.CONTENT, text="algebra", limit=1)
        )

        self.assertEqual(page.total_matches, 2)
        self.assertTrue(page.total_is_exact)
        self.assertEqual(len(page.results), 1)

    def test_coverage_is_scoped_to_requested_allowed_volumes(self) -> None:
        external = self.root / "external"
        external.mkdir()
        (self.documents / "system.txt").write_text("system", encoding="utf-8")
        (external / "external.txt").write_text("external", encoding="utf-8")
        system_volume = DiscoveredVolume(
            "test-volume", "Documents", str(self.documents), "test", "testfs",
            True, False, False,
        )
        external_volume = DiscoveredVolume(
            "external-volume", "External", str(external), "external", "testfs",
            False, True, False,
        )
        self.service.volumes.discovery = MultiDiscovery([system_volume, external_volume])
        self.service.volumes.refresh()
        self.service.volumes.set_permission("external-volume", PermissionLevel.CONTENT)
        self.service.catalog.scan_volume("test-volume")
        self.service.catalog.scan_volume("external-volume")
        self.service.coverage_source = lambda: {
            "state": "updating",
            "coverage_complete": False,
            "covered_volume_ids": ("external-volume",),
            "scanning_volume_ids": ("test-volume",),
            "inaccessible": 0,
        }

        coverage = self.service.coverage(("external-volume",))

        self.assertTrue(coverage.complete)
        self.assertEqual(coverage.cataloged_items, 1)
        self.assertEqual(coverage.covered_volume_ids, ("external-volume",))
        self.assertEqual(coverage.scanning_volume_ids, ())
        self.assertIsNone(coverage.warning)

    def test_runtime_exposes_optional_system_monitor_snapshot(self) -> None:
        snapshot = {"schema_version": 1, "supported": True, "processes": []}
        application = QueryRuntimeApplication(
            self.service, system_monitor_status=lambda _payload: snapshot
        )

        self.assertEqual(application.system_status(), snapshot)
        self.assertEqual(
            application.system_status({"process_sort": "memory", "process_order": "asc"}),
            snapshot,
        )
        with self.assertRaises(ValueError):
            application.system_status({"untrusted": True})
        for invalid in (
            {"process_limit": 0},
            {"process_limit": True},
            {"process_sort": "disk"},
            {"process_order": "sideways"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    application.system_status(invalid)
        self.assertIn("system.monitor.snapshot", application.capabilities())
        with self.assertRaises(RuntimeError):
            QueryRuntimeApplication(self.service).system_status()

    def test_runtime_exposes_validated_system_update_check(self) -> None:
        result = {"schema_version": 1, "state": "up_to_date"}
        application = QueryRuntimeApplication(
            self.service, system_updates_check=lambda _payload: result
        )

        self.assertEqual(application.check_system_updates({}), result)
        self.assertIn("system.updates.check", application.capabilities())
        with self.assertRaises(ValueError):
            application.check_system_updates({"command": "install"})
        with self.assertRaises(RuntimeError):
            QueryRuntimeApplication(self.service).check_system_updates({})

    def test_runtime_exposes_validated_model_lifecycle_contract(self) -> None:
        catalog = {"schema_version": 1, "models": [{"model_id": "workspace.qwen"}]}
        decisions = []
        application = QueryRuntimeApplication(
            self.service,
            model_catalog=lambda: catalog,
            model_decision=lambda payload: decisions.append(payload) or {"state": "starting"},
        )

        self.assertEqual(application.model_catalog({}), catalog)
        self.assertEqual(
            application.respond_to_model(
                {"model_id": "assistant.llama", "decision": "download"}
            ),
            {"state": "starting"},
        )
        self.assertEqual(decisions, [
            {"model_id": "assistant.llama", "decision": "download"}
        ])
        self.assertIn("models.catalog.read", application.capabilities())
        self.assertIn("models.lifecycle.respond", application.capabilities())
        for invalid in (
            {},
            {"model_id": "assistant.llama", "decision": "maybe"},
            {"model_id": "unknown", "decision": "download"},
            {"model_id": "assistant.llama", "decision": "later", "extra": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                application.respond_to_model(invalid)

    def test_runtime_exposes_validated_ollama_provider_contract(self) -> None:
        provider = {"schema_version": 1, "provider_id": "ollama", "state": "consent_required"}
        decisions = []
        application = QueryRuntimeApplication(
            self.service,
            ollama_provider_status=lambda: provider,
            ollama_provider_decision=lambda payload: decisions.append(payload) or {
                **provider, "state": "downloading"
            },
        )

        self.assertEqual(application.ollama_provider_status({}), provider)
        self.assertEqual(
            application.respond_to_ollama_provider({"decision": "install"})["state"],
            "downloading",
        )
        self.assertEqual(decisions, [{"decision": "install"}])
        self.assertIn("providers.ollama.read", application.capabilities())
        self.assertIn("providers.ollama.respond", application.capabilities())
        for invalid in ({}, {"decision": "yes"}, {"decision": "later", "extra": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                application.respond_to_ollama_provider(invalid)

    def test_runtime_composes_provider_and_models_into_one_lifecycle(self) -> None:
        provider = {
            "provider_id": "ollama",
            "state": "consent_required",
            "installed": False,
            "prompt_required": True,
        }
        catalog = {
            "schema_version": 1,
            "models": [
                {
                    "model_id": "workspace.qwen",
                    "required": True,
                    "state": "consent_required",
                    "prompt_required": True,
                },
                {
                    "model_id": "assistant.llama",
                    "required": False,
                    "state": "consent_required",
                    "prompt_required": True,
                },
            ],
        }
        application = QueryRuntimeApplication(
            self.service,
            model_catalog=lambda: catalog,
            ollama_provider_status=lambda: provider,
        )

        snapshot = application.inference_lifecycle({})

        self.assertEqual(snapshot["state"], "action_required")
        self.assertEqual(snapshot["provider"], provider)
        self.assertEqual(
            [model["effective_state"] for model in snapshot["models"]],
            ["blocked", "blocked"],
        )
        self.assertTrue(all(
            model["blocked_by"] == "provider.ollama" for model in snapshot["models"]
        ))
        self.assertEqual(snapshot["errors"], [])
        self.assertIn("inference.lifecycle.read", application.capabilities())

    def test_runtime_lifecycle_always_returns_public_component_errors(self) -> None:
        def unavailable():
            raise RuntimeError("private detail")

        application = QueryRuntimeApplication(
            self.service,
            model_catalog=unavailable,
            ollama_provider_status=unavailable,
        )

        snapshot = application.inference_lifecycle({})

        self.assertEqual(snapshot["state"], "error")
        self.assertEqual(snapshot["provider"]["reason"], "provider_status_unavailable")
        self.assertEqual(
            [model["model_id"] for model in snapshot["models"]],
            ["workspace.qwen", "assistant.llama"],
        )
        self.assertTrue(all(
            model["reason"] == "model_catalog_unavailable"
            for model in snapshot["models"]
        ))
        self.assertNotIn("private detail", repr(snapshot))
        with self.assertRaises(ValueError):
            application.inference_lifecycle({"unexpected": True})

    def test_runtime_lifecycle_restores_models_missing_from_catalog(self) -> None:
        application = QueryRuntimeApplication(
            self.service,
            model_catalog=lambda: {"schema_version": 1, "models": []},
            ollama_provider_status=lambda: {
                "provider_id": "ollama",
                "state": "ready",
                "installed": True,
                "prompt_required": False,
            },
        )

        snapshot = application.inference_lifecycle({})

        self.assertEqual(snapshot["state"], "error")
        self.assertEqual(
            [model["model_id"] for model in snapshot["models"]],
            ["workspace.qwen", "assistant.llama"],
        )
        self.assertTrue(all(
            model["reason"] == "model_status_missing" for model in snapshot["models"]
        ))

    def test_installed_provider_without_api_keeps_models_blocked(self) -> None:
        application = QueryRuntimeApplication(
            self.service,
            model_catalog=lambda: {
                "schema_version": 1,
                "models": [{
                    "model_id": "workspace.qwen",
                    "required": True,
                    "state": "ready",
                    "prompt_required": False,
                }],
            },
            ollama_provider_status=lambda: {
                "provider_id": "ollama",
                "state": "error",
                "installed": True,
                "managed": False,
                "prompt_required": False,
                "reason": "external_server_unavailable",
            },
        )

        snapshot = application.inference_lifecycle({})

        self.assertEqual(snapshot["state"], "error")
        self.assertEqual(snapshot["models"][0]["effective_state"], "blocked")
        self.assertEqual(snapshot["models"][0]["blocked_by"], "provider.ollama")

    def test_runtime_lifecycle_orders_provider_then_models(self) -> None:
        provider = {
            "provider_id": "ollama", "state": "ready", "installed": True,
            "prompt_required": False,
        }

        def snapshot(qwen_state, llama_state, *, qwen_prompt=False, llama_prompt=False):
            catalog = {
                "schema_version": 1,
                "models": [
                    {
                        "model_id": "workspace.qwen", "required": True,
                        "state": qwen_state, "prompt_required": qwen_prompt,
                    },
                    {
                        "model_id": "assistant.llama", "required": False,
                        "state": llama_state, "prompt_required": llama_prompt,
                    },
                ],
            }
            return QueryRuntimeApplication(
                self.service,
                model_catalog=lambda: catalog,
                ollama_provider_status=lambda: provider,
            ).inference_lifecycle({})

        self.assertEqual(
            snapshot("consent_required", "consent_required", qwen_prompt=True, llama_prompt=True)["state"],
            "action_required",
        )
        self.assertEqual(snapshot("downloading", "consent_required")["state"], "models_preparing")
        ready = snapshot("ready", "declined")
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(
            [model["effective_state"] for model in ready["models"]],
            ["ready", "declined"],
        )

    def test_runtime_exposes_software_snapshot_only_when_configured(self) -> None:
        snapshot = {
            "schema_version": 1,
            "provider": "snap",
            "catalog": [{"application_id": "steam"}],
            "tasks": [],
            "backups": [],
        }
        application = QueryRuntimeApplication(
            self.service, software_snapshot=lambda: snapshot
        )

        self.assertEqual(application.software_snapshot({}), snapshot)
        self.assertIn("software.catalog.read", application.capabilities())
        self.assertIn("software.tasks.read", application.capabilities())
        self.assertIn("software.backups.read", application.capabilities())
        with self.assertRaises(ValueError):
            application.software_snapshot({"refresh": True})
        with self.assertRaises(RuntimeError):
            QueryRuntimeApplication(self.service).software_snapshot({})

    def test_runtime_exposes_validated_software_mutations(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def callback(name):
            def invoke(payload):
                calls.append((name, payload))
                return {"schema_version": 1, "task": payload}
            return invoke

        application = QueryRuntimeApplication(
            self.service,
            software_prepare=callback("prepare"),
            software_respond=callback("respond"),
            software_control=callback("control"),
        )
        transport = TransportContext.internal()
        prepared = application.software_prepare(
            {
                "action": "install",
                "application_id": "steam",
                "locale": "system",
                "install_location": "default",
                "selected_options": [],
            },
            transport_context=transport,
        )
        responded = application.software_respond(
            {
                "task_id": "task-1",
                "confirmed": True,
                "action": "install",
                "final_confirmation": False,
            },
            transport_context=transport,
        )
        controlled = application.software_control(
            {"task_id": "task-1", "action": "pause"},
            transport_context=transport,
        )

        self.assertEqual(prepared["task"]["application_id"], "steam")
        self.assertTrue(responded["task"]["confirmed"])
        self.assertEqual(controlled["task"]["action"], "pause")
        self.assertEqual([name for name, _payload in calls], ["prepare", "respond", "control"])
        self.assertIn("software.install.prepare", application.capabilities())
        self.assertIn("software.install.commit", application.capabilities())
        self.assertIn("software.tasks.control", application.capabilities())
        with self.assertRaises(ValueError):
            application.software_respond(
                {
                    "task_id": "task-1",
                    "confirmed": "yes",
                    "action": "install",
                    "final_confirmation": False,
                },
                transport_context=transport,
            )
        with self.assertRaises(ValueError):
            application.software_control(
                {"task_id": "task-1", "action": "delete"},
                transport_context=transport,
            )

    def test_runtime_exposes_validated_workspace_projection(self) -> None:
        workspace = FakeWorkspace()
        application = QueryRuntimeApplication(self.service, workspace=workspace)
        run_id = str(uuid4())

        self.assertEqual(application.workspace_messages({"limit": 25}), ("message",))
        self.assertEqual(workspace.message_limit, 25)
        self.assertEqual(
            application.workspace_runs({"limit": 5, "active_only": True}),
            ("run",),
        )
        self.assertEqual(workspace.run_options, (5, True))
        self.assertEqual(application.workspace_run({"run_id": run_id}), f"run:{run_id}")
        self.assertIn("workspace.messages.read", application.capabilities())
        self.assertIn("workspace.runs.read", application.capabilities())
        for invalid in (
            {"limit": True},
            {"limit": 501},
            {"unknown": 1},
        ):
            with self.subTest(messages=invalid):
                with self.assertRaises(ValueError):
                    application.workspace_messages(invalid)
        for invalid in (
            {"limit": 0},
            {"active_only": "yes"},
            {"unknown": 1},
        ):
            with self.subTest(runs=invalid):
                with self.assertRaises(ValueError):
                    application.workspace_runs(invalid)

    def test_runtime_exposes_workspace_controller_with_transport_context(self) -> None:
        controller = FakeWorkspaceController()
        application = QueryRuntimeApplication(
            self.service, workspace_controller=controller
        )
        transport = object()

        self.assertEqual(
            application.workspace_submit(
                {"text": "Привет"}, transport_context=transport
            ),
            "submitted:Привет",
        )
        self.assertIs(controller.transport, transport)
        self.assertEqual(
            application.workspace_approval(
                {"approval_request_id": "approval", "confirmed": True},
                transport_context=transport,
            ),
            ("approval", True),
        )
        self.assertIn("workspace.submit", application.capabilities())
        for invalid in ({}, {"text": ""}, {"text": "x", "extra": True}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    application.workspace_submit(invalid, transport_context=transport)

    def test_revoked_content_permission_hides_stale_index_snippets(self) -> None:
        self.make_pdf("Private mathematics theorem")
        self.service.catalog.scan_volume("test-volume")
        self.service.ingest_pdfs()
        self.service.volumes.set_permission("test-volume", PermissionLevel.METADATA)

        content = self.service.search(DocumentQuery(text="theorem"))
        metadata = self.service.search(DocumentQuery(name_contains=("study",)))

        self.assertEqual(content, [])
        self.assertEqual(len(metadata), 1)
        self.assertIsNone(metadata[0].snippet)

    def test_runtime_compiles_with_server_owned_task_context(self) -> None:
        pipeline = FakeIntentPipeline()
        application = QueryRuntimeApplication(
            self.service,
            intent_pipeline=pipeline,
            task_context=lambda: TaskContext("trusted-results", None, "ru"),
        )

        result = application.compile_intent({"text": "скопируй их"})

        self.assertIn("intent.compile", application.capabilities())
        self.assertEqual(result.clarification_question, "скопируй их")
        self.assertEqual(pipeline.context.active_collection_id, "trusted-results")
        with self.assertRaises(ValueError):
            application.compile_intent({"text": "поиск", "active_collection_id": "forged"})

    def test_runtime_execution_accepts_only_server_owned_plan_id(self) -> None:
        store = FakePlanStore()
        executor = FakeExecutor()
        store.plan = "trusted-plan-object"
        application = QueryRuntimeApplication(
            self.service, plan_store=store, plan_executor=executor
        )

        result = application.execute_plan({"plan_id": "plan-id"})

        self.assertEqual(result, {"executed": "trusted-plan-object"})
        self.assertIn("execution.plan.execute", application.capabilities())
        with self.assertRaises(ValueError):
            application.execute_plan({"plan_id": "plan-id", "steps": []})

        approval = application.respond_to_approval(
            {"approval_request_id": "approval-id", "confirmed": True}
        )
        self.assertEqual(
            approval, {"approval_request_id": "approval-id", "confirmed": True}
        )
        with self.assertRaises(ValueError):
            application.respond_to_approval(
                {"approval_request_id": "approval-id", "confirmed": "yes"}
            )


if __name__ == "__main__":
    unittest.main()
