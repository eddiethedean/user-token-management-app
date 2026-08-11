"""Minimal FastAPI application for testing Posit Workbench and Posit Connect."""

from __future__ import annotations

import html
import json
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="Posit FastAPI Demo",
    description="A small deployment-confidence check for Workbench and Connect.",
    version="1.0.0",
)


def _safe_base_path(value: str) -> str:
    """Return a safe path prefix from an ASGI root path or Connect base URL."""
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlsplit(candidate)
    path = parsed.path if parsed.scheme and parsed.netloc else candidate
    path = path.rstrip("/")
    if (
        not path
        or path == "/"
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "?" in path
        or "#" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        return ""
    return path


def app_base_path(request: Request) -> str:
    """Find the externally visible path prefix used by Connect or Workbench."""
    connect_base = _safe_base_path(request.headers.get("rstudio-connect-app-base-url", ""))
    asgi_root = _safe_base_path(str(request.scope.get("root_path", "")))
    return connect_base or asgi_root


def app_url(request: Request, path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{app_base_path(request)}{normalized}"


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title)}</title>
    <style>
      :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
      body {{ max-width: 46rem; margin: 4rem auto; padding: 0 1.25rem; line-height: 1.55; }}
      main {{ border: 1px solid #8a8a8a66; border-radius: 0.8rem; padding: 1.5rem; }}
      .success {{ color: #16833b; font-weight: 700; }}
      code, pre {{ background: #8882; border-radius: 0.3rem; }}
      code {{ padding: 0.1rem 0.3rem; }}
      pre {{ padding: 1rem; overflow-x: auto; min-height: 1.5rem; }}
      button {{ font: inherit; padding: 0.5rem 0.8rem; cursor: pointer; }}
      nav {{ display: flex; gap: 1rem; margin-top: 1.25rem; }}
    </style>
  </head>
  <body><main>{body}</main></body>
</html>"""
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    base_path = app_base_path(request)
    hello_url = app_url(request, "/api/hello")
    about_url = app_url(request, "/about")
    health_url = app_url(request, "/health")
    displayed_base = base_path or "(none — app is mounted at the site root)"
    body = f"""
      <p class="success">The FastAPI demo is running.</p>
      <h1>Posit deployment check</h1>
      <p>
        If this page loaded, Python, FastAPI, and the ASGI server started correctly.
        Use the button to verify that requests also work through the platform's URL prefix.
      </p>
      <p>Detected application base path: <code>{html.escape(displayed_base)}</code></p>
      <button id="hello" type="button">Call the JSON endpoint</button>
      <pre id="result" aria-live="polite">No request made yet.</pre>
      <nav>
        <a href="{html.escape(about_url, quote=True)}">About this check</a>
        <a href="{html.escape(health_url, quote=True)}">Health endpoint</a>
        <a href="docs">FastAPI docs</a>
      </nav>
      <script>
        const endpoint = {json.dumps(hello_url)};
        document.querySelector("#hello").addEventListener("click", async () => {{
          const output = document.querySelector("#result");
          output.textContent = "Loading…";
          try {{
            const response = await fetch(endpoint, {{headers: {{"Accept": "application/json"}}}});
            const payload = await response.json();
            output.textContent = JSON.stringify(payload, null, 2);
          }} catch (error) {{
            output.textContent = `Request failed: ${{error}}`;
          }}
        }});
      </script>
    """
    return page("Posit deployment check", body)


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    home_url = app_url(request, "/")
    body = f"""
      <h1>About this check</h1>
      <p>
        This deliberately small app has no database, authentication, email, Hedron, or HTMX.
        It isolates the platform basics before those pieces are introduced.
      </p>
      <p><a href="{html.escape(home_url, quote=True)}">Return to the demo</a></p>
    """
    return page("About the Posit deployment check", body)


@app.get("/api/hello")
async def hello(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "message": "The browser reached the JSON endpoint through the application prefix.",
        "root_path": str(request.scope.get("root_path", "")),
        "detected_base_path": app_base_path(request),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
