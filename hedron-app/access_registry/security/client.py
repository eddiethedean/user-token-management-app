from ipaddress import ip_address

from fastapi import Request

from access_registry.config import Settings


def direct_client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return ""
    direct = request.client.host.strip()
    try:
        return str(ip_address(direct))
    except ValueError:
        return direct[:64]


def is_trusted_direct_proxy(request: Request | None, settings: Settings) -> bool:
    return direct_client_ip(request) in settings.trusted_proxy_ip_set


def client_ip(request: Request | None, settings: Settings) -> str:
    """Return a normalized source address, trusting forwarding data only from configured proxies."""
    normalized_direct = direct_client_ip(request)
    if not normalized_direct:
        return ""

    if not is_trusted_direct_proxy(request, settings):
        return normalized_direct

    forwarded = request.headers.get("x-forwarded-for", "")
    candidate = forwarded.split(",", 1)[0].strip()
    if not candidate:
        return normalized_direct
    try:
        return str(ip_address(candidate))
    except ValueError:
        return normalized_direct
