"""Shared Foundry HTTP client for MSS and MCS-COP."""

from __future__ import annotations

import json
import re
import shutil
import ssl
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx2
import polars as pl

from app.config import Settings, get_settings
from app.connectors.base import (
    BatchWriteResult,
    CatalogPage,
    ConnectionHealth,
    DestinationManifest,
    LoadSession,
    ObjectSchema,
    RemoteNamespace,
    RemoteObject,
    TransferBatch,
    map_http_status,
)
from app.connectors.errors import ConnectorError, TransferErrorCode
from app.connectors.locators import (
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    Locator,
    WritePolicy,
)
from app.connectors.redaction import redact_text
from app.connectors.tls import ssl_context_for_bundle

SUPPORTED_SUFFIXES = (".csv", ".parquet")
DEFAULT_BRANCHES = ("master", "main")


def normalize_foundry_base(endpoint: str) -> str:
    raw = endpoint.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() in {"http", "https"}:
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Foundry endpoint contains an invalid port.") from exc
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in raw)
            or "\\" in raw
        ):
            raise ValueError("Foundry endpoint must be a clean HTTP(S) URL.")
        scheme = parsed.scheme.casefold()
        host = parsed.hostname.casefold()
        if scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
            scheme = "https"
        path = parsed.path.rstrip("/")
        return f"{scheme}://{parsed.netloc}{path}"
    if "://" in raw:
        raise ValueError("Foundry endpoint must use HTTP or HTTPS.")
    raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE).strip("/")
    return f"https://{raw}"


def host_from_endpoint(endpoint: str) -> str:
    parsed = urlsplit(normalize_foundry_base(endpoint))
    return (parsed.hostname or "").casefold().rstrip(".")


def assert_host_allowed(hostname: str, settings: Settings) -> None:
    if settings.is_demo_mode:
        return
    allowed = settings.allowed_https_hosts
    if hostname not in allowed and not any(hostname.endswith(f".{item}") for item in allowed):
        raise ConnectorError(
            TransferErrorCode.ENDPOINT_BLOCKED,
            "That Foundry host is not on the operator allowlist.",
            retryable=False,
        )


