"""Local opt-in retrieval service for AI-native Linux."""

from .contracts import IndexReport, SearchHit
from .service import IndexerService

__all__ = ["IndexReport", "IndexerService", "SearchHit"]
