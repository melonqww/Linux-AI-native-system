"""Production pipeline assembly over lab-owned storage and capability adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from time import perf_counter

from ai_native_capabilities import CapabilityRegistry
from ai_native_intents import (
    IntentCompiler,
    OllamaModelProvider,
    TaskContextStore,
    build_operation_definitions,
)
from ai_native_ledger import TaskLedger
from ai_native_orchestrator import DestinationResolver, ExecutionOrchestrator
from ai_native_permissions import TransportContext
from ai_native_query import QueryService
from ai_native_scheduler import IndexScheduler
from ai_native_storage import (
    ApprovalAuthority,
    MaterializeService,
    PermissionLevel,
    VolumeRegistry,
)
from ai_native_storage.contracts import DiscoveredVolume
from ai_native_turns import (
    CapabilityCandidateRouter,
    CapabilityDescriptor,
    HybridCapabilityRouter,
    OllamaEmbeddingProvider,
    SemanticCapabilitySelector,
    TurnRouter,
)
from ai_native_workspace import WorkspaceRuntime, WorkspaceStore

from .virtual_pc import VirtualComputer
from .contracts import FaultSpec
from .faults import FaultController


class _Discovery:
    def __init__(self, volumes: tuple[DiscoveredVolume, ...]) -> None:
        self.volumes = volumes

    def discover(self) -> list[DiscoveredVolume]:
        return list(self.volumes)


class MeasuredOllamaModelProvider(OllamaModelProvider):
    """Captures Ollama timing/token counters without changing provider semantics."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._usage: list[dict[str, object]] = []

    def _json_request(self, method, path, payload=None, *, timeout=None):
        started = perf_counter()
        result = super()._json_request(method, path, payload, timeout=timeout)
        if method == "POST":
            self._usage.append(
                {
                    "path": path,
                    "wall_ms": round((perf_counter() - started) * 1_000, 3),
                    "prompt_tokens": _counter(result, "prompt_eval_count"),
                    "output_tokens": _counter(result, "eval_count"),
                    "ollama_total_ns": _counter(result, "total_duration"),
                }
            )
        return result

    def drain_usage(self) -> tuple[dict[str, object], ...]:
        result = tuple(self._usage)
        self._usage.clear()
        return result


class TracingModel:
    """Records model boundaries while preserving the exact production provider."""

    def __init__(self, provider: object, faults: FaultController) -> None:
        self.provider = provider
        self.faults = faults
        self.events: list[dict[str, object]] = []

    def health(self):
        return self.provider.health()

    def route(self, request):
        return self._call("route", request, lambda: self.provider.route(request))

    def respond_chat(self, request):
        return self._call(
            "respond_chat", request, lambda: self.provider.respond_chat(request)
        )

    def summarize_result(self, facts, *, locale: str):
        return self._call(
            "summarize_result",
            {"facts": dict(facts), "locale": locale},
            lambda: self.provider.summarize_result(facts, locale=locale),
        )

    def compose_conversation(self, request, *, system_result: str):
        return self._call(
            "compose_conversation",
            {"request": request, "system_result": system_result},
            lambda: self.provider.compose_conversation(
                request, system_result=system_result
            ),
        )

    def classify_turn(self, request):
        return self._call(
            "classify_turn", request, lambda: self.provider.classify_turn(request)
        )

    def annotate_compilation(self, result: object) -> None:
        """Attach safe compiler metadata to the producing route trace.

        Compiler exception text and payload values are deliberately excluded.
        The trace contains only stable codes already intended for developers.
        """
        state = getattr(getattr(result, "state", None), "value", None)
        raw_diagnostics = getattr(result, "diagnostics", ())
        diagnostics = [
            item
            for item in raw_diagnostics
            if isinstance(item, str)
            and item
            and len(item) <= 64
            and item.isascii()
            and all(
                character.islower() or character.isdigit() or character == "_"
                for character in item
            )
        ]
        for event in reversed(self.events):
            if event.get("kind") == "route" and "compilation" not in event:
                event["compilation"] = {
                    "state": state if isinstance(state, str) else "unknown",
                    "diagnostics": diagnostics,
                }
                return

    def _call(self, kind: str, request: object, callback: Callable[[], object]):
        started = perf_counter()
        event: dict[str, object] = {
            "kind": kind,
            "request": _trace_request(request),
        }
        drain = getattr(self.provider, "drain_usage", None)
        if callable(drain):
            drain()
        try:
            response = self.faults.call(f"model.{kind}", callback)
            event["response"] = _json_value(response)
            event["status"] = "ok"
            return response
        except Exception as error:
            event["status"] = "error"
            event["error_type"] = type(error).__name__
            event["error"] = str(error)[:1_000]
            raise
        finally:
            event["duration_ms"] = round((perf_counter() - started) * 1_000, 3)
            if callable(drain):
                event["ollama_usage"] = list(drain())
            self.events.append(event)


