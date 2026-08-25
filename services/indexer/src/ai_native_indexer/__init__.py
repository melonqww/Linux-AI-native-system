"""Local opt-in retrieval service for AI-native Linux."""

from .contracts import ContentIndexState, ContentIndexStatus, IndexReport, SearchHit
from .service import IndexerService

__all__ = [
    "ContentIndexState",
    "ContentIndexStatus",
    "IndexReport",
    "IndexerService",
    "SearchHit",
]
