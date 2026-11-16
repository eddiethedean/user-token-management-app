"""Typed control-flow errors for checks that must stop or skip safely."""

from app.connectors.errors import ConnectorError, TransferErrorCode


class DiagnosticAdministratorUnavailable(ConnectorError):
    def __init__(self) -> None:
        super().__init__(
            TransferErrorCode.PERMISSION_DENIED,
            "Administrator access is no longer available.",
            retryable=False,
        )


class DiagnosticCredentialsStale(ConnectorError):
    def __init__(self) -> None:
        super().__init__(
            TransferErrorCode.CREDENTIALS_STALE,
            "The saved connection changed during this diagnostic check.",
            retryable=False,
        )
