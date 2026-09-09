#!/usr/bin/env python3
"""Probe the same authenticated Unix IPC contract used by the GNOME panel."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from uuid import uuid4


IPC_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024


class ProbeError(RuntimeError):
    pass


def request(
    socket_path: Path,
    method: str,
    path: str,
    *,
    timeout: float,
) -> dict[str, object]:
    request_id = str(uuid4())
    frame = json.dumps(
        {
            "version": IPC_VERSION,
            "request_id": request_id,
            "method": method,
            "path": path,
            "body": {},
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(socket_path))
        connection.sendall(frame)
        response = _read_line(connection)
    except (OSError, TimeoutError) as error:
        raise ProbeError(f"transport_unavailable:{type(error).__name__}") from error
    finally:
        connection.close()
    return decode_response(response, request_id)


def _read_line(connection: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) <= MAX_MESSAGE_BYTES:
        block = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - len(chunks)))
        if not block:
            break
        chunks.extend(block)
        if b"\n" in block:
            break
    if not chunks or len(chunks) > MAX_MESSAGE_BYTES or not chunks.endswith(b"\n"):
        raise ProbeError("response_frame_invalid")
    return bytes(chunks[:-1])


def decode_response(raw: bytes, request_id: str) -> dict[str, object]:
    try:
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProbeError("response_json_invalid") from error
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"version", "request_id", "status", "body"}
        or envelope.get("version") != IPC_VERSION
        or envelope.get("request_id") != request_id
        or not isinstance(envelope.get("status"), int)
        or not isinstance(envelope.get("body"), dict)
    ):
        raise ProbeError("response_contract_invalid")
    body = envelope["body"]
    status = envelope["status"]
    if not 200 <= status < 300:
        error = body.get("error")
        code = (
            error.get("code", "request_failed")
            if isinstance(error, dict)
            else "request_failed"
        )
        raise ProbeError(f"runtime_status_{status}:{code}")
    return body


def probe(socket_path: Path) -> dict[str, object]:
    health = request(socket_path, "GET", "/v1/health", timeout=2)
    if health != {"status": "ok"}:
        raise ProbeError("health_contract_invalid")
    capabilities = request(socket_path, "GET", "/v1/capabilities", timeout=2)
    inference = request(socket_path, "POST", "/v1/inference/status", timeout=10)
    models = inference.get("models")
    if not isinstance(models, list):
        raise ProbeError("inference_models_invalid")
    provider = inference.get("provider")
    if not isinstance(provider, dict):
        raise ProbeError("inference_provider_invalid")
    advertised = capabilities.get("capabilities")
    if not isinstance(advertised, list) or "inference.lifecycle.read" not in advertised:
        raise ProbeError("inference_lifecycle_capability_missing")
    errors = inference.get("errors")
    if not isinstance(errors, list):
        raise ProbeError("inference_errors_invalid")
    if errors:
        codes = sorted(
            f"{error.get('component', 'unknown')}:{error.get('code', 'unknown')}"
            for error in errors
            if isinstance(error, dict)
        )
        raise ProbeError(f"inference_components_unavailable:{','.join(codes)}")
    model_ids = {
        model.get("model_id") for model in models if isinstance(model, dict)
    }
    if not {"workspace.qwen", "assistant.llama", "semantic.selector"}.issubset(
        model_ids
    ):
        raise ProbeError("inference_models_missing")
    return {
        "health": "ok",
        "lifecycle_capability": True,
        "inference_state": inference.get("state", "unknown"),
        "provider_state": provider.get("state", "unknown"),
        "models": {
            model.get("model_id", "unknown"): model.get(
                "effective_state", model.get("state", "unknown")
            )
            for model in models
            if isinstance(model, dict)
        },
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.socket)
    except ProbeError as error:
        print(f"FAIL: runtime IPC probe: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
