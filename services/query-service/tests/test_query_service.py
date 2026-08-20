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
from ai_native_query import DocumentQuery, QueryRuntimeApplication, QueryService
from ai_native_storage import PermissionLevel, VolumeRegistry
from ai_native_storage.contracts import DiscoveredVolume


class Discovery:
    def __init__(self, volume: DiscoveredVolume) -> None:
        self.volume = volume

    def discover(self):
        return [self.volume]


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
        with self.assertRaises(ValueError):
            application.search({"text": [], "limit": 20})

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
