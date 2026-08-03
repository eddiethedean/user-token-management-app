from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.security.email import normalize_email

log = logging.getLogger(__name__)


class DirectoryEligibilityError(ValueError):
    pass


class DirectoryUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectoryRecord:
    email: str
    display_name: str = ""


def _first_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _parse_record(data: Any, query_email: str, settings: Settings) -> DirectoryRecord:
    if not isinstance(data, dict):
        raise DirectoryUnavailableError("Directory returned an unexpected response.")
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    returned_email = (
        _first_string(data.get("email"))
        or _first_string(data.get("mail"))
        or _first_string(attributes.get("mail"))
        or _first_string(attributes.get("userPrincipalName"))
    )
    if not returned_email:
        raise DirectoryUnavailableError("Directory response did not contain an email address.")
    canonical_query, _ = normalize_email(query_email, settings)
    canonical_returned, _ = normalize_email(returned_email, settings)
    if canonical_returned != canonical_query:
        raise DirectoryEligibilityError("The government directory did not confirm that address.")
    display_name = _first_string(data.get("display_name")) or _first_string(
        attributes.get("displayName")
    )
    return DirectoryRecord(email=canonical_returned, display_name=display_name.strip()[:160])


async def validate_directory_email(
    email: str,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DirectoryRecord | None:
    """Validate enrollment eligibility; this does not authenticate or identity-proof the user."""
    if not settings.directory_lookup_url:
        return None
    headers = {"Accept": "application/json"}
    if settings.directory_lookup_bearer_token:
        headers["Authorization"] = f"Bearer {settings.directory_lookup_bearer_token}"
    verify: bool | ssl.SSLContext = settings.directory_lookup_verify_tls
    if settings.directory_lookup_ca_bundle and verify:
        verify = ssl.create_default_context(cafile=settings.directory_lookup_ca_bundle)
    try:
        async with httpx.AsyncClient(
            verify=verify,
            timeout=settings.directory_lookup_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.get(
                settings.directory_lookup_url,
                params={"query": email},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        if settings.directory_lookup_required:
            raise DirectoryUnavailableError("The government directory is unavailable.") from exc
        log.warning("Directory lookup unavailable; allowing enrollment because fail-closed is off")
        return None

    if response.status_code == 404:
        raise DirectoryEligibilityError("That address was not found in the government directory.")
    if not 200 <= response.status_code < 300:
        if settings.directory_lookup_required:
            raise DirectoryUnavailableError("The government directory is unavailable.")
        log.warning(
            "Directory lookup returned status %s; allowing enrollment because fail-closed is off",
            response.status_code,
        )
        return None
    try:
        return _parse_record(response.json(), email, settings)
    except DirectoryEligibilityError:
        raise
    except (ValueError, TypeError, DirectoryUnavailableError) as exc:
        if settings.directory_lookup_required:
            raise DirectoryUnavailableError(
                "The government directory response was invalid."
            ) from exc
        log.warning("Invalid directory response; allowing enrollment because fail-closed is off")
        return None
