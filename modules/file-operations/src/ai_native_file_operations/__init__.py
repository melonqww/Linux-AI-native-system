"""Bounded implementation and executor adapter for File Operations R1."""

from .service import FileOperationError, FileOperationsService, OperationKind, PlanState
from .integration import CAPABILITY_IDS, FileOperationsCapabilityHandler, capability_handlers

MODULE_ID = "files.operations"

__all__ = [
    "MODULE_ID",
    "CAPABILITY_IDS",
    "FileOperationError",
    "FileOperationsService",
    "FileOperationsCapabilityHandler",
    "OperationKind",
    "PlanState",
    "capability_handlers",
]
