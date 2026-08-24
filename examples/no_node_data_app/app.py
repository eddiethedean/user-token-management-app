"""Tiny server-rendered data-app example: FastAPI + Hedron + HTMX, no Node.js."""

from __future__ import annotations

from fastapi import Form, Request
from hedron import (
    Alert,
    Button,
    FormField,
    Heading,
    Hedron,
    SecurityPolicy,
    Stack,
    csrf_token_for_request,
    html,
)
from hedron.responses import render_component_response
from hedron_core import NodeLike, SafeUrl
from hedron_core.security.urls import UrlPurpose

app = Hedron(
    title="ADE no-Node data app",
    explorer="off",
    session_secret="workshop-only-change-me-before-deployment",
)


def preview(source: str, destination: str, limit: int) -> NodeLike:
    if not source.strip() or not destination.strip():
        return html.div(
            Alert(
                "Enter both a source and destination before previewing.",
                title="Validation needed",
                tone="warning",
            ),
            id="preview",
        )
    return html.div(
        Alert(
            f"Preview ready: up to {limit:,} rows from {source.strip()} to {destination.strip()}.",
            title="Route preview",
            tone="success",
        ),
        id="preview",
    )


@app.page("/")
def home(request: Request):
    return render_component_response(
        Stack(
            Heading("No-Node data app", level=1),
            html.p("A complete FastAPI + Hedron + HTMX workflow rendered by Python."),
            html.form(
                FormField(
                    name="source",
                    label="Source",
                    control=html.input(name="source", value="orders"),
                ),
                FormField(
                    name="destination",
                    label="Destination",
                    control=html.input(name="destination", value="warehouse"),
                ),
                FormField(
                    name="limit",
                    label="Row limit",
                    control=html.input(name="limit", type="number", value="1000", min="1"),
                ),
                html.input(
                    type="hidden",
                    name="csrf_token",
                    value=csrf_token_for_request(request, SecurityPolicy()),
                ),
                Button("Preview route", type="submit"),
                action=SafeUrl.parse("/preview", purpose=UrlPurpose.FORM_ACTION),
                method="post",
                **{
                    "hx-post": SafeUrl.parse("/preview", purpose=UrlPurpose.NAVIGATION),
                    "hx-target": "#preview",
                    "hx-swap": "outerHTML",
                },
            ),
            preview("orders", "warehouse", 1000),
            gap="lg",
        ),
        request=request,
    )


@app.action("/preview")
def preview_action(
    request: Request,
    source: str = Form(""),
    destination: str = Form(""),
    limit: int = Form(1000),
):
    return render_component_response(
        preview(source, destination, max(1, min(limit, 100_000))),
        request=request,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8770)
