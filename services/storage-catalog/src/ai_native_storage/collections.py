"""Persistent smart collections and immutable snapshots of file references."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .catalog import FileCatalog
from .contracts import (
    CatalogEntry,
    CollectionItem,
    CollectionKind,
    FileQuery,
    VirtualCollection,
)
from .database import StorageDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class VirtualCollectionStore:
    def __init__(self, database_path: Path) -> None:
        self.database = StorageDatabase(database_path)
        self.catalog = FileCatalog(database_path)

    def create_smart(self, title: str, query: FileQuery) -> VirtualCollection:
        title = self._validate_title(title)
        collection_id = str(uuid4())
        timestamp = _now()
        query_json = json.dumps(asdict(query), ensure_ascii=False, sort_keys=True)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO collections(collection_id, title, kind, query_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    collection_id,
                    title,
                    CollectionKind.SMART.value,
                    query_json,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_collection(collection_id)

    def create_snapshot(
        self,
        title: str,
        items: list[CatalogEntry] | list[CollectionItem],
    ) -> VirtualCollection:
        title = self._validate_title(title)
        collection_id = str(uuid4())
        timestamp = _now()
        normalized = [self._as_item(item) for item in items]
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO collections(collection_id, title, kind, query_json, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (
                    collection_id,
                    title,
                    CollectionKind.SNAPSHOT.value,
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """
                INSERT INTO collection_items(
                    collection_id, ordinal, volume_id, path, stable_key, name, score, snippet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        collection_id,
                        ordinal,
                        item.volume_id,
                        item.path,
                        item.stable_key,
                        item.name,
                        item.score,
                        item.snippet,
                    )
                    for ordinal, item in enumerate(normalized)
                ],
            )
        return self.get_collection(collection_id)

    def get_collection(self, collection_id: str) -> VirtualCollection:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown collection: {collection_id}")
        return self._collection_from_row(row)

    def list_collections(self) -> list[VirtualCollection]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM collections ORDER BY updated_at DESC, title"
            ).fetchall()
        return [self._collection_from_row(row) for row in rows]

    def resolve(self, collection_id: str) -> list[CollectionItem]:
        collection = self.get_collection(collection_id)
        if collection.kind is CollectionKind.SMART:
            assert collection.query is not None
            return [self._as_item(entry) for entry in self.catalog.search(collection.query)]

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM collection_items
                WHERE collection_id = ?
                ORDER BY ordinal
                """,
                (collection_id,),
            ).fetchall()

        resolved: list[CollectionItem] = []
        for row in rows:
            current = self.catalog.resolve_reference(
                volume_id=str(row["volume_id"]),
                stable_key=str(row["stable_key"]),
                fallback_path=str(row["path"]),
            )
            if current is None:
                resolved.append(
                    CollectionItem(
                        volume_id=str(row["volume_id"]),
                        path=str(row["path"]),
                        stable_key=str(row["stable_key"]),
                        name=str(row["name"]),
                        score=None if row["score"] is None else float(row["score"]),
                        snippet=None if row["snippet"] is None else str(row["snippet"]),
                        available=False,
                    )
                )
            else:
                resolved.append(
                    CollectionItem(
                        volume_id=current.volume_id,
                        path=current.path,
                        stable_key=current.stable_key,
                        name=current.name,
                        score=None if row["score"] is None else float(row["score"]),
                        snippet=None if row["snippet"] is None else str(row["snippet"]),
                        available=True,
                    )
                )
        return resolved

    def delete(self, collection_id: str) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM collections WHERE collection_id = ?", (collection_id,)
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown collection: {collection_id}")

    @staticmethod
    def _validate_title(title: str) -> str:
        title = title.strip()
        if not title or len(title) > 200:
            raise ValueError("collection title must contain from 1 to 200 characters")
        return title

    @staticmethod
    def _as_item(value: CatalogEntry | CollectionItem) -> CollectionItem:
        if isinstance(value, CollectionItem):
            return value
        return CollectionItem(
            volume_id=value.volume_id,
            path=value.path,
            stable_key=value.stable_key,
            name=value.name,
        )

    @staticmethod
    def _collection_from_row(row: sqlite3.Row) -> VirtualCollection:
        query = None
        if row["query_json"] is not None:
            payload = json.loads(str(row["query_json"]))
            query = FileQuery(
                name_contains=tuple(payload.get("name_contains", ())),
                extensions=tuple(payload.get("extensions", ())),
                roles=tuple(payload.get("roles", ())),
                volume_ids=tuple(payload.get("volume_ids", ())),
                limit=int(payload.get("limit", 100)),
            )
        return VirtualCollection(
            collection_id=str(row["collection_id"]),
            title=str(row["title"]),
            kind=CollectionKind(str(row["kind"])),
            query=query,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