class RecordingExecutor:
    def __init__(
        self, executor: ExecutionOrchestrator, faults: FaultController
    ) -> None:
        self.executor = executor
        self.faults = faults
        self.records: list[dict[str, object]] = []

    def available_capabilities(self) -> tuple[str, ...]:
        return self.executor.available_capabilities()

    def execute(self, plan, *, transport_context=None):
        started = perf_counter()
        record = {"event": "execute", "plan": _json_value(plan)}
        try:
            result = self.faults.call(
                "executor.execute",
                lambda: self.executor.execute(
                    plan, transport_context=transport_context
                ),
            )
            record["result"] = _json_value(result)
            return result
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error)[:1_000])
            raise
        finally:
            record["duration_ms"] = round((perf_counter() - started) * 1_000, 3)
            self.records.append(record)

    def respond_to_approval(
        self, approval_request_id: str, *, confirmed: bool, transport_context=None
    ):
        started = perf_counter()
        record = {
            "event": "approval_response",
            "approval_request_id": approval_request_id,
            "confirmed": confirmed,
        }
        try:
            result = self.faults.call(
                "executor.approval_response",
                lambda: self.executor.respond_to_approval(
                    approval_request_id,
                    confirmed=confirmed,
                    transport_context=transport_context,
                ),
            )
            record["result"] = _json_value(result)
            return result
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error)[:1_000])
            raise
        finally:
            record["duration_ms"] = round((perf_counter() - started) * 1_000, 3)
            self.records.append(record)


class TracingIntentCompiler:
    """Lab-only observer around the production compiler contract."""

    def __init__(self, compiler: IntentCompiler, model: TracingModel) -> None:
        self.compiler = compiler
        self.model = model

    def model_request(self, *args, **kwargs):
        return self.compiler.model_request(*args, **kwargs)

    def compile_and_plan(self, *args, **kwargs):
        result = self.compiler.compile_and_plan(*args, **kwargs)
        self.model.annotate_compilation(result)
        return result

    def compile_payload(self, *args, **kwargs):
        result = self.compiler.compile_payload(*args, **kwargs)
        self.model.annotate_compilation(result)
        return result

