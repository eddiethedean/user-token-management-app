from __future__ import annotations

from app.config import Settings
from app.services.links import public_url


def test_public_url_preserves_deployment_mount_and_encodes_query() -> None:
    settings = Settings(public_base_url="https://workbench.example.mil/s/session-token/p/8765")

    assert public_url(
        settings,
        "/invitations/accept",
        query={"token": "token/with spaces+symbols"},
    ) == (
        "https://workbench.example.mil/s/session-token/p/8765/invitations/accept"
        "?token=token%2Fwith+spaces%2Bsymbols"
    )


def test_public_url_uses_local_base_without_mount() -> None:
    settings = Settings(public_base_url="http://127.0.0.1:8000")

    assert public_url(settings, "/login", query={"next": "/pipeline"}) == (
        "http://127.0.0.1:8000/login?next=%2Fpipeline"
    )
