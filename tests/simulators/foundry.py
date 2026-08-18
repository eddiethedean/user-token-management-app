"""Semblance Foundry simulator for the frozen MSS/MCS-COP HTTP contract.

JSON list responses are schema-driven via Semblance. Download and preview-upload
are octet-stream, so those two routes are FastAPI overlays on the same app.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from semblance import SemblanceAPI, test_client

from tests.simulators.auth import BearerTokenMiddleware
from tests.simulators.http import serve_asgi
from tests.simulators.links import FromFoundryFileList, load_fixture

FOUNDRY_TOKEN = "test-foundry-token-value"
FOUNDRY_DATASET = "ri.foundry.main.dataset.example"
DEFAULT_CSV = b"event_id,unit_name\n1,Alpha\n"


class FileListQuery(BaseModel):
    dataset_rid: str = ""
    branchName: str = "master"
    pageToken: str | None = None


class FileListResponse(BaseModel):
    data: Annotated[list[dict[str, Any]], FromFoundryFileList("data")]
    nextPageToken: Annotated[str, FromFoundryFileList("nextPageToken")] = ""


def build_foundry_api(token: str = FOUNDRY_TOKEN) -> SemblanceAPI:
    api = SemblanceAPI(seed=42, validate_responses=True)
    api.add_middleware(
        BearerTokenMiddleware,
        token=token,
        unauthorized=load_fixture("foundry_error_unauthorized.json"),
    )

    @api.get(
        "/api/v1/datasets/{dataset_rid}/files",
        input=FileListQuery,
        output=FileListResponse,
        summary="List dataset files",
        tags=["foundry"],
    )
    def list_files() -> None:
        return None

    return api


class FoundrySimulator:
    """Loopback Foundry host used by connector tests."""

    def __init__(self, *, token: str = FOUNDRY_TOKEN) -> None:
        self.token = token
        self.files: dict[str, bytes] = {"notes.csv": DEFAULT_CSV}
        self.fail_upload = False
        self.api = build_foundry_api(token)
        self.app = self._with_binary_routes(self.api.as_fastapi())
        self.base_url = ""

    def _with_binary_routes(self, app: FastAPI) -> FastAPI:
        @app.get("/api/v1/datasets/{dataset_rid}/files/{file_path}/content")
        async def download_content(dataset_rid: str, file_path: str, branchName: str = "master"):
            del dataset_rid, branchName
            body = self.files.get(unquote(file_path), DEFAULT_CSV)
            return Response(content=body, media_type="application/octet-stream")

        @app.post("/api/v2/datasets/{dataset_rid}/files/{file_name}/upload")
        async def upload_file(
            request: Request, dataset_rid: str, file_name: str, preview: str = "false"
        ):
            del dataset_rid
            if preview != "true":
                return JSONResponse({"error": "preview required"}, status_code=400)
            if self.fail_upload:
                return JSONResponse({"error": "unavailable"}, status_code=503)
            body = await request.body()
            payload = dict(load_fixture("foundry_upload_success.json")["body"])
            path = unquote(file_name)
            payload["filePath"] = path
            payload["sizeBytes"] = len(body)
            self.files[path] = body
            return JSONResponse(payload)

        return app

    def test_client(self):
        return test_client(self.app)

    @contextmanager
    def serve(self) -> Iterator[str]:
        with serve_asgi(self.app) as url:
            self.base_url = url
            yield url