class LabEnvironment:
    """One scenario gets one model conversation, database set, and virtual PC."""

    def __init__(
        self,
        *,
        project_root: Path,
        lab_root: Path,
        run_root: Path,
        fixture_path: Path,
        locale: str,
        provider: object | None = None,
        model: str = "qwen3.5:2b",
        base_url: str = "http://127.0.0.1:11434",
        faults: tuple[FaultSpec, ...] = (),
    ) -> None:
        self.project_root = project_root.resolve()
        self.lab_root = lab_root.resolve()
        self.run_root = run_root.resolve()
        if not self.run_root.is_relative_to(self.lab_root / ".runtime"):
            raise ValueError("scenario run root must stay inside lab .runtime")
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.pc = VirtualComputer(self.run_root / "virtual-pc", lab_root=self.lab_root)
        self.pc.provision(fixture_path)
        self.faults = FaultController(faults)
        self.audit_events: list[dict[str, object]] = []
        self.capability_registry = CapabilityRegistry(
            self.run_root / "capability-registry.sqlite3"
        )
        registry_report = self.capability_registry.sync(
            [self.project_root / "services", self.project_root / "modules"]
        )
        if registry_report.issues:
            details = "; ".join(
                f"{issue.path}: {issue.error}" for issue in registry_report.issues
            )
            raise RuntimeError(f"production capability registry sync failed: {details}")
        self._prepare_storage()
        self.query = QueryService(
            storage_database=self.storage_db,
            index_database=self.index_db,
            coverage_source=lambda: asdict(self.scheduler.status()),
            semantic_provider=(
                OllamaEmbeddingProvider(
                    model="qwen3-embedding:0.6b",
                    base_url=base_url,
                )
                if provider is None
                else None
            ),
        )
        self.context = TaskContextStore(
            locale=locale, database=self.run_root / "task-context.sqlite3"
        )
        self.ledger = TaskLedger(self.run_root / "task-ledger.sqlite3")
        materialize = MaterializeService(
            self.storage_db, ApprovalAuthority(), plan_ttl_seconds=600
        )
        resolver = DestinationResolver(
            home=self.pc.home,
            roles={
                "desktop": self.pc.home / "Desktop",
                "documents": self.pc.home / "Documents",
                "downloads": self.pc.home / "Downloads",
            },
        )
        capabilities = (
            "documents.query.search",
            "storage.materialize.plan-copy",
        )
        missing_capabilities = set(capabilities).difference(
            self.capability_registry.available_capabilities()
        )
        if missing_capabilities:
            missing = ", ".join(sorted(missing_capabilities))
            raise RuntimeError(
                f"lab executor capabilities are unavailable in production registry: {missing}"
            )
        orchestrator = ExecutionOrchestrator(
            self.query,
            self.context,
            audit_sink=self.audit_events.append,
            materialize_service=materialize,
            destination_resolver=resolver,
            capability_source=lambda: capabilities,
            task_ledger=self.ledger,
        )
        self.executor = RecordingExecutor(orchestrator, self.faults)
        raw_provider = provider or MeasuredOllamaModelProvider(
            model=model,
            base_url=base_url,
            timeout_seconds=90,
            context_tokens=8_192,
            max_output_tokens=768,
            keep_alive="10m",
        )
        self.model = TracingModel(raw_provider, self.faults)
        self.compiler = TracingIntentCompiler(
            IntentCompiler(
                self.model,
                operation_source=lambda: _operation_definitions(
                    self.capability_registry,
                    self.executor.available_capabilities(),
                    orchestrator.permission_gateway.policy,
                ),
            ),
            self.model,
        )
        self.store = WorkspaceStore(self.run_root / "workspace.sqlite3")
        descriptors = _registry_descriptors(
            self.capability_registry,
            self.executor.available_capabilities(),
            orchestrator.permission_gateway.policy,
        )
        lexical_router = CapabilityCandidateRouter(descriptors)
        capability_router = lexical_router
        if provider is None:
            capability_router = HybridCapabilityRouter(
                lexical_router,
                SemanticCapabilitySelector(
                    descriptors,
                    OllamaEmbeddingProvider(
                        model="qwen3-embedding:0.6b",
                        base_url=base_url,
                    ),
                ),
            )
        self.runtime = WorkspaceRuntime(
            self.store,
            self.model,
            self.compiler,
            self.executor,
            self.context.snapshot,
            turn_router=TurnRouter(self.model),
            capability_router=capability_router,
            workers=1,
            max_pending=4,
        )

    @property
    def transport(self) -> TransportContext:
        return TransportContext.internal()

    def close(self) -> None:
        self.runtime.close()
        self.pc.close()

    def _prepare_storage(self) -> None:
        self.storage_db = self.run_root / "storage.sqlite3"
        self.index_db = self.run_root / "index.sqlite3"
        discovered = tuple(
            DiscoveredVolume(
                item.volume_id,
                item.name,
                str(item.physical_root),
                f"virtual:{item.volume_id}",
                "ai-labfs",
                item.is_system,
                not item.is_system,
                False,
                16 * 1024**3,
                8 * 1024**3,
            )
            for item in self.pc.volumes
        )
        registry = VolumeRegistry(self.storage_db, discovery=_Discovery(discovered))
        registry.refresh()
        for item in self.pc.volumes:
            registry.set_permission(item.volume_id, PermissionLevel(item.permission))
        self.scheduler = IndexScheduler(
            storage_database=self.storage_db,
            index_database=self.index_db,
        )
        for item in self.pc.volumes:
            if item.permission != "none":
                self.scheduler.request_rescan(item.volume_id)
        for cycle in range(5_000):
            status = self.scheduler.process_once(now=float(cycle))
            if status.coverage_complete:
                break
        else:
            raise RuntimeError("virtual computer index did not finish")


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _registry_descriptors(
    registry: CapabilityRegistry,
    available: tuple[str, ...],
    policy_source,
) -> tuple[CapabilityDescriptor, ...]:
    """Adapt enabled Registry routes to the production turn-router contract."""
    result = tuple(
        CapabilityDescriptor(
            definition.capability_id,
            definition.operation,
            definition.description,
            definition.examples,
        )
        for definition in _operation_definitions(registry, available, policy_source)
    )
    if not result:
        raise RuntimeError("no enabled production intent routes were loaded")
    return result


def _operation_definitions(registry, available, policy_source):
    """Build the same trusted operation catalog used by the production runtime."""
    return build_operation_definitions(
        registry.capability_contracts(),
        available_capabilities=available,
        policy_source=policy_source,
    )


def _counter(payload: Mapping[str, object], name: str) -> int | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _trace_request(value: object) -> object:
    """Keep reports useful without repeating schemas and full system prompts."""
    if hasattr(value, "user_text") and hasattr(value, "locale"):
        history = getattr(value, "history", ())
        return {
            "user_text": getattr(value, "user_text"),
            "locale": getattr(value, "locale"),
            "context": _json_value(getattr(value, "context", {})),
            "history": _json_value(history),
            "allowed_operations": _json_value(
                getattr(value, "allowed_operations", None)
            ),
        }
    if isinstance(value, dict) and "request" in value:
        return {
            **{
                key: _json_value(item)
                for key, item in value.items()
                if key != "request"
            },
            "request": _trace_request(value["request"]),
        }
    return _json_value(value)
