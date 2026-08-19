"""Model-neutral interface: local and cloud adapters implement the same contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from .contracts import ModelRequest


class IntentProviderError(RuntimeError):
    pass


class IntentProviderUnavailableError(IntentProviderError):
    pass


class IntentProviderResponseError(IntentProviderError):
    pass


class IntentModelProvider(Protocol):
    def compile(self, request: ModelRequest) -> Mapping[str, object]: ...


class CallableIntentProvider:
    """Adapter useful for local model runtimes, SDK clients and deterministic tests."""

    def __init__(self, function: Callable[[ModelRequest], Mapping[str, object]]) -> None:
        self.function = function

    def compile(self, request: ModelRequest) -> Mapping[str, object]:
        result = self.function(request)
        if not isinstance(result, Mapping):
            raise IntentProviderResponseError("intent provider must return an object")
        return result
