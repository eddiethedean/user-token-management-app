from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

from app.config import Settings


class EmailPolicyError(ValueError):
    pass


def normalize_email(raw_email: str, settings: Settings) -> tuple[str, str]:
    try:
        result = validate_email(raw_email.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise EmailPolicyError("Enter a valid government email address.") from exc
    original = result.normalized
    canonical = original.casefold()
    domain = canonical.rpartition("@")[2]
    allowlist = settings.email_domain_allowlist
    if allowlist and domain not in allowlist:
        raise EmailPolicyError("That email domain is not approved for this application.")
    return canonical, original