class FoundryClient:
    def __init__(self, credentials: Mapping[str, str], settings: Settings | None = None):
        self.settings = settings or get_settings()
        endpoint = credentials.get("endpoint") or credentials.get("url") or ""
        if not endpoint:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING, "Foundry endpoint is required."
            )
        try:
            self.base_url = normalize_foundry_base(endpoint)
        except ValueError as exc:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING, "The Foundry endpoint is invalid."
            ) from exc
        assert_host_allowed(host_from_endpoint(endpoint), self.settings)
        token = credentials.get("token", "")
        if not token:
            raise ConnectorError(
                TransferErrorCode.CREDENTIALS_MISSING, "Foundry API token is required."
            )
        self.headers = {"authorization": f"Bearer {token}"}
        self.default_rid = credentials.get("dataset_rid", "")
        self.default_branch = credentials.get("branch", "") or "master"
        ca_profile = (credentials.get("ca_profile") or "system").casefold()
        ca_bundle = self.settings.pipeline_ca_bundle
        if ca_profile not in {"system", "default", "nipr"} and Path(ca_profile).is_file():
            ca_bundle = ca_profile
        verify: ssl.SSLContext | bool = (
            ssl_context_for_bundle(ca_bundle)
            if ca_bundle and ca_profile not in {"system", "default"}
            else True
        )
        timeout = httpx2.Timeout(
            connect=self.settings.pipeline_http_connect_seconds,
            read=self.settings.pipeline_http_read_seconds,
            write=self.settings.pipeline_http_write_seconds,
            pool=self.settings.pipeline_http_connect_seconds,
        )
        self._client = httpx2.Client(
            headers=self.headers,
            timeout=timeout,
            verify=verify,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, url: str, **kwargs):
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx2.TimeoutException as exc:
            raise ConnectorError(
                TransferErrorCode.CONNECTION_TIMEOUT, "The Foundry request timed out."
            ) from exc
        except httpx2.TransportError as exc:
            summary = redact_text(str(exc))
            code = (
                TransferErrorCode.TLS_FAILED
                if "ssl" in summary.casefold() or "certificate" in summary.casefold()
                else TransferErrorCode.PROVIDER_UNAVAILABLE
            )
            raise ConnectorError(code, "The Foundry endpoint could not be reached.") from exc
        if not 200 <= response.status_code < 300:
            code = map_http_status(response.status_code, for_destination="/upload" in url)
            raise ConnectorError(code, f"Foundry returned HTTP {response.status_code}.")
        return response

    def list_files(self, dataset_rid: str, branch: str, cursor: str | None = None) -> dict:
        params = {"branchName": branch}
        if cursor:
            params["pageToken"] = cursor
        url = f"{self.base_url}/api/v1/datasets/{dataset_rid}/files"
        return self.request("GET", url, params=params).json()

    def resolve_branch(self, dataset_rid: str) -> tuple[str, list[dict]]:
        branches = [
            self.default_branch,
            *[item for item in DEFAULT_BRANCHES if item != self.default_branch],
        ]
        last_error: ConnectorError | None = None
        for branch in branches:
            try:
                payload = self.list_files(dataset_rid, branch)
                files = list(payload.get("data") or [])
                return branch, files
            except ConnectorError as exc:
                last_error = exc
                continue
        raise last_error or ConnectorError(
            TransferErrorCode.SOURCE_NOT_FOUND, "Could not list dataset files."
        )

    def download_file(self, dataset_rid: str, branch: str, path: str, dest: Path) -> int:
        encoded = quote(path, safe="")
        url = f"{self.base_url}/api/v1/datasets/{dataset_rid}/files/{encoded}/content"
        try:
            with self._client.stream(
                "GET", url, params={"branchName": branch}, headers=self.headers
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ConnectorError(
                        map_http_status(response.status_code),
                        f"Foundry returned HTTP {response.status_code}.",
                    )
                written = 0
                max_bytes = self.settings.pipeline_max_source_bytes
                with dest.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise ConnectorError(
                                TransferErrorCode.SOURCE_LIMIT_EXCEEDED,
                                "The dataset file exceeds the configured source size limit.",
                            )
                        handle.write(chunk)
                return written
        except httpx2.TimeoutException as exc:
            raise ConnectorError(
                TransferErrorCode.CONNECTION_TIMEOUT, "The Foundry request timed out."
            ) from exc
        except httpx2.TransportError as exc:
            summary = redact_text(str(exc))
            code = (
                TransferErrorCode.TLS_FAILED
                if "ssl" in summary.casefold() or "certificate" in summary.casefold()
                else TransferErrorCode.PROVIDER_UNAVAILABLE
            )
            raise ConnectorError(code, "The Foundry endpoint could not be reached.") from exc

    def upload_file(self, dataset_rid: str, file_name: str, path: Path) -> dict:
        url = (
            f"{self.base_url}/api/v2/datasets/{dataset_rid}/files/"
            f"{quote(file_name, safe='')}/upload"
        )
        with path.open("rb") as handle:
            response = self.request(
                "POST",
                url,
                params={"preview": "true"},
                headers={**self.headers, "content-type": "application/octet-stream"},
                content=handle,
            )
        if response.content:
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"filePath": file_name, "sizeBytes": path.stat().st_size}
        return {"filePath": file_name, "sizeBytes": path.stat().st_size}


def supported_files(entries: list[dict]) -> list[dict]:
    selected = []
    for entry in entries:
        path = str(entry.get("path") or "")
        if path.lower().endswith(SUPPORTED_SUFFIXES):
            selected.append(entry)
    return selected


