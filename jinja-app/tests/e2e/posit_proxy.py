import argparse
import json
import ssl
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx2

HOP_BY_HOP_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

PLATFORM_REQUEST_HEADERS = {
    "forwarded",
    "rstudio-connect-app-base-url",
    "rstudio-connect-credentials",
    "x-correlation-id",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-prefix",
    "x-forwarded-proto",
    "x-rsc-request",
    "x-rstudio-root-path",
}


def normalize_prefix(value: str) -> str:
    prefix = value.strip()
    if not prefix.startswith("/") or prefix.startswith("//"):
        raise ValueError("the simulated platform prefix must be an absolute path")
    return prefix.rstrip("/")


def normalize_public_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("the public origin must contain only an HTTP(S) scheme and authority")
    return f"{parsed.scheme}://{parsed.netloc}"


def sanitize_request_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        key: value
        for key, value in items
        if key.casefold()
        not in HOP_BY_HOP_HEADERS | PLATFORM_REQUEST_HEADERS | {"host", "accept-encoding"}
    }


def platform_request_headers(
    config: Mapping[str, object], *, client_ip: str, path: str, query: str = ""
) -> dict[str, str]:
    prefix = str(config["prefix"])
    public_origin = str(config["public_origin"])
    public = urlsplit(public_origin)
    public_port = public.port or (443 if public.scheme == "https" else 80)
    original_url = f"{public_origin}{path}"
    if query:
        original_url = f"{original_url}?{query}"

    headers = {
        "Accept-Encoding": "identity",
        "Forwarded": f'for={client_ip};proto={public.scheme};host="{public.netloc}"',
        "Host": public.netloc,
        "X-Forwarded-For": client_ip,
        "X-Forwarded-Host": public.netloc,
        "X-Forwarded-Port": str(public_port),
        "X-Forwarded-Prefix": prefix,
        "X-Forwarded-Proto": public.scheme,
    }

    if config["platform"] == "connect":
        base_url = prefix
        if config["connect_base"] == "absolute":
            base_url = f"{public_origin}{prefix}"
        headers.update(
            {
                "RStudio-Connect-App-Base-URL": base_url,
                "RStudio-Connect-Credentials": json.dumps(
                    {"user": "browser.viewer", "groups": ["all", "data-science"]},
                    separators=(",", ":"),
                ),
                "X-Correlation-ID": "posit-connect-simulation",
                "X-RSC-Request": original_url,
            }
        )
    else:
        headers["X-RStudio-Root-Path"] = prefix
    return headers


class PositProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_DELETE(self) -> None:  # noqa: N802
        self.proxy()

    def do_GET(self) -> None:  # noqa: N802
        self.proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self.proxy()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self.proxy()

    def do_POST(self) -> None:  # noqa: N802
        self.proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self.proxy()

    def proxy(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        prefix = config["prefix"]
        parsed_request = urlsplit(self.path)
        if parsed_request.path != prefix and not parsed_request.path.startswith(f"{prefix}/"):
            self.send_error(404)
            return

        upstream_path = parsed_request.path
        if config["upstream_path"] == "strip":
            upstream_path = upstream_path[len(prefix) :] or "/"
        if parsed_request.query:
            upstream_path = f"{upstream_path}?{parsed_request.query}"

        content_length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(content_length) if content_length else None
        headers = sanitize_request_headers(self.headers.items())
        headers.update(
            platform_request_headers(
                config,
                client_ip=self.client_address[0],
                path=parsed_request.path,
                query=parsed_request.query,
            )
        )

        with httpx2.Client(follow_redirects=False, timeout=30) as client:
            response = client.request(
                self.command,
                f"{config['upstream']}{upstream_path}",
                headers=headers,
                content=body,
            )

        self.send_response(response.status_code)
        for key, value in response.headers.multi_items():
            if key.casefold() not in HOP_BY_HOP_HEADERS | {"date", "server"}:
                self.send_header(key, value)
        if config["platform"] == "connect" and config["worker_cookie"]:
            self.send_header(
                "Set-Cookie",
                f"connect.workerid=worker-1; Path={prefix}; Secure; HttpOnly; SameSite=Lax",
            )
        response_length = response.headers.get("content-length", "0")
        if self.command != "HEAD":
            response_length = str(len(response.content))
        self.send_header("Content-Length", response_length)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.content)
        self.close_connection = True

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--platform", choices=["connect", "workbench"], required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--upstream-path", choices=["preserve", "strip"], default="strip")
    parser.add_argument("--connect-base", choices=["absolute", "path"], default="path")
    parser.add_argument("--worker-cookie", action="store_true")
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()

    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be provided together")

    prefix = normalize_prefix(args.prefix)
    public_origin = normalize_public_origin(args.public_origin)
    server = ThreadingHTTPServer(("127.0.0.1", args.listen_port), PositProxyHandler)
    server.config = {  # type: ignore[attr-defined]
        "connect_base": args.connect_base,
        "platform": args.platform,
        "prefix": prefix,
        "public_origin": public_origin,
        "upstream": args.upstream.rstrip("/"),
        "upstream_path": args.upstream_path,
        "worker_cookie": args.worker_cookie,
    }
    if args.tls_cert and args.tls_key:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
