"""TLS bootstrap for outbound connector traffic.

Tests mock this module. Production prefers a configured CA bundle. The optional
internal ``socom_ca_fix`` package is invoked during app startup when enabled.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path

from app.connectors.errors import ConnectorError, TransferErrorCode


class TlsBootstrapError(ConnectorError):
    def __init__(self, summary: str) -> None:
        super().__init__(code=TransferErrorCode.TLS_FAILED, summary=summary, retryable=False)


@lru_cache(maxsize=8)
def ssl_context_for_bundle(ca_bundle: str) -> ssl.SSLContext:
    if not ca_bundle:
        return ssl.create_default_context()
    path = Path(ca_bundle)
    if not path.is_file():
        raise TlsBootstrapError("The configured pipeline CA bundle is not a readable file.")
    try:
        return ssl.create_default_context(cafile=str(path))
    except ssl.SSLError as exc:
        raise TlsBootstrapError("The configured pipeline CA bundle could not be loaded.") from exc


def apply_internal_ca_fix() -> bool:
    """Call the deployment CA helper once if it is installed. Returns whether it ran."""
    try:
        import socom_ca_fix  # type: ignore[import-not-found]
    except ImportError:
        return False
    adder = getattr(socom_ca_fix, "add_nipr_ca", None)
    if adder is None:
        return False
    adder()
    return True


def verify_hostname_policy(*, https_only: bool, hostname: str) -> None:
    if https_only and not hostname:
        raise TlsBootstrapError("Foundry endpoints must include a hostname.")
