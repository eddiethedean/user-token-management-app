from ipaddress import ip_address

from fastapi import Request

from app.config import Settings


def client_ip(request: Request | None, settings: Settings) -> str:
    """Return a normalized source address, trusting forwarding data only from configured proxies."""
    if request is None or request.client is None:
        return ""
    direct = request.client.host.strip()
    try:
        normalized_direct = str(ip_address(direct))
    except ValueError:
        normalized_direct = direct[:64]

    if normalized_direct not in settings.trusted_proxy_ip_set:
        return normalized_direct

    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",", 1)[0].strip()
    if not candidate:
        return normalized_direct
    try:
        return str(ip_address(candidate))
    except ValueError:
        return normalized_direct
