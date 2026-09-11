"""Semblance Foundry simulator for the frozen MSS/MCS-COP HTTP contract.

JSON list responses are schema-driven via Semblance. Download and file-upload
are octet-stream, so those two routes are FastAPI overlays on the same app.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from urllib.parse import unquote
from uuid import uuid4

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
        "/api/v2/datasets/{dataset_rid}/files",
        input=FileListQuery,
        output=FileListResponse,
        summary="List dataset files",
        tags=["foundry"],
    )
    def list_files() -> None:
        return None

    @api.get(
        "/api/v1/datasets/{dataset_rid}/files",
        input=FileListQuery,
        output=FileListResponse,
        summary="List dataset files (legacy)",
        tags=["foundry"],
    )
    def list_files_v1() -> None:
        return None

    return api


class FoundrySimulator:
    """Loopback Foundry host used by connector tests."""

    def __init__(self, *, token: str = FOUNDRY_TOKEN) -> None:
        self.token = token
        self.files: dict[str, bytes] = {"notes.csv": DEFAULT_CSV}
        self.created_datasets: list[dict[str, str]] = []
        self.legacy_create_only = False
        self.legacy_upload_only = False
        self.fail_upload = False
        self.legacy_preview_only = False
        self.last_download_branch = ""
        self.last_upload_branch = ""
        self.last_upload_publication = ""
        self.api = build_foundry_api(token)
        self.app = self._with_binary_routes(self.api.as_fastapi())
        self.base_url = ""

    def _with_binary_routes(self, app: FastAPI) -> FastAPI:
        @app.post("/api/v2/datasets", status_code=201)
        async def create_dataset(request: Request):
            if self.legacy_create_only:
                return JSONResponse({"error": "v2 route unavailable"}, status_code=404)
            return await create_dataset_payload(request)

        @app.post("/api/v1/datasets", status_code=201)
        async def create_dataset_v1(request: Request):
            return await create_dataset_payload(request)

        async def create_dataset_payload(request: Request):
            payload = await request.json()
            created = {
                "rid": f"ri.foundry.main.dataset.{uuid4()}",
                "name": str(payload.get("name") or ""),
                "parentFolderRid": str(payload.get("parentFolderRid") or ""),
            }
            self.created_datasets.append(created)
            return created

        @app.get("/api/v2/datasets/{dataset_rid}/files/{file_path}/content")
        async def download_content(dataset_rid: str, file_path: str, branchName: str = "master"):
            del dataset_rid
            self.last_download_branch = branchName
            body = self.files.get(unquote(file_path), DEFAULT_CSV)
            return Response(content=body, media_type="application/octet-stream")

        @app.get("/api/v1/datasets/{dataset_rid}/files/{file_path}/content")
        async def download_content_v1(dataset_rid: str, file_path: str, branchName: str = "master"):
            return await download_content(dataset_rid, file_path, branchName)

        @app.post("/api/v2/datasets/{dataset_rid}/files/{file_name}/upload")
        async def upload_file(
            request: Request,
            dataset_rid: str,
            file_name: str,
            preview: str | None = None,
            branchName: str = "master",
            transactionType: str | None = None,
        ):
            del dataset_rid
            self.last_upload_branch = branchName
            if self.legacy_upload_only:
                return JSONResponse({"error": "v2 route unavailable"}, status_code=404)
            if self.legacy_preview_only and preview != "true":
                return JSONResponse({"error": "preview required"}, status_code=400)
            if self.fail_upload:
                return JSONResponse({"error": "unavailable"}, status_code=503)
            self.last_upload_publication = (
                "legacy_preview_upload" if preview == "true" else "committed_upload"
            )
            body = await request.body()
            payload = dict(load_fixture("foundry_upload_success.json")["body"])
            path = unquote(file_name)
            payload["path"] = path
            payload["sizeBytes"] = len(body)
            if transactionType:
                payload["transactionType"] = transactionType
            self.files[path] = body
            return JSONResponse(payload)

        @app.post("/api/v1/datasets/{dataset_rid}/files:upload")
        async def upload_file_v1(
            request: Request,
            dataset_rid: str,
            filePath: str,
            branchId: str = "master",
            transactionType: str | None = None,
        ):
            del dataset_rid
            self.last_upload_branch = branchId
            if self.fail_upload:
                return JSONResponse({"error": "unavailable"}, status_code=503)
            self.last_upload_publication = "committed_upload"
            body = await request.body()
            payload = dict(load_fixture("foundry_upload_success.json")["body"])
            payload["path"] = filePath
            payload["sizeBytes"] = len(body)
            if transactionType:
                payload["transactionType"] = transactionType
            self.files[filePath] = body
            return JSONResponse(payload)

        return app

    def test_client(self):
        return test_client(self.app)

    @contextmanager
    def serve(self) -> Iterator[str]:
        with serve_asgi(self.app) as url:
            self.base_url = url
            yield url
