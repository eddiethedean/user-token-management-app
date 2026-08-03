import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx


def normalize_prefix(value: str) -> str:
    prefix = value.strip()
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802
        self.proxy()

    def do_POST(self) -> None:  # noqa: N802
        self.proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self.proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self.proxy()

    def proxy(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        prefix = config["prefix"]
        parsed = urlsplit(self.path)
        if parsed.path != prefix and not parsed.path.startswith(f"{prefix}/"):
            self.send_error(404)
            return

        upstream_path = parsed.path
        if config["mode"] == "strip":
            upstream_path = upstream_path[len(prefix) :] or "/"
        if parsed.query:
            upstream_path = f"{upstream_path}?{parsed.query}"

        content_length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(content_length) if content_length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.casefold() not in {"host", "content-length", "accept-encoding", "connection"}
        }
        headers["Accept-Encoding"] = "identity"
        headers["X-Forwarded-For"] = self.client_address[0]
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Forwarded-Host"] = config["external_host"]
        if config["mode"] == "strip":
            headers["RStudio-Connect-App-Base-URL"] = config["external_base"]

        with httpx.Client(follow_redirects=False, timeout=30) as client:
            response = client.request(
                self.command,
                f"{config['upstream']}{upstream_path}",
                headers=headers,
                content=body,
            )

        self.send_response(response.status_code)
        excluded = {
            "connection",
            "content-encoding",
            "content-length",
            "keep-alive",
            "transfer-encoding",
        }
        for key, value in response.headers.multi_items():
            if key.casefold() not in excluded:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        self.wfile.write(response.content)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--prefix", default="/content/access-registry")
    parser.add_argument("--mode", choices=["preserve", "strip"], required=True)
    args = parser.parse_args()

    prefix = normalize_prefix(args.prefix)
    host = f"127.0.0.1:{args.listen_port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.listen_port), ProxyHandler)
    server.config = {  # type: ignore[attr-defined]
        "upstream": args.upstream.rstrip("/"),
        "prefix": prefix,
        "mode": args.mode,
        "external_host": host,
        "external_base": f"http://{host}{prefix}",
    }
    server.serve_forever()


if __name__ == "__main__":
    main()