class FoundryConnector:
    capabilities = None  # set by subclass

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._load_credentials: dict[str, str] = {}

    def _client(self, credentials) -> FoundryClient:
        return FoundryClient(credentials, self.settings)

    def test_connection(self, credentials) -> ConnectionHealth:
        client = self._client(credentials)
        try:
            rid = client.default_rid
            if not rid:
                return ConnectionHealth(
                    status="untested",
                    message="Provide a default dataset RID to verify Foundry access.",
                    latency_ms=0,
                )
            branch, files = client.resolve_branch(rid)
            message = f"Authenticated · branch {branch} · {len(files)} files"
            return ConnectionHealth(status="connected", message=message, latency_ms=1)
        except ConnectorError as exc:
            if exc.code == TransferErrorCode.SOURCE_NOT_FOUND:
                return ConnectionHealth(
                    status="connected",
                    message="Authenticated. Default dataset was not found.",
                    latency_ms=1,
                )
            raise
        finally:
            client.close()

    def list_namespaces(self, credentials) -> list[RemoteNamespace]:
        client = self._client(credentials)
        try:
            rid = client.default_rid
            if not rid:
                return []
            return [RemoteNamespace(name=rid, display_name=rid, kind="dataset")]
        finally:
            client.close()

    def list_objects(self, credentials, namespace: str, cursor: str | None = None) -> CatalogPage:
        client = self._client(credentials)
        try:
            branch = client.default_branch
            payload = client.list_files(namespace, branch, cursor=cursor)
            files = supported_files(list(payload.get("data") or []))
            items = []
            for entry in files:
                path = str(entry.get("path") or "")
                locator: Locator
                if self.capabilities and self.capabilities.source:
                    locator = FoundryDatasetFilesLocator(
                        dataset_rid=namespace, branch=branch, file_paths=[path]
                    )
                else:
                    locator = FoundryUploadLocator(
                        dataset_rid=namespace, branch=branch, file_name=path.split("/")[-1]
                    )
                items.append(
                    RemoteObject(
                        name=path,
                        display_name=path,
                        locator=locator,
                        size_bytes=int(entry.get("sizeBytes") or 0) or None,
                        updated_at=str(entry.get("updatedTime") or ""),
                        format="parquet" if path.casefold().endswith(".parquet") else "csv",
                    )
                )
            return CatalogPage(items=tuple(items), cursor=payload.get("nextPageToken"))
        finally:
            client.close()

    def inspect_object(self, credentials, locator: Locator) -> ObjectSchema:
        return ObjectSchema(locator=locator, columns=(), estimated_rows=None)

    def count_rows(self, credentials, locator: Locator) -> int | None:
        """Foundry file metadata does not provide a portable row-count API."""

        return None

    def extract(
        self, credentials, locator: Locator, *, batch_rows: int, batch_bytes: int
    ) -> Iterator[TransferBatch]:
        if not isinstance(locator, FoundryDatasetFilesLocator):
            raise ConnectorError(
                TransferErrorCode.SOURCE_NOT_FOUND, "Foundry source locator is invalid."
            )
        client = self._client(credentials)
        spool_root = Path(self.settings.pipeline_spool_root or "/tmp")
        spool_root.mkdir(parents=True, exist_ok=True)
        try:
            branch, listed = client.resolve_branch(locator.dataset_rid)
            available = {str(item.get("path") or ""): item for item in supported_files(listed)}
            if locator.file_paths == "all_supported":
                paths = list(available)
            else:
                paths = list(locator.file_paths)
                missing = [path for path in paths if path not in available]
                if missing:
                    raise ConnectorError(
                        TransferErrorCode.SOURCE_NOT_FOUND,
                        "A selected dataset file is no longer available.",
                    )
            sequence = 1
            yielded = False
            with tempfile.TemporaryDirectory(prefix="foundry-extract-", dir=spool_root) as temp_dir:
                extract_root = Path(temp_dir)
                for path in paths:
                    dest = extract_root / path.replace("/", "_")
                    client.download_file(locator.dataset_rid, branch, path, dest)
                    scan = (
                        pl.scan_parquet(dest)
                        if path.casefold().endswith(".parquet")
                        else pl.scan_csv(dest)
                    )
                    for frame in scan.collect_batches(
                        chunk_size=max(1, batch_rows), maintain_order=True
                    ):
                        if frame.height == 0:
                            continue
                        yielded = True
                        yield TransferBatch(
                            frame=frame,
                            row_count=frame.height,
                            byte_count=int(frame.estimated_size()),
                            sequence=sequence,
                        )
                        sequence += 1
            if not yielded:
                raise ConnectorError(
                    TransferErrorCode.SOURCE_NOT_FOUND, "The dataset has no CSV or Parquet files."
                )
        finally:
            client.close()

    def prepare_destination(
        self,
        credentials,
        locator: Locator,
        schema: ObjectSchema,
        write_policy: WritePolicy,
        *,
        run_id: str,
    ) -> LoadSession:
        self._load_credentials = dict(credentials)
        spool_root = Path(self.settings.pipeline_spool_root or "/tmp")
        spool_root.mkdir(parents=True, exist_ok=True)
        spool = spool_root / f"{run_id}.snappy.parquet"
        chunk_root = spool_root / f"{run_id}.chunks"
        chunk_root.mkdir(exist_ok=True)
        return LoadSession(
            locator=locator,
            write_policy=write_policy,
            staging_name=str(spool),
            columns=tuple(column.name for column in schema.columns),
            metadata={"chunk_root": str(chunk_root)},
        )

    def write_batch(self, load_session: LoadSession, batch: TransferBatch) -> BatchWriteResult:
        path = Path(load_session.staging_name)
        frame: pl.DataFrame = batch.frame
        if path.exists():
            existing = pl.read_parquet(path)
            if set(existing.columns) != set(frame.columns):
                raise ConnectorError(
                    TransferErrorCode.SCHEMA_DRIFT,
                    "The source schema changed during extraction.",
                    retryable=False,
                )
            frame = pl.concat([existing, frame.select(existing.columns)], how="vertical_relaxed")
            frame.write_parquet(path, compression="snappy")
            if path.stat().st_size > self.settings.pipeline_max_spool_bytes:
                raise ConnectorError(
                    TransferErrorCode.SPOOL_LIMIT_EXCEEDED,
                    "The destination staging data exceeds the configured spool size limit.",
                    retryable=False,
                )
        else:
            chunk_root = Path(load_session.metadata["chunk_root"])
            chunk_root.mkdir(parents=True, exist_ok=True)
            load_session.metadata.setdefault("columns", json.dumps(frame.columns))
            chunk_path = chunk_root / f"{batch.sequence:08d}.parquet"
            frame.write_parquet(chunk_path, compression="snappy")
            spool_bytes = int(load_session.metadata.get("spool_bytes", "0"))
            spool_bytes += chunk_path.stat().st_size
            load_session.metadata["spool_bytes"] = str(spool_bytes)
            if spool_bytes > self.settings.pipeline_max_spool_bytes:
                chunk_path.unlink(missing_ok=True)
                raise ConnectorError(
                    TransferErrorCode.SPOOL_LIMIT_EXCEEDED,
                    "The destination staging data exceeds the configured spool size limit.",
                    retryable=False,
                )
        load_session.metadata["rows"] = str(
            int(load_session.metadata.get("rows", "0")) + batch.row_count
        )
        return BatchWriteResult(
            rows_acknowledged=batch.row_count, bytes_acknowledged=batch.byte_count
        )

    def finalize(self, load_session: LoadSession) -> DestinationManifest:
        path = Path(load_session.staging_name)
        chunk_root_value = load_session.metadata.get("chunk_root")
        chunk_root = Path(chunk_root_value) if chunk_root_value else None
        client = None
        try:
            if not path.exists() and chunk_root is not None and chunk_root.is_dir():
                chunks = sorted(chunk_root.glob("*.parquet"))
                if chunks:
                    pl.concat(
                        [pl.scan_parquet(chunk) for chunk in chunks], how="vertical_relaxed"
                    ).sink_parquet(path, compression="snappy")
            if not path.exists():
                raise ConnectorError(
                    TransferErrorCode.PARTIAL_WRITE, "No Parquet spool was produced."
                )
            if path.stat().st_size > self.settings.pipeline_max_spool_bytes:
                raise ConnectorError(
                    TransferErrorCode.SPOOL_LIMIT_EXCEEDED,
                    "The destination staging data exceeds the configured spool size limit.",
                    retryable=False,
                )
            locator = load_session.locator
            if not isinstance(locator, FoundryUploadLocator):
                raise ConnectorError(
                    TransferErrorCode.DESTINATION_NOT_FOUND,
                    "Foundry destination locator is invalid.",
                )
            client = FoundryClient(getattr(self, "_load_credentials", {}), self.settings)
            payload = client.upload_file(locator.dataset_rid, locator.file_name, path)
            size = int(payload.get("sizeBytes") or path.stat().st_size)
            return DestinationManifest(
                locator=locator,
                rows=int(load_session.metadata.get("rows", "0"))
                or int(pl.read_parquet(path).height),
                bytes=size,
                remote_id=str(payload.get("filePath") or locator.file_name),
                details={"publication": locator.publication},
            )
        except ConnectorError as exc:
            if exc.code in {
                TransferErrorCode.CONNECTION_TIMEOUT,
                TransferErrorCode.PROVIDER_UNAVAILABLE,
            }:
                raise ConnectorError(
                    TransferErrorCode.PUBLISH_UNCERTAIN,
                    "The Foundry upload timed out before confirmation.",
                    retryable=False,
                ) from exc
            raise
        finally:
            if client is not None:
                client.close()
            self._load_credentials = {}
            self._cleanup_staging(path, chunk_root)

    def abort(self, load_session: LoadSession) -> None:
        path = Path(load_session.staging_name)
        chunk_root_value = load_session.metadata.get("chunk_root")
        self._cleanup_staging(path, Path(chunk_root_value) if chunk_root_value else None)

    @staticmethod
    def _cleanup_staging(path: Path, chunk_root: Path | None) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if chunk_root is not None:
            shutil.rmtree(chunk_root, ignore_errors=True)
