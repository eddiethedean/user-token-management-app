"""HTTP registration for CSV source inspection interactions."""

from __future__ import annotations

from fastapi import Request, status
from hedron import Badge, Hedron, OobUpdate
from starlette.responses import Response

from app.dependencies import Auth, DbSession, RequireCsrf
from app.services.csv_uploads import MAX_CSV_UPLOAD_BYTES, store_csv_upload
from app.ui.interactions import interaction_response, ok_fragment
from app.ui.params import CsvUploadForm
from app.ui.regions import CSV_INSPECTION, CSV_UPLOAD_STATE, TOAST_HOST
from app.ui.routes.pipeline_preview import CsvInspectionFragment


def register_pipeline_csv_routes(
    app: Hedron,
    *,
    inspection_fragment: CsvInspectionFragment,
) -> None:
    """Register CSV upload and inspection endpoints."""

    @app.action(
        "/pipeline/csv/inspect",
        fragment_regions=(CSV_INSPECTION, CSV_UPLOAD_STATE, TOAST_HOST),
        include_in_schema=False,
    )
    async def pipeline_csv_inspect(
        request: Request,
        auth: Auth,
        db: DbSession,
        _csrf: RequireCsrf,
        csv_file: CsvUploadForm,
    ) -> Response:
        try:
            content = await csv_file.read(MAX_CSV_UPLOAD_BYTES + 1)
            upload, inspection = store_csv_upload(
                db,
                user=auth.user,
                filename=csv_file.filename or "",
                content_type=csv_file.content_type or "text/csv",
                content=content,
                request=request,
            )
        except ValueError as exc:
            return await interaction_response(
                request,
                ok_fragment(
                    inspection_fragment(error=str(exc)),
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    toast=str(exc),
                    toast_tone="danger",
                    oob=(
                        OobUpdate(
                            Badge("Scan failed", tone="danger"),
                            element_id="pipeline-csv-upload-state",
                            swap="outerHTML",
                        ),
                    ),
                    region_id=CSV_INSPECTION.id,
                ),
            )
        finally:
            await csv_file.close()
        return await interaction_response(
            request,
            ok_fragment(
                inspection_fragment(upload, inspection),
                toast=f"Scanned {inspection.filename}: {len(inspection.columns)} columns detected.",
                oob=(
                    OobUpdate(
                        Badge("Scan complete", tone="success"),
                        element_id="pipeline-csv-upload-state",
                        swap="outerHTML",
                    ),
                ),
                region_id=CSV_INSPECTION.id,
            ),
        )
