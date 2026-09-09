"""Bounded loopback Ollama adapter for optional semantic embeddings."""

from __future__ import annotations

import json
import math
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


_MODEL = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}:[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_BATCH = 64
_MAX_TEXT = 4_000
_MAX_DIMENSIONS = 4_096


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class EmbeddingUnavailableError(RuntimeError):
    """The optional local semantic service cannot answer safely right now."""


class OllamaEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str = "qwen3-embedding:0.6b",
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 4.0,
        keep_alive: str = "5m",
        open_fn: Callable[..., object] | None = None,
    ) -> None:
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise ValueError("embedding model name is invalid")
        if not isinstance(timeout_seconds, (int, float)) or not 0.1 <= timeout_seconds <= 30:
            raise ValueError("embedding timeout must be from 0.1 to 30 seconds")
        if not isinstance(keep_alive, str) or not keep_alive or len(keep_alive) > 32:
            raise ValueError("embedding keep_alive is invalid")
        self.model = model
        self.base_url = self._base_url(base_url)
        self.timeout_seconds = float(timeout_seconds)
        self.keep_alive = keep_alive
        self._open_fn = open_fn or build_opener(ProxyHandler({})).open

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        values = tuple(texts)
        if not 1 <= len(values) <= _MAX_BATCH:
            raise ValueError("embedding batch must contain from 1 to 64 texts")
        if any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > _MAX_TEXT
            or any(ord(character) < 32 and character not in "\n\t" for character in item)
            for item in values
        ):
            raise ValueError("embedding text is invalid")
        payload = json.dumps(
            {
                "model": self.model,
                "input": list(values),
                "truncate": True,
                "keep_alive": self.keep_alive,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._open_fn(request, timeout=self.timeout_seconds) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise EmbeddingUnavailableError(
                f"local embedding request failed: {type(error).__name__}"
            ) from error
        if len(body) > _MAX_RESPONSE_BYTES:
            raise EmbeddingUnavailableError("local embedding response is too large")
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EmbeddingUnavailableError("local embedding response is invalid") from error
        if not isinstance(envelope, Mapping) or set(envelope) - {
            "model",
            "embeddings",
            "total_duration",
            "load_duration",
            "prompt_eval_count",
        }:
            raise EmbeddingUnavailableError("local embedding envelope is invalid")
        raw_vectors = envelope.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(values):
            raise EmbeddingUnavailableError("local embedding batch shape is invalid")
        vectors = tuple(self._vector(item) for item in raw_vectors)
        dimensions = {len(item) for item in vectors}
        if len(dimensions) != 1:
            raise EmbeddingUnavailableError("local embedding dimensions are inconsistent")
        return vectors

    @staticmethod
    def _vector(value: object) -> tuple[float, ...]:
        if not isinstance(value, list) or not 1 <= len(value) <= _MAX_DIMENSIONS:
            raise EmbeddingUnavailableError("local embedding vector is invalid")
        result: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise EmbeddingUnavailableError("local embedding value is invalid")
            number = float(item)
            if not math.isfinite(number):
                raise EmbeddingUnavailableError("local embedding value is not finite")
            result.append(number)
        if not any(result):
            raise EmbeddingUnavailableError("local embedding vector is empty")
        return tuple(result)

    @staticmethod
    def _base_url(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("embedding base_url must be a string")
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK_HOSTS
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("embedding base_url must be a credential-free loopback origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("embedding base_url has an invalid port") from error
        if port is None:
            raise ValueError("embedding base_url must include a port")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return f"http://{host}:{port}"
