"""Guardrail acceptance across HTTP review, saved decisions, and the real worker."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import replace

import polars as pl
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectors.base import ObjectSchema
from app.connectors.fake import DEMO_DATASET, FakeFoundryConnector, FakePostgresConnector
from app.connectors.registry import destination_writer_for, source_reader_for
from app.database import SessionLocal
from app.domain.pipelines.guardrails import guardrail_source_key
from app.models import AuditEvent, PipelineDefinition, PipelineRun, PipelineRunEvent, PipelineUpload
from app.services.pipeline_runs import snapshot_from_definition
from tests.helpers import csrf_from, web_login


@pytest.mark.parametrize("control_character", ["\n", "\r", "\t", "\x7f"])
def test_csv_upload_rejects_control_character_column_names_with_feedback(
    client, control_character
) -> None:
    web_login(client, next_path="/pipeline")
    csrf = csrf_from(client.get("/pipeline").text)
    content = f'id,"national{control_character}identifier"\n1,123-45-6789\n'.encode()
    response = client.post(
        "/pipeline/csv/inspect",
        data={"csrf_token": csrf},
        files={"csv_file": ("unsupported-name.csv", content, "text/csv")},
        headers={"HX-Request": "true", "HX-Target": "pipeline-csv-inspection"},
    )
    assert response.status_code == 422
    assert "control characters" in response.text
    assert "Rename those columns and upload the file again." in response.text
    assert "123-45-6789" not in response.text
    with SessionLocal() as db:
        assert db.scalar(select(PipelineUpload)) is None


def test_legacy_csv_scan_returns_feedback_for_unsupported_column_names(
    client, demo_connections, monkeypatch
) -> None:
    web_login(client, next_path="/pipeline")
    csrf = csrf_from(client.get("/pipeline").text)
    # Recreate an upload accepted before header validation was tightened.
    with monkeypatch.context() as legacy:
        legacy.setattr(
            "app.services.csv_uploads.csv_headers",
            lambda raw_headers, **kwargs: [header.strip() for header in raw_headers],
        )
        upload = client.post(
            "/pipeline/csv/inspect",
            data={"csrf_token": csrf},
            files={
                "csv_file": (
                    "legacy-name.csv",
                    b'id,"national\nidentifier"\n1,123-45-6789\n',
                    "text/csv",
                )
            },
            headers={"HX-Request": "true", "HX-Target": "pipeline-csv-inspection"},
        )
    assert upload.status_code == 200
    match = re.search(r'name="source_upload_id" value="([^"]+)"', upload.text)
    assert match is not None
    response = client.post(
        "/pipeline/preview",
        data={
            "csrf_token": csrf,
            "source_provider": "csv",
            "source_upload_id": match.group(1),
            "destination_provider": "postgres",
            "destination_schema": "public",
            "destination_table": "legacy_name_review",
            "write_mode": "append",
            "scan_sensitive_data": "true",
        },
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert response.status_code == 422
    assert "column names contain no control characters" in response.text
    assert "rename unsupported columns and scan again" in response.text
    assert "123-45-6789" not in response.text


@pytest.mark.parametrize("resolved_columns", [(), ("ssn",), ("ssn", "alternate")])
def test_saved_content_decisions_protect_every_batch_and_keep_review_value_free(
    client, demo_connections, monkeypatch, caplog, resolved_columns
) -> None:
    web_login(client, next_path="/pipeline")
    csrf = csrf_from(client.get("/pipeline").text)
    # The first batch is clean. The only matches occur in the final batch, so
    # inspecting a sample or writing while scanning would violate this test.
    raw_ssn = "123-45-6789"
    raw_alternate = "234-56-7890"
    rows = ["1,,public", *(f"{row},ordinary,public" for row in range(2, 1001))]
    rows.extend([f"1001,{raw_ssn},{raw_alternate}", f"1002,{raw_ssn},"])
    content = ("id,ssn,alternate\n" + "\n".join(rows) + "\n").encode()
    upload = client.post(
        "/pipeline/csv/inspect",
        data={"csrf_token": csrf},
        files={"csv_file": ("guardrail-acceptance.csv", content, "text/csv")},
        headers={"HX-Request": "true", "HX-Target": "pipeline-csv-inspection"},
    )
    assert upload.status_code == 200
    match = re.search(r'name="source_upload_id" value="([^"]+)"', upload.text)
    assert match is not None
    upload_id = match.group(1)
    form = {
        "csrf_token": csrf,
        "pipeline_name": "Late-batch guardrail acceptance",
        "source_provider": "csv",
        "source_upload_id": upload_id,
        "destination_provider": "postgres",
        "destination_schema": "public",
        "destination_table": "guardrail_acceptance",
        "write_mode": "append",
    }
    preview = client.post(
        "/pipeline/preview",
        data={**form, "scan_sensitive_data": "true"},
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert preview.status_code == 200
    summary_match = re.search(r'name="guardrail_scan_result" value="([^"]+)"', preview.text)
    assert summary_match is not None
    summary = json.loads(html.unescape(summary_match.group(1)))
    assert {(item["column"], item["count"]) for item in summary["findings"]} == {
        ("ssn", 2),
        ("alternate", 1),
    }
    assert all(item["detector"] == "ssn" for item in summary["findings"])
    assert "Hash" in preview.text and "Remove" in preview.text
    assert raw_ssn not in preview.text and raw_alternate not in preview.text

    source_key = guardrail_source_key("csv", upload_id=upload_id)
    selected = {"ssn": "hash", "alternate": "remove"}
    saved = client.post(
        "/pipeline/save",
        data={
            **form,
            "guardrail_actions": [
                json.dumps(["ssn", column, selected[column], source_key])
                for column in resolved_columns
            ],
        },
    )
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == form["pipeline_name"])
        )
        assert pipeline is not None
        snapshot = snapshot_from_definition(pipeline)
        assert {(action.column, action.action) for action in snapshot.guardrail_actions} == {
            (column, selected[column]) for column in resolved_columns
        }
        assert json.loads(pipeline.guardrail_actions_json) == [
            action.model_dump(mode="json") for action in snapshot.guardrail_actions
        ]
        pipeline_id = pipeline.id

    source = source_reader_for("csv")
    destination = destination_writer_for("postgres")
    original_extract = source.extract
    original_prepare = destination.prepare_destination
    original_write = destination.write_batch
    scan_finished = False
    scanned_batches: list[int] = []
    output: list[pl.DataFrame] = []
    prepared_schemas = []

    def record_extract(*args, **kwargs):
        nonlocal scan_finished
        # Providers may emit batches below the requested ceiling. Keep this
        # case independent of the runtime's captured default batch size.
        kwargs["batch_rows"] = 1000
        for batch in original_extract(*args, **kwargs):
            scanned_batches.append(batch.row_count)
            yield batch
        scan_finished = True

    def record_prepare(*args, **kwargs):
        assert scan_finished and scanned_batches == [1000, 2]
        prepared_schemas.append(args[2])
        return original_prepare(*args, **kwargs)

    def record_write(session, batch):
        assert scan_finished
        output.append(batch.frame)
        return original_write(session, batch)

    monkeypatch.setattr(source, "extract", record_extract)
    monkeypatch.setattr(destination, "prepare_destination", record_prepare)
    monkeypatch.setattr(destination, "write_batch", record_write)
    monkeypatch.setattr("app.worker.source_reader_for", lambda _provider: source)
    monkeypatch.setattr("app.worker.destination_writer_for", lambda _provider: destination)
    settings = get_settings()
    with caplog.at_level(logging.INFO):
        queued = client.post(
            "/pipeline/runs",
            data={"csrf_token": csrf, "pipeline_id": pipeline_id},
            headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
        )
    assert queued.status_code == 202

    with SessionLocal() as db:
        run = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert run is not None
        run_id = run.id
        review = json.loads(run.guardrail_json or "{}")
        assert run.pipeline_definition_id == pipeline_id
        assert json.loads(run.definition_snapshot_json)["guardrail_actions"] == [
            action.model_dump(mode="json") for action in snapshot.guardrail_actions
        ]
        assert review["scan_complete"] is True
        assert review["scanned_rows"] == 1002
        assert {(item["column"], item["count"]) for item in review["findings"]} == {
            ("ssn", 2),
            ("alternate", 1),
        }
        if len(resolved_columns) == 2:
            assert run.status == "succeeded"
            assert review["outcome"] == "applied"
            assert review["destination_outcome"] == "committed"
            assert all(item["outcome"] == "applied" for item in review["findings"])
            assert len(output) == 2
            assert [column.name for column in prepared_schemas[0].columns] == ["id", "ssn"]
            transferred = pl.concat(output)
            assert transferred.columns == ["id", "ssn"]
            assert transferred["id"].to_list() == list(range(1, 1003))
            assert transferred["ssn"].null_count() == 1
            assert all(
                re.fullmatch(r"[0-9a-f]{64}", value) for value in transferred["ssn"].drop_nulls()
            )
            assert transferred["ssn"][-1] == transferred["ssn"][-2]
            assert transferred["ssn"][1] == transferred["ssn"][999]
            source_columns = json.loads(run.source_manifest_json or "{}")["schema"]["columns"]
            assert [column["name"] for column in source_columns] == ["id", "ssn", "alternate"]
        else:
            assert run.status == "blocked"
            assert run.error_code == "sensitive_data_guardrail_blocked"
            assert review["outcome"] == "blocked"
            assert review["destination_outcome"] == "unchanged"
            assert {
                item["column"]
                for item in review["findings"]
                if item["outcome"] == "review_required"
            } == {"ssn", "alternate"} - set(resolved_columns)
            assert not prepared_schemas and not output
            assert run.loaded_rows == 0
        events = db.scalars(select(PipelineRunEvent).where(PipelineRunEvent.run_id == run_id)).all()
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type.like("pipeline.run.%"))
        ).all()
        assert events and audits
        diagnostics = "\n".join(
            [
                run.guardrail_json or "",
                run.error_summary or "",
                caplog.text,
                *(event.message for event in events),
                *(event.detail for event in audits),
            ]
        )
        assert raw_ssn not in diagnostics and raw_alternate not in diagnostics
        assert (
            settings.api_token_key_ring[settings.api_token_active_key_id].hex() not in diagnostics
        )

    restored = client.get(f"/pipeline?pipeline_id={pipeline_id}")
    assert restored.status_code == 200
    assert "Sensitive-data guardrails" in restored.text
    assert (
        "Actions applied" in restored.text
        if len(resolved_columns) == 2
        else "Blocked before writes" in restored.text
    )
    assert raw_ssn not in restored.text and raw_alternate not in restored.text


@pytest.mark.parametrize("table_marked,resolved", [(False, True), (False, False), (True, False)])
def test_foundry_metadata_review_decisions_and_table_block_survive_http_run(
    client, demo_connections, monkeypatch, table_marked, resolved
) -> None:
    original_inspect = FakeFoundryConnector.inspect_object
    original_extract = FakeFoundryConnector.extract
    original_write = FakePostgresConnector.write_batch
    column_markers = {} if table_marked else {"unit_name": ("pii",), "score": ("pii",)}
    table_markers = ("pii",) if table_marked else ()
    extracted = []
    transferred = []

    def inspect(self, credentials, locator):
        if table_marked:
            return ObjectSchema(locator=locator, columns=(), sensitivity_markers=table_markers)
        schema = original_inspect(self, credentials, locator)
        return replace(
            schema,
            columns=tuple(
                replace(column, sensitivity_markers=column_markers.get(column.name, ()))
                for column in schema.columns
            ),
            column_sensitivity_markers=tuple(column_markers.items()),
        )

    def extract(self, *args, **kwargs):
        extracted.append(True)
        return original_extract(self, *args, **kwargs)

    def write(self, session, batch):
        transferred.append(batch.frame)
        return original_write(self, session, batch)

    monkeypatch.setattr(FakeFoundryConnector, "inspect_object", inspect)
    monkeypatch.setattr(
        FakeFoundryConnector,
        "inspect_sensitivity_metadata",
        lambda *args: (table_markers, column_markers),
    )
    monkeypatch.setattr(FakeFoundryConnector, "extract", extract)
    monkeypatch.setattr(FakePostgresConnector, "write_batch", write)
    web_login(client, next_path="/pipeline")
    csrf = csrf_from(client.get("/pipeline").text)
    form = {
        "csrf_token": csrf,
        "pipeline_name": "Metadata acceptance",
        "source_provider": "mss",
        "source_schema": DEMO_DATASET,
        "source_table": "mission_orders.parquet",
        "destination_provider": "postgres",
        "destination_schema": "public",
        "destination_table": "metadata_acceptance",
        "write_mode": "append",
    }
    preview = client.post(
        "/pipeline/preview",
        data=form,
        headers={"HX-Request": "true", "HX-Target": "pipeline-preview-region"},
    )
    assert preview.status_code == 200
    assert "Foundry metadata" in preview.text
    selected_actions = []
    if table_marked:
        assert "Table-level sensitivity" in preview.text
        assert "Blocked before destination writes" in preview.text
    else:
        assert "Action for foundry_metadata finding in unit_name" in preview.text
        assert "Action for foundry_metadata finding in score" in preview.text
        if resolved:
            source_key = guardrail_source_key("mss", DEMO_DATASET, "mission_orders.parquet")
            selected_actions = [
                json.dumps(["foundry_metadata", "unit_name", "hash", source_key]),
                json.dumps(["foundry_metadata", "score", "remove", source_key]),
            ]
    saved = client.post("/pipeline/save", data={**form, "guardrail_actions": selected_actions})
    assert saved.status_code == 303
    with SessionLocal() as db:
        pipeline = db.scalar(
            select(PipelineDefinition).where(PipelineDefinition.name == form["pipeline_name"])
        )
        assert pipeline is not None
        pipeline_id = pipeline.id
    queued = client.post(
        "/pipeline/runs",
        data={"csrf_token": csrf, "pipeline_id": pipeline_id},
        headers={"HX-Request": "true", "HX-Target": "pipeline-run-monitor"},
    )
    assert queued.status_code == 202
    with SessionLocal() as db:
        run = db.scalar(
            select(PipelineRun).where(PipelineRun.pipeline_definition_id == pipeline_id)
        )
        assert run is not None
        review = json.loads(run.guardrail_json or "{}")
        assert review["outcome"] == ("applied" if resolved else "blocked")
        assert run.status == ("succeeded" if resolved else "blocked")
        if table_marked:
            assert not extracted and not transferred
            assert review["findings"][0]["table"] == DEMO_DATASET
            assert review["scan_complete"] is False
        elif resolved:
            assert {(item["column"], item["action"]) for item in review["findings"]} == {
                ("unit_name", "hash"),
                ("score", "remove"),
            }
            assert transferred
            assert all(frame.columns == ["event_id", "unit_name", "ready"] for frame in transferred)
            assert all(
                re.fullmatch(r"[0-9a-f]{64}", value)
                for frame in transferred
                for value in frame["unit_name"]
            )
        else:
            assert extracted and not transferred
            assert {(item["column"], item["outcome"]) for item in review["findings"]} == {
                ("unit_name", "review_required"),
                ("score", "review_required"),
            }
        assert "Unit-1" not in (run.guardrail_json or "")
    restored = client.get(f"/pipeline?pipeline_id={pipeline_id}")
    assert restored.status_code == 200
    assert "Foundry metadata" in restored.text
    assert (
        "Actions applied" in restored.text if resolved else "Blocked before writes" in restored.text
    )
    assert "Unit-1" not in restored.text
