from __future__ import annotations

from ..snapd import SnapdClient


class SnapdProvider(SnapdClient):
    """Snap-specific policy kept behind the generic software-manager contract."""

    provider_id = "snap"
    retryable_error_codes = frozenset({
        "snapd_unavailable",
        "network-timeout",
        "dns-failure",
        "store-unreachable",
        "temporarily-unavailable",
        "connection-refused",
        "connection-reset",
    })

    def is_retryable_error(self, error_code: str | None) -> bool:
        return error_code in self.retryable_error_codes
