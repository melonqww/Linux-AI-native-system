"""Validated adapter between the loopback panel bridge and QueryService."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Callable

from .contracts import DocumentQuery, QueryResult
from .service import QueryService


class QueryRuntimeApplication:
    def __init__(
        self,
        query_service: QueryService,
        *,
        scheduler_status: Callable[[], object] | None = None,
    ) -> None:
        self.query_service = query_service
        self.scheduler_status = scheduler_status

    def capabilities(self) -> list[str]:
        return [
            "documents.query.search",
            "documents.pdf.extract",
            "storage.collection.snapshot",
        ]

    def search(self, payload: dict[str, object]) -> list[QueryResult]:
        text = self._string(payload, "text")
        name_contains = self._strings(payload, "name_contains")
        extensions = self._strings(payload, "extensions")
        volume_ids = self._strings(payload, "volume_ids")
        limit = payload.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        return self.query_service.search(
            DocumentQuery(
                text=text,
                name_contains=name_contains,
                extensions=extensions,
                volume_ids=volume_ids,
                limit=limit,
            )
        )

    def index_status(self) -> dict[str, object]:
        status: dict[str, object] = {
            "catalog": self.query_service.catalog.status(),
            "content_index": self.query_service.indexer.get_index_status(),
        }
        if self.scheduler_status is not None:
            scheduler = self.scheduler_status()
            status["scheduler"] = asdict(scheduler) if is_dataclass(scheduler) else scheduler
        return status

    @staticmethod
    def _string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if len(value) > 2_000:
            raise ValueError(f"{key} is too long")
        return value

    @staticmethod
    def _strings(payload: dict[str, object], key: str) -> tuple[str, ...]:
        value = payload.get(key, ())
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a list of strings")
        if len(value) > 100:
            raise ValueError(f"{key} has too many values")
        return tuple(value)
