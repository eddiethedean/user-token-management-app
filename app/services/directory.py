from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import TypeAlias, TypedDict, cast
from urllib.parse import urlencode

import httpx2
from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User, UserStatus
from app.routing import app_path
from app.security.email import normalize_email

log = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

ADMIN_PAGE_SIZE = 50


class DirectoryEligibilityError(ValueError):
    pass


class DirectoryUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectoryRecord:
    email: str
    display_name: str = ""


class UserListingValues(TypedDict):
    users: list[User]
    total_users: int
    current_page: int
    page_count: int
    user_query: str
    status_filter: str
    user_success: str


def _first_string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _parse_record(data: JsonValue, query_email: str, settings: Settings) -> DirectoryRecord:
    if not isinstance(data, dict):
        raise DirectoryUnavailableError("Directory returned an unexpected response.")
    attributes_raw = data.get("attributes")
    attributes: dict[str, JsonValue] = attributes_raw if isinstance(attributes_raw, dict) else {}
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
    transport: httpx2.AsyncBaseTransport | None = None,
) -> DirectoryRecord | None:
    """Validate enrollment eligibility; this does not authenticate or identity-proof the user."""
    canonical_email, _ = normalize_email(email, settings)
    if not settings.directory_lookup_url:
        return None
    headers = {"Accept": "application/json"}
    if settings.directory_lookup_bearer_token:
        headers["Authorization"] = f"Bearer {settings.directory_lookup_bearer_token}"
    verify: bool | ssl.SSLContext = settings.directory_lookup_verify_tls
    if settings.directory_lookup_ca_bundle and verify:
        verify = ssl.create_default_context(cafile=settings.directory_lookup_ca_bundle)
    try:
        async with httpx2.AsyncClient(
            verify=verify,
            timeout=settings.directory_lookup_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.get(
                settings.directory_lookup_url,
                params={"query": canonical_email},
                headers=headers,
            )
    except httpx2.HTTPError as exc:
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
        return _parse_record(cast(JsonValue, response.json()), canonical_email, settings)
    except DirectoryEligibilityError:
        raise
    except (ValueError, TypeError, DirectoryUnavailableError) as exc:
        if settings.directory_lookup_required:
            raise DirectoryUnavailableError(
                "The government directory response was invalid."
            ) from exc
        log.warning("Invalid directory response; allowing enrollment because fail-closed is off")
        return None


def list_users_page(
    db: Session, *, query: str = "", status_filter: str = "", page: int = 1
) -> tuple[list[User], int, int]:
    """Return a page of directory users matching optional search/status filters."""
    page = max(1, page)
    statement = select(User)
    count_statement = select(func.count()).select_from(User)
    conditions = []
    cleaned_query = query.strip()[:160]
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        conditions.append(
            or_(
                User.email.ilike(pattern),
                User.email_original.ilike(pattern),
                User.full_name.ilike(pattern),
                User.organization.ilike(pattern),
            )
        )
    if status_filter in {item.value for item in UserStatus}:
        conditions.append(User.status == status_filter)
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = int(db.scalar(count_statement) or 0)
    page_count = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(page, page_count)
    users = db.scalars(
        statement.order_by(User.created_at.desc())
        .offset((page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
    ).all()
    return list(users), total, page


def user_listing_values(
    db: Session,
    *,
    query: str = "",
    status_filter: str = "",
    page: int = 1,
    user_success: str = "",
) -> UserListingValues:
    cleaned_query = query.strip()[:160]
    cleaned_status = status_filter if status_filter in {item.value for item in UserStatus} else ""
    users, total_users, current_page = list_users_page(
        db, query=cleaned_query, status_filter=cleaned_status, page=page
    )
    return {
        "users": users,
        "total_users": total_users,
        "current_page": current_page,
        "page_count": max(1, (total_users + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE),
        "user_query": cleaned_query,
        "status_filter": cleaned_status,
        "user_success": user_success,
    }


def user_listing_path(
    request: Request, *, query: str = "", status_filter: str = "", page: int = 1, notice: str = ""
) -> str:
    parameters = {"q": query, "status": status_filter, "page": max(1, page)}
    if notice:
        parameters["notice"] = notice
    return app_path(request, f"/admin/users?{urlencode(parameters)}")
