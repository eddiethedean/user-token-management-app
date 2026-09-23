"""Authenticated data-movement demo workspace."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import HTTPException, Request, status
from hedron import (
    ActionGroup,
    Alert,
    AsyncRegion,
    AttrHost,
    Avatar,
    Badge,
    Button,
    CircularProgress,
    ConnectorFlow,
    ConnectorNode,
    ConnectorTrack,
    DescriptionList,
    Expander,
    FileUpload,
    FlowStep,
    FormField,
    FormGrid,
    Grid,
    Hedron,
    HedronRouter,
    Inline,
    Metric,
    ProcessFlow,
    Progress,
    ResourceList,
    ResourceRow,
    ScrollRegion,
    Stack,
    StateView,
    Status,
    Surface,
    Table,
    TableColumn,
    Text,
    Timeline,
    html,
)
from hedron_core import NodeLike
from starlette.responses import Response

import app.services.pipeline_runs as pipeline_run_service
from app.application.catalogs import CatalogAccess, CatalogOperationRunner
from app.application.dto import ActorContext
from app.application.pipelines import PipelineAuthoringOperation
from app.connectors.errors import ConnectorError
from app.connectors.locators import (
    DATASET_RID_PATTERN,
    FoundryDatasetFilesLocator,
    FoundryUploadLocator,
    PostgresTableLocator,
    PostgresUpsertPolicy,
    normalize_foundry_source_paths,
    parse_locator,
    parse_snapshot,
    parse_write_policy,
    postgres_table,
)
from app.connectors.registry import (
    capabilities_for,
    route_allowed,
    writer_enabled,
)
from app.dependencies import Auth, DbSession, SettingsDep
from app.domain.feedback import DataImpact
from app.models import PipelineDefinition, PipelineUpload
from app.services.catalogs import (
    CREATE_TABLE_VALUE,
    CSV_SOURCE_CATALOG,
    ProviderCatalog,
    all_provider_catalogs,
    require_catalog_provider,
)
from app.services.csv_uploads import (
    MAX_CSV_UPLOAD_BYTES,
    CsvInspection,
    inspection_from_upload,
)
from app.services.pipeline_metadata import provenance_label, schema_diff
from app.services.pipeline_runs import (
    events_after,
    latest_run_map,
    owned_run,
)
from app.services.pipelines import list_pipelines, locators_overlap
from app.services.secrets import list_user_secrets
from app.ui.design_system import (
    DATA_MOVER_DESIGN,
    apply_data_recipe,
    stacked_surface,
    surface_card,
)
from app.ui.design_system import DataMoverPageHeader as PageHeader
from app.ui.forms import csrf_hidden
from app.ui.http import render_authenticated_view
from app.ui.layout import INDICATOR, alert_box
from app.ui.params import NoticeQuery
from app.ui.partials.feedback import feedback_panel
from app.ui.presenters.feedback import run_outcome, verification_summary
from app.ui.presenters.pipeline import SavedPipelineFields, saved_pipeline_form_data
from app.ui.presenters.run_status import (
    EVENT_STAGE_LABELS,
    destination_count_metric,
    run_action_state,
    run_flow_statuses,
    run_flow_steps,
    run_progress,
    run_stage_copy,
)
from app.ui.regions import (
    MAIN_PANEL,
    PIPELINE_SAVE_NOTICE,
    SIDE_NAV,
)
from app.ui.routes.pipeline_context import (
    request_metadata,
    run_owned_sync,
    with_user_catalog,
    with_user_session,
)
from app.ui.tabs import NavigationTabs
from app.ui.urls import form_action, hx_attrs


@dataclass(frozen=True)
class SwapEligibility:
    allowed: bool
    reason: str = ""


WriterPolicy = Callable[[str], bool]


def _provider_label(provider: str) -> str:
    if provider == "csv":
        return CSV_SOURCE_CATALOG.label
    return require_catalog_provider(provider).label


def _option(
    value: str,
    label: str,
    *,
    selected: bool = False,
    disabled: bool = False,
    **data: str,
):
    attrs: dict[str, Any] = {"value": value}
    if selected:
        attrs["selected"] = True
    if disabled:
        attrs["disabled"] = True
    if data:
        attrs["data"] = data
    return html.option(label, **attrs)


_WRITE_MODE_LABELS = {
    "upsert": "Upsert on primary key",
    "append": "Append only",
    "replace": "Replace destination",
}


def _resolved_write_mode(
    catalog: ProviderCatalog | None, selected: str, *, upsert_available: bool = True
) -> str:
    supported = tuple(
        mode
        for mode in (catalog.write_modes if catalog is not None else ())
        if mode != "upsert" or upsert_available
    )
    if selected in supported:
        return selected
    return supported[0] if supported else ""


def _write_mode_select(
    request: Request,
    catalog: ProviderCatalog | None,
    *,
    selected: str,
    upsert_available: bool = True,
    oob: bool = False,
) -> NodeLike:
    resolved = _resolved_write_mode(catalog, selected, upsert_available=upsert_available)
    supported = tuple(
        mode
        for mode in (catalog.write_modes if catalog is not None else ())
        if mode != "upsert" or upsert_available
    )
    options = [
        _option(mode, _WRITE_MODE_LABELS[mode], selected=mode == resolved) for mode in supported
    ]
    if not options:
        options = [_option("", "Choose a destination first", selected=True, disabled=True)]
    attrs: dict[str, Any] = {
        "id": "pipeline-mode-select",
        "name": "write_mode",
        "disabled": not supported,
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-mode-select"
    attrs.update(
        hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        )
    )
    return html.select(*options, **attrs)


def _upsert_keys(
    catalog_access: CatalogAccess, provider: str, namespace: str, object_name: str
) -> tuple[tuple[str, ...], ...]:
    if (
        provider != "postgres"
        or not namespace
        or not object_name
        or object_name == CREATE_TABLE_VALUE
    ):
        return ()
    try:
        schema = catalog_access.inspect_object("postgres", postgres_table(namespace, object_name))
    except Exception:
        return ()
    keys = [tuple(schema.primary_key), *map(tuple, schema.unique_constraints)]
    return tuple(dict.fromkeys(key for key in keys if key))


def _upsert_key_select(
    keys: tuple[tuple[str, ...], ...], *, selected: str = "", oob: bool = False
) -> NodeLike:
    selected_key = tuple(item.strip() for item in selected.split(",") if item.strip())
    options = [
        _option(
            ",".join(key),
            ", ".join(key),
            selected=key == selected_key or (not selected_key and index == 0),
        )
        for index, key in enumerate(keys)
    ]
    if not options:
        options = [_option("", "No primary or unique key", selected=True, disabled=True)]
    attrs: dict[str, Any] = {
        "id": "pipeline-upsert-key-select",
        "name": "conflict_columns",
        "disabled": not keys,
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-upsert-key-select"
    return html.select(*options, **attrs)


def _connection_configured(details: dict[str, str | bool]) -> bool:
    return details["configured"] is True and details["validation"] == "connected"


def _connection_runnable(details: dict[str, str | bool]) -> bool:
    return _connection_configured(details)


def _connection_provisionable(details: dict[str, str | bool], catalog: ProviderCatalog) -> bool:
    return (
        details["configured"] is True
        and catalog.dataset_creation
        and details["validation"] in {"connected", "untested"}
    )


def _configured_catalogs(
    connections: dict[str, dict[str, str | bool]],
    *,
    role: str,
    writer_policy: WriterPolicy,
) -> tuple[ProviderCatalog, ...]:
    return tuple(
        catalog
        for catalog in all_provider_catalogs()
        if catalog.name in connections
        and (
            _connection_configured(connections[catalog.name])
            or (
                role == "destination"
                and _connection_provisionable(connections[catalog.name], catalog)
            )
        )
        and (
            (role == "source" and catalog.source)
            or (role == "destination" and catalog.destination and writer_policy(catalog.name))
        )
    )


def _provider_options(
    connections: dict[str, dict[str, str | bool]],
    *,
    selected: str,
    role: str,
    counterpart: str = "",
    writer_policy: WriterPolicy,
):
    return [
        _option(
            catalog.name,
            f"{catalog.label} · {catalog.technology}",
            selected=catalog.name == selected,
            configured=str(bool(connections[catalog.name]["configured"])).lower(),
            validation=str(connections[catalog.name]["validation"]),
            runtime=str(connections[catalog.name]["runtime"]),
            technology=catalog.technology,
            region=catalog.namespaces_label,
        )
        for catalog in all_provider_catalogs()
        if catalog.name in connections
        and (
            _connection_configured(connections[catalog.name])
            or (
                role == "destination"
                and _connection_provisionable(connections[catalog.name], catalog)
            )
        )
        and (
            (role == "source" and catalog.source)
            or (role == "destination" and catalog.destination and writer_policy(catalog.name))
        )
        and (
            not counterpart
            or (
                route_allowed(catalog.name, counterpart)
                if role == "source"
                else route_allowed(counterpart, catalog.name)
            )
        )
    ]


def _source_provider_options(
    connections: dict[str, dict[str, str | bool]],
    *,
    selected: str,
    target_provider: str,
    writer_policy: WriterPolicy,
):
    return [
        *_provider_options(
            connections,
            selected=selected,
            role="source",
            writer_policy=writer_policy,
        ),
        *(
            [
                _option(
                    "csv",
                    "CSV file · Upload from device",
                    selected=selected == "csv",
                    configured="false",
                    validation="local",
                    runtime="",
                    technology=CSV_SOURCE_CATALOG.technology,
                    region=CSV_SOURCE_CATALOG.region,
                )
            ]
            if not target_provider or route_allowed("csv", target_provider)
            else []
        ),
    ]


def _source_provider_select(
    request: Request,
    connections: dict[str, dict[str, str | bool]],
    *,
    selected: str,
    target_provider: str,
    writer_policy: WriterPolicy,
    oob: bool = False,
) -> NodeLike:
    attrs: dict[str, Any] = {
        "id": "pipeline-source-select",
        "name": "source_provider",
        "data": {"pipeline-control": "source-provider"},
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        ),
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-source-select"
    return html.select(
        *_source_provider_options(
            connections,
            selected=selected,
            target_provider=target_provider,
            writer_policy=writer_policy,
        ),
        **attrs,
    )


def _source_namespace_control(
    request: Request,
    catalog_access: CatalogAccess,
    provider: str,
    *,
    preferred_schema: str = "",
    oob: bool = False,
) -> NodeLike:
    """Render a source namespace control, allowing arbitrary Foundry dataset RIDs."""

    label = require_catalog_provider(provider).namespaces_label
    attrs: dict[str, Any] = {
        "id": "pipeline-source-schema-select",
        "name": "source_schema",
        "data": {"pipeline-control": "source-schema", "field-label": label},
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        ),
    }
    if provider.casefold() in {"mss", "mcscop"}:
        attrs["list"] = "pipeline-source-dataset-suggestions"
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-source-schema-select"

    if provider.casefold() in {"mss", "mcscop"}:
        return html.input(
            type="text",
            value=preferred_schema,
            maxlength="240",
            placeholder="ri.foundry.main.dataset…",
            autocomplete="off",
            spellcheck="false",
            required=True,
            **attrs,
        )

    return html.select(
        *_schema_options(catalog_access, provider, preferred_schema=preferred_schema),
        **attrs,
    )


def _eligible_destinations(
    connections,
    source_provider: str,
    *,
    writer_policy: WriterPolicy,
) -> tuple[ProviderCatalog, ...]:
    return tuple(
        catalog
        for catalog in _configured_catalogs(
            connections, role="destination", writer_policy=writer_policy
        )
        if route_allowed(source_provider, catalog.name)
    )


def _destination_unavailable_message(
    connections,
    source_provider: str,
    *,
    writer_policy: WriterPolicy,
) -> str:
    if not any(details["configured"] for details in connections.values()):
        return "Set up at least one connection before building or running a pipeline."
    disabled = [
        catalog.label
        for catalog in all_provider_catalogs()
        if catalog.name in connections
        and catalog.destination
        and _connection_configured(connections[catalog.name])
        and route_allowed(source_provider, catalog.name)
        and not writer_policy(catalog.name)
    ]
    if disabled:
        return (
            f"Writing to {', '.join(disabled)} is disabled by the administrator. "
            "A successful connection check does not enable writes."
        )
    return (
        "No writable destination is available for this source. "
        "Choose another source or configure and validate a supported destination in Connections."
    )


def _destination_provider_select(
    request,
    connections,
    *,
    source_provider: str,
    selected: str,
    writer_policy: WriterPolicy,
    oob=False,
):
    options = _provider_options(
        connections,
        selected=selected,
        role="destination",
        counterpart=source_provider,
        writer_policy=writer_policy,
    )
    if not options:
        options = [
            _option(
                "",
                "No writable destination for this source"
                if any(details["configured"] for details in connections.values())
                else "Set up a connection first",
                selected=True,
                disabled=True,
            )
        ]
    return html.select(
        *options,
        id="pipeline-target-select",
        name="destination_provider",
        data={
            "pipeline-control": "target-provider",
            "file-destination": str(selected in {"mss", "mcscop"}).lower(),
        },
        disabled=not selected,
        **({"hx-swap-oob": "outerHTML:#pipeline-target-select"} if oob else {}),
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        ),
    )


def _namespace_entries(catalog_access: CatalogAccess, provider: str) -> list[tuple[str, str]]:
    return [(item.name, item.display_name) for item in catalog_access.list_namespaces(provider)]


def _object_entries(
    catalog_access: CatalogAccess, provider: str, namespace: str, *, destination: bool = False
) -> list[tuple[str, str]]:
    try:
        page = catalog_access.list_objects(provider, namespace)
    except ConnectorError:
        # A saved remote namespace can disappear or, in the demo adapter, be
        # empty after a process restart. Keep the workspace usable so the user
        # can select another destination or provision a replacement.
        return []
    entries = [(item.name, item.display_name) for item in page.items]
    return _destination_object_entries(provider, entries) if destination else entries


def _destination_object_entries(
    provider: str, entries: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Keep only file formats the selected destination writer can publish."""

    if provider.casefold() != "mcscop":
        return entries
    return [entry for entry in entries if entry[0].casefold().endswith(".parquet")]


def _first_namespace(catalog_access: CatalogAccess, provider: str) -> str:
    entries = _namespace_entries(catalog_access, provider)
    return entries[0][0] if entries else ""


def _first_object(catalog_access: CatalogAccess, provider: str, namespace: str) -> str:
    if not namespace:
        return ""
    entries = _object_entries(catalog_access, provider, namespace)
    return entries[0][0] if entries else ""


def _normalized_selection(
    catalog_access: CatalogAccess,
    provider: str,
    namespace: str,
    object_name: str,
    *,
    preserve_create: bool = False,
    freeform_namespace: bool = False,
    destination: bool = False,
) -> tuple[str, str]:
    """Keep a form selection valid when its provider changes."""
    if not provider:
        return "", ""
    valid_freeform_namespace = freeform_namespace and bool(DATASET_RID_PATTERN.fullmatch(namespace))
    namespaces = _namespace_entries(catalog_access, provider)
    if not namespaces:
        if valid_freeform_namespace:
            return namespace, object_name
        return (
            "",
            CREATE_TABLE_VALUE if preserve_create and object_name == CREATE_TABLE_VALUE else "",
        )
    namespace_names = {name for name, _ in namespaces}
    resolved_namespace = (
        namespace
        if valid_freeform_namespace
        else namespace
        if namespace in namespace_names
        else namespaces[0][0]
    )
    objects = _object_entries(
        catalog_access, provider, resolved_namespace, destination=destination
    )
    object_names = {name for name, _ in objects}
    if preserve_create and object_name == CREATE_TABLE_VALUE:
        return resolved_namespace, CREATE_TABLE_VALUE
    if valid_freeform_namespace and object_name:
        # Foundry source paths may be outside the current catalog page and may
        # contain multiple comma/newline-separated files. Preserve the user's
        # entry and let the typed locator validate it at save/runtime.
        return resolved_namespace, object_name
    resolved_object = (
        object_name if object_name in object_names else (objects[0][0] if objects else "")
    )
    return resolved_namespace, resolved_object


def _schema_options(catalog_access: CatalogAccess, provider: str, preferred_schema: str = ""):
    entries = _namespace_entries(catalog_access, provider)
    return [
        _option(
            name,
            display,
            selected=name == preferred_schema or (not preferred_schema and index == 0),
        )
        for index, (name, display) in enumerate(entries)
    ]


def _committed_new_table_name(value: str) -> str:
    prefix = f"{CREATE_TABLE_VALUE}:"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _table_options(
    catalog_access: CatalogAccess,
    provider: str,
    schema_name: str,
    *,
    allow_create: bool = False,
    additional_tables: tuple[str, ...] = (),
    preferred_table: str = "",
    create_label: str = "table",
    destination: bool = False,
):
    entries = (
        _object_entries(catalog_access, provider, schema_name, destination=destination)
        if schema_name
        else []
    )
    known = {name for name, _ in entries}
    options = [
        _option(name, display, selected=(name == preferred_table or index == 0))
        for index, (name, display) in enumerate(entries)
    ]
    for table_name in additional_tables:
        if table_name not in known and (
            not destination or _destination_object_entries(provider, [(table_name, table_name)])
        ):
            options.append(_option(table_name, table_name))
    if allow_create:
        options.append(
            _option(
                CREATE_TABLE_VALUE,
                f"＋ Create a new {create_label}…",
                selected=preferred_table == CREATE_TABLE_VALUE,
            )
        )
    return options


def _source_object_control(
    request: Request,
    catalog_access: CatalogAccess,
    provider: str,
    namespace: str,
    *,
    preferred_object: str = "",
    oob: bool = False,
):
    """Render a catalog selector or a flexible Foundry file-path input."""

    if provider.casefold() in {"mss", "mcscop"}:
        attrs: dict[str, Any] = {
            "id": "pipeline-source-table-select",
            "name": "source_table",
            "value": preferred_object,
            "maxlength": "1000",
            "placeholder": "file.parquet or file1.parquet, file2.csv",
            "list": "pipeline-source-file-suggestions",
            "autocomplete": "off",
            "spellcheck": "false",
            "required": True,
            "data": {
                "pipeline-control": "source-table",
                "field-label": "File(s)",
            },
            **hx_attrs(
                request,
                path="/pipeline/preview",
                method="post",
                target="#pipeline-preview-region",
                swap="none",
                include="#pipeline-form",
                trigger="change",
            ),
        }
        if oob:
            attrs["hx-swap-oob"] = "outerHTML:#pipeline-source-table-select"
        return html.input(type="text", **attrs)

    options = _table_options(
        catalog_access,
        provider,
        namespace,
        preferred_table=preferred_object,
    )
    attrs = {
        "id": "pipeline-source-table-select",
        "name": "source_table",
        "data": {"pipeline-control": "source-table"},
        **({"hx-swap-oob": "outerHTML:#pipeline-source-table-select"} if oob else {}),
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        ),
    }
    return html.select(*options, **attrs)


def _source_catalog_suggestions(
    catalog_access: CatalogAccess, provider: str, namespace: str, *, oob: bool = False
) -> tuple[NodeLike, NodeLike]:
    is_foundry_source = provider.casefold() in {"mss", "mcscop"}
    datasets = (
        [
            html.option(display, value=name)
            for name, display in _namespace_entries(catalog_access, provider)
        ]
        if is_foundry_source
        else []
    )
    files = (
        [
            html.option(display, value=name)
            for name, display in _object_entries(catalog_access, provider, namespace)
        ]
        if is_foundry_source
        else []
    )
    dataset_attrs = {"id": "pipeline-source-dataset-suggestions"}
    file_attrs = {"id": "pipeline-source-file-suggestions"}
    if oob:
        dataset_attrs["hx-swap-oob"] = "outerHTML:#pipeline-source-dataset-suggestions"
        file_attrs["hx-swap-oob"] = "outerHTML:#pipeline-source-file-suggestions"
    return html.datalist(*datasets, **dataset_attrs), html.datalist(*files, **file_attrs)


def _swap_direction_button(
    request: Request, *, can_swap: bool, reason: str = "", oob: bool = False
) -> NodeLike:
    button_attrs: dict[str, Any] = {
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
        ),
        "hx-vals": '{"swap_direction":"true"}',
    }
    wrapper_attrs: dict[str, Any] = {"id": "pipeline-swap-direction"}
    if oob:
        wrapper_attrs["hx-swap-oob"] = "outerHTML:#pipeline-swap-direction"
    described_by = "pipeline-availability-note"
    if not can_swap and reason:
        described_by += " pipeline-swap-unavailable-note"
    reason_note = (
        html.p(
            reason,
            id="pipeline-swap-unavailable-note",
            class_="hedron-process-flow-description",
            role="note",
        )
        if not can_swap and reason
        else None
    )
    return html.div(
        Button(
            "Swap direction",
            variant="secondary",
            size="sm",
            type="button",
            attrs={
                "data-pipeline-swap": "true",
                "aria-describedby": described_by,
                **button_attrs,
            },
            disabled=not can_swap,
        ),
        reason_note,
        **wrapper_attrs,
    )


def _selection_overlaps(
    source_provider: str,
    source_schema: str,
    source_table: str,
    destination_provider: str,
    destination_schema: str,
    destination_table: str,
    destination_table_new: str = "",
) -> bool:
    if source_provider != destination_provider:
        return False
    try:
        if source_provider == "postgres":
            source_locator = postgres_table(source_schema, source_table)
            destination_locator = postgres_table(destination_schema, destination_table)
        elif source_provider in {"mss", "mcscop"}:
            source_locator = FoundryDatasetFilesLocator(
                dataset_rid=source_schema,
                branch="master",
                file_paths=normalize_foundry_source_paths(source_table),
            )
            destination_name = (
                _committed_new_table_name(destination_table_new)
                or destination_table_new.strip()
                or "new_table"
                if destination_table == CREATE_TABLE_VALUE
                else _committed_new_table_name(destination_table)
            )
            if not destination_name.endswith(".parquet"):
                destination_name = f"{destination_name}.snappy.parquet"
            destination_locator = FoundryUploadLocator(
                dataset_rid=destination_schema,
                branch="master",
                file_name=destination_name,
            )
        else:
            return False
    except ValueError:
        return False
    return locators_overlap(
        source_provider,
        source_locator,
        destination_provider,
        destination_locator,
    )


def _known_catalog_object(
    catalog_access: CatalogAccess, provider: str, namespace: str, object_name: str
) -> bool:
    if not namespace or not object_name:
        return False
    return namespace in {
        name for name, _display in _namespace_entries(catalog_access, provider)
    } and (
        object_name
        in {name for name, _display in _object_entries(catalog_access, provider, namespace)}
    )


def _swap_operand_reason(
    catalog_access: CatalogAccess, provider: str, namespace: str, object_name: str
) -> str:
    """Explain why an endpoint cannot be preserved as a destination after swapping."""

    if provider == "postgres":
        if _known_catalog_object(catalog_access, provider, namespace, object_name):
            return ""
        return "Swap requires both endpoints to be known catalog objects."
    if provider in {"mss", "mcscop"}:
        paths = normalize_foundry_source_paths(object_name)
        if not isinstance(paths, list) or len(paths) != 1:
            return (
                "Swap requires one known Foundry parquet source file; multiple files "
                "and all_supported cannot be reversed."
            )
        if paths[0] != object_name or not paths[0].endswith(".parquet"):
            return "Swap requires one known Foundry parquet source file."
        if not _known_catalog_object(catalog_access, provider, namespace, paths[0]):
            return "Swap requires the Foundry dataset and file to be present in the catalog."
        return ""
    return "Swap requires endpoints from a supported provider."


def _can_swap_direction(
    catalog_access: CatalogAccess,
    connections: dict[str, dict[str, str | bool]],
    *,
    source_provider: str,
    source_schema: str,
    source_table: str,
    target_provider: str,
    target_schema: str,
    target_table: str,
    writer_policy: WriterPolicy,
    destination_table_new: str = "",
) -> SwapEligibility:
    """Return whether swapping preserves both endpoints without fallback selection."""

    if source_provider == "csv":
        return SwapEligibility(
            False, "CSV sources cannot be reversed because uploads are source-only."
        )
    if not target_provider:
        return SwapEligibility(False, "Select a destination before swapping.")
    if target_table == CREATE_TABLE_VALUE:
        return SwapEligibility(False, "Choose an existing destination object before swapping.")
    if source_provider not in {
        catalog.name
        for catalog in _eligible_destinations(
            connections, target_provider, writer_policy=writer_policy
        )
    }:
        return SwapEligibility(
            False, "The selected destination cannot be used as a source in reverse."
        )
    if _selection_overlaps(
        source_provider,
        source_schema,
        source_table,
        target_provider,
        target_schema,
        target_table,
        destination_table_new,
    ):
        return SwapEligibility(
            False, "Choose a destination object different from the source object."
        )

    # Both current endpoints must be known objects. In particular, this keeps
    # an arbitrary Foundry source RID/file from becoming the first catalog
    # destination during a swap.
    source_reason = _swap_operand_reason(
        catalog_access, source_provider, source_schema, source_table
    )
    if source_reason:
        return SwapEligibility(False, source_reason)
    target_reason = _swap_operand_reason(
        catalog_access, target_provider, target_schema, target_table
    )
    if target_reason:
        return SwapEligibility(False, target_reason)
    return SwapEligibility(True)


def _destination_namespace_options(
    catalog_access: CatalogAccess,
    catalog: ProviderCatalog,
    provider: str,
    *,
    preferred_schema: str = "",
):
    options = _schema_options(catalog_access, provider, preferred_schema=preferred_schema)
    if options or not catalog.dataset_creation:
        return options
    return [_option("", "Create a dataset above", selected=True, disabled=True)]


def _destination_object_options(
    catalog_access: CatalogAccess,
    catalog: ProviderCatalog,
    provider: str,
    namespace: str,
    *,
    preferred_table: str = "",
    additional_tables: tuple[str, ...] = (),
):
    if catalog.dataset_creation and not namespace:
        return [_option("", "Create a dataset first", selected=True, disabled=True)]
    return _table_options(
        catalog_access,
        provider,
        namespace,
        allow_create=True,
        additional_tables=additional_tables,
        preferred_table=preferred_table,
        create_label=catalog.objects_label.casefold(),
        destination=True,
    )


def _dataset_creator(
    request: Request,
    catalog: ProviderCatalog | None,
    *,
    created_name: str = "",
    error: str = "",
    needs_dataset: bool = False,
    oob: bool = False,
) -> NodeLike:
    attrs: dict[str, Any] = {
        "id": "pipeline-dataset-creator",
        "class_": "hedron-stack data-mover-dataset-creator",
        "data-hedron-gap": "sm",
    }
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-dataset-creator"
    if catalog is None or not catalog.dataset_creation:
        attrs["hidden"] = True
        return html.div(**attrs)
    feedback: NodeLike | None = None
    if created_name:
        feedback = Alert(
            f"Dataset ready · “{created_name}” is selected. Name its first file below.",
            tone="success",
            appearance="soft",
        )
    elif error:
        feedback = Alert(error, tone="danger")
    elif needs_dataset:
        feedback = Alert(
            "No dataset is available yet. Create one to unlock the file destination.",
            tone="info",
        )
    return html.div(
        feedback if created_name else None,
        Expander(
            "Create another dataset" if created_name else "Create Foundry dataset",
            Stack(
                feedback if not created_name else None,
                html.p(
                    "Provision an empty dataset in an approved Foundry folder and select it for this route.",
                    class_="hedron-text-muted",
                ),
                FormGrid(
                    FormField(
                        name="parent_folder_rid",
                        label="Parent folder RID",
                        help="The token must be allowed to create resources in this folder.",
                        control=html.input(
                            name="parent_folder_rid",
                            maxlength="240",
                            placeholder="ri.compass.main.folder…",
                            autocomplete="off",
                        ),
                    ),
                    FormField(
                        name="dataset_name",
                        label="Dataset name",
                        control=html.input(
                            name="dataset_name",
                            maxlength="160",
                            placeholder="Daily readiness landing",
                            autocomplete="off",
                        ),
                    ),
                    columns=1,
                    gap="sm",
                    density="compact",
                ),
                Button(
                    "Create and select dataset",
                    type="button",
                    variant="secondary",
                    attrs={
                        **hx_attrs(
                            request,
                            path="/pipeline/foundry-datasets",
                            method="post",
                            target="#pipeline-dataset-creator",
                            swap="outerHTML",
                            include="#pipeline-form",
                            disabled_elt="this",
                            indicator=INDICATOR,
                            busy="region",
                        ),
                        "formnovalidate": True,
                        "hx-validate": "false",
                    },
                ),
                gap="sm",
            ),
            open=bool(error or needs_dataset),
        ),
        **attrs,
    )


def _select_fragment(
    request: Request,
    select_id: str,
    options,
    *,
    name: str,
    disabled: bool = False,
    label: str = "",
):
    attrs = {
        "id": select_id,
        "data": {"field-label": label},
        "name": name,
        **({"disabled": True} if disabled else {}),
        **{"hx-swap-oob": f"outerHTML:#{select_id}"},
        **hx_attrs(
            request,
            path="/pipeline/preview",
            method="post",
            target="#pipeline-preview-region",
            swap="none",
            include="#pipeline-form",
            trigger="change",
        ),
    }
    return html.select(*options, **attrs)


def _created_destination_tables(
    pipelines: list[PipelineDefinition], provider: str, schema_name: str
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _pipeline_form_locations(pipeline)[3]
            for pipeline in pipelines
            if pipeline.destination_create
            and pipeline.destination_provider == provider
            and _pipeline_form_locations(pipeline)[2] == schema_name
        )
    )


def _pipeline_form_locations(pipeline: PipelineDefinition) -> tuple[str, str, str, str]:
    """Return catalog namespace/object values, preferring canonical versioned locators."""
    source_namespace = pipeline.source_schema
    source_object = pipeline.source_table
    destination_namespace = pipeline.destination_schema
    destination_object = pipeline.destination_table

    try:
        source = parse_locator(json.loads(pipeline.source_locator_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        source = None
    if isinstance(source, PostgresTableLocator):
        source_namespace = source.schema_name
        source_object = source.table
    elif isinstance(source, FoundryDatasetFilesLocator):
        source_namespace = source.dataset_rid
        if source.file_paths == "all_supported":
            source_object = "all_supported"
        elif source.file_paths:
            source_object = ", ".join(source.file_paths)

    try:
        destination = parse_locator(json.loads(pipeline.destination_locator_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        destination = None
    if isinstance(destination, PostgresTableLocator):
        destination_namespace = destination.schema_name
        destination_object = destination.table
    elif isinstance(destination, FoundryUploadLocator):
        destination_namespace = destination.dataset_rid
        destination_object = destination.file_name

    return source_namespace, source_object, destination_namespace, destination_object


def _capability_surface(
    source_catalog: ProviderCatalog, destination_catalog: ProviderCatalog | None
):
    def facts(catalog: ProviderCatalog | None, *, destination: bool = False):
        route_label = "Destination route" if destination else "Source route"
        route_description = (
            "Write options and post-transfer verification."
            if destination
            else "Pre-transfer inspection and extraction visibility."
        )
        if catalog is None:
            return Surface(
                Stack(
                    PageHeader(
                        "Not selected",
                        eyebrow=route_label,
                        description=route_description,
                        level=4,
                        density="compact",
                    ),
                    DescriptionList(
                        ("Status", Badge("Select a destination", tone="warning")),
                        density="compact",
                    ),
                    gap="sm",
                ),
                appearance="raised",
                padding="sm",
                elevation="sm",
            )
        count_label = (
            "Scanned locally"
            if catalog.name == "csv"
            else "Exact counts"
            if destination and catalog.exact_row_counts
            else "Catalog estimates"
            if catalog.exact_row_counts
            else "Counts unavailable"
        )
        schema_label = (
            "Schema preview" if catalog.schema_inspection else "Schema captured during run"
        )
        return Surface(
            Stack(
                PageHeader(
                    catalog.label,
                    eyebrow=route_label,
                    description=route_description,
                    level=4,
                    density="compact",
                ),
                DescriptionList(
                    (
                        "Schema",
                        Badge(
                            schema_label,
                            tone="success" if catalog.schema_inspection else "info",
                        ),
                    ),
                    (
                        "Rows",
                        Badge(
                            count_label,
                            tone="success" if catalog.exact_row_counts else "warning",
                        ),
                    ),
                    ("Verification", catalog.verification_level.replace("_", " ").title()),
                    (
                        "Write modes",
                        ", ".join(catalog.write_modes) if destination else "Source only",
                    ),
                    density="compact",
                ),
                gap="sm",
            ),
            appearance="raised",
            padding="sm",
            elevation="sm",
        )

    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        stacked_surface(
            PageHeader(
                "Route capabilities",
                eyebrow="What will be known before and after the run",
                description="Provider support varies; unavailable facts are reported instead of estimated silently.",
                level=3,
                density="compact",
            ),
            Grid(
                facts(source_catalog),
                facts(destination_catalog, destination=True),
                columns={"base": 1, "xl": 2},
                gap="md",
            ),
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _run_duration_label(run: object) -> str:
    started = getattr(run, "started_at", None)
    finished = getattr(run, "finished_at", None)
    if started is None or finished is None:
        return "Duration unavailable"
    seconds = max(0.0, (finished - started).total_seconds())
    return f"{seconds:.1f}s" if seconds < 60 else f"{seconds / 60:.1f}m"


def _saved_run_summary(run: object | None) -> str:
    if run is None:
        return "No runs yet"
    verification = _run_manifest(getattr(run, "verification_json", None))
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    if isinstance(before, int) and isinstance(after, int):
        destination = f"destination {before:,} → {after:,} ({after - before:+,})"
    elif isinstance(after, int):
        destination = f"destination {after:,} rows"
    else:
        destination = "destination count unavailable"
    timestamp = getattr(run, "finished_at", None) or getattr(run, "created_at", None)
    timestamp_label = (
        timestamp.strftime("%b %d, %H:%M") if timestamp is not None else "time unavailable"
    )
    return (
        f"Last run {timestamp_label} · "
        f"{getattr(run, 'source_rows', 0):,} extracted · "
        f"{getattr(run, 'loaded_rows', 0):,} loaded · {destination} · "
        f"{_run_duration_label(run)}"
    )


def _catalog_data(
    catalog_access: CatalogAccess,
    catalogs: tuple[ProviderCatalog, ...],
    pipelines: list[PipelineDefinition],
):
    nodes = []
    for catalog in catalogs:
        for namespace, _display in _namespace_entries(catalog_access, catalog.name):
            objects = [
                name for name, _label in _object_entries(catalog_access, catalog.name, namespace)
            ] + list(_created_destination_tables(pipelines, catalog.name, namespace))
            for table_name in dict.fromkeys(objects):
                nodes.append(
                    html.span(
                        data={
                            "catalog-provider": catalog.name,
                            "catalog-schema": namespace,
                            "catalog-table": table_name,
                            "records": "—",
                            "size": "—",
                            "megabytes": "0",
                        }
                    )
                )
    return html.div(
        *nodes,
        id="pipeline-catalog-data",
        hidden=True,
        aria={"hidden": "true"},
    )


def _format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _run_locator_label(locator: object) -> str:
    if isinstance(locator, PostgresTableLocator):
        return f"{locator.schema_name}.{locator.table}"
    if isinstance(locator, FoundryDatasetFilesLocator):
        if isinstance(locator.file_paths, list) and locator.file_paths:
            first = locator.file_paths[0]
            return (
                first
                if len(locator.file_paths) == 1
                else f"{first} (+{len(locator.file_paths) - 1} more)"
            )
        return locator.dataset_rid
    if isinstance(locator, FoundryUploadLocator):
        return locator.file_name
    return "Uploaded CSV"


def _csv_columns_json(inspection: CsvInspection) -> str:
    return json.dumps(
        [
            {
                "name": column.name,
                "type": column.inferred_type,
                "populated": column.populated,
                "nulls": column.nulls,
                "example": column.example,
            }
            for column in inspection.columns
        ],
        separators=(",", ":"),
    )


def _remote_object_preview(
    catalog_access: CatalogAccess, provider: str, namespace: str, object_name: str
):
    if not provider or not namespace or not object_name or object_name == CREATE_TABLE_VALUE:
        return None
    try:
        page = catalog_access.list_objects(provider, namespace)
        return next((item for item in page.items if item.name == object_name), None)
    except Exception:
        return None


def _foundry_source_preview(
    catalog_access: CatalogAccess, provider: str, namespace: str, object_name: str
):
    paths = normalize_foundry_source_paths(object_name)
    try:
        page = catalog_access.list_objects(provider, namespace)
    except Exception:
        return None
    if paths == "all_supported":
        selected = list(page.items)
        requested_count = len(selected)
    else:
        requested_count = len(paths)
        requested = set(paths)
        selected = [item for item in page.items if item.name in requested]
    if not selected and paths == "all_supported":
        return None
    complete_catalog_match = paths == "all_supported" or len(selected) == requested_count
    sizes = [item.size_bytes for item in selected]
    rows = [item.estimated_rows for item in selected]
    known_sizes = [value for value in sizes if isinstance(value, int)]
    known_rows = [value for value in rows if isinstance(value, int)]
    return {
        "rows": sum(known_rows)
        if complete_catalog_match and len(known_rows) == len(rows)
        else None,
        "size_bytes": sum(known_sizes)
        if complete_catalog_match and len(known_sizes) == len(sizes)
        else None,
        "columns": [],
        "primary_key": [],
        "status": f"Catalog preview · {requested_count} file(s) selected",
        "schema_provenance": "provider_unavailable",
        "row_provenance": "estimated"
        if complete_catalog_match and len(known_rows) == len(rows)
        else "unavailable",
        "size_provenance": "catalog"
        if complete_catalog_match and len(known_sizes) == len(sizes)
        else "unavailable",
        "capabilities": {
            "schema_inspection": False,
            "exact_row_counts": False,
            "verification_level": capabilities_for(provider).verification_level,
            "limitations": capabilities_for(provider).limitations
            or ("Manually entered file paths are validated during the run.",),
        },
    }


def _route_schema_preview(
    catalog_access: CatalogAccess,
    provider: str,
    namespace: str,
    object_name: str,
    *,
    destination: bool = False,
    creating: bool = False,
    csv_inspection: CsvInspection | None = None,
):
    if csv_inspection is not None:
        return {
            "rows": csv_inspection.row_count,
            "size_bytes": csv_inspection.size_bytes,
            "columns": [
                {
                    "name": column.name,
                    "data_type": column.inferred_type,
                    "nullable": column.nulls > 0,
                    "example": column.example,
                }
                for column in csv_inspection.columns
            ],
            "primary_key": [],
            "status": "Scanned locally",
            "schema_provenance": "catalog",
            "row_provenance": "exact",
            "size_provenance": "catalog",
            "capabilities": {
                "schema_inspection": True,
                "exact_row_counts": True,
                "verification_level": "local_manifest",
                "limitations": (),
            },
        }
    if creating:
        return {
            "rows": None,
            "size_bytes": None,
            "columns": [],
            "primary_key": [],
            "status": "Created by run",
            "schema_provenance": "unavailable",
            "row_provenance": "unavailable",
            "size_provenance": "unavailable",
            "capabilities": {
                "schema_inspection": True,
                "exact_row_counts": False,
                "verification_level": "local_manifest",
                "limitations": ("The destination schema is created during the run.",),
            },
        }
    if not destination and provider in {"mss", "mcscop"}:
        return _foundry_source_preview(catalog_access, provider, namespace, object_name)
    remote = _remote_object_preview(catalog_access, provider, namespace, object_name)
    if remote is None:
        return None
    capabilities = capabilities_for(provider)
    columns: list[dict[str, object]] = []
    primary_key: list[str] = []
    estimated_rows = remote.estimated_rows
    schema_provenance = (
        "provider_unavailable" if not capabilities.schema_inspection else "unavailable"
    )
    row_provenance = "estimated" if estimated_rows is not None else "unavailable"
    try:
        inspected = catalog_access.inspect_object(provider, remote.locator)
        estimated_rows = inspected.estimated_rows or estimated_rows
        primary_key = list(inspected.primary_key)
        columns = [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "example": column.example,
            }
            for column in inspected.columns
        ]
        if columns:
            schema_provenance = "catalog"
        if inspected.estimated_rows is not None:
            row_provenance = "estimated"
    except Exception:
        pass
    if destination and capabilities.exact_row_counts:
        try:
            counted = catalog_access.count_rows(provider, remote.locator)
            estimated_rows = counted if counted is not None else estimated_rows
            if counted is not None:
                row_provenance = "exact"
        except Exception:
            pass
    return {
        "rows": estimated_rows,
        "size_bytes": remote.size_bytes,
        "columns": columns,
        "primary_key": primary_key,
        "status": "Catalog preview",
        "schema_provenance": schema_provenance,
        "row_provenance": row_provenance,
        "size_provenance": "catalog" if remote.size_bytes is not None else "unavailable",
        "capabilities": {
            "schema_inspection": capabilities.schema_inspection,
            "exact_row_counts": capabilities.exact_row_counts,
            "verification_level": capabilities.verification_level,
            "limitations": capabilities.limitations,
        },
    }


def _schema_columns_table(columns: list[dict[str, object]], label: str):
    if not columns:
        return StateView(
            "Schema details will appear during validation",
            kind="empty",
            description="The provider does not expose column metadata before a run.",
        )
    return ScrollRegion(
        Table(
            rows=[
                [
                    html.strong(str(column.get("name") or "—")),
                    Badge(str(column.get("data_type") or "unknown"), tone="info", size="sm"),
                    "Nullable" if column.get("nullable") else "Required",
                    html.code(str(column.get("example") or "—")),
                ]
                for column in columns
            ],
            columns=[
                TableColumn(header="Column"),
                TableColumn(header="Type"),
                TableColumn(header="Nullability"),
                TableColumn(header="Example", size="wide"),
            ],
            density="compact",
            sticky_header=True,
            zebra=True,
        ),
        axis="block",
        size="sm",
        label=label,
    )


def _schema_preview_surface(
    title: str, preview: dict[str, Any] | None, *, destination: bool = False
):
    if preview is None:
        return Surface(
            PageHeader(title, eyebrow="Schema preview", level=3, density="compact"),
            StateView(
                "Select an object to preview it",
                kind="empty",
                description="Choose a source or destination object to see available schema facts.",
            ),
            appearance="plain",
            padding="sm",
            elevation="none",
        )
    rows = preview.get("rows")
    row_value = (
        "New table"
        if rows is None and preview.get("status") == "Created by run"
        else (f"{rows:,} rows" if isinstance(rows, int) else "Unavailable")
    )
    size = preview.get("size_bytes")
    size_text = _format_file_size(int(size)) if isinstance(size, int) else "Size unavailable"
    schema_provenance = str(preview.get("schema_provenance") or "unavailable")
    row_provenance = str(preview.get("row_provenance") or "unavailable")
    preview_complete = bool(preview.get("columns")) and row_provenance in {"exact", "estimated"}
    capabilities_value = preview.get("capabilities")
    capabilities: dict[str, Any] = (
        capabilities_value if isinstance(capabilities_value, dict) else {}
    )
    limitations = tuple(str(item) for item in capabilities.get("limitations") or ())
    status_label = "Ready to compare" if preview_complete else "Limited preview"
    status_tone = "success" if preview_complete else "warning"
    limitation = limitations[0] if limitations else None
    return stacked_surface(
        PageHeader(
            title,
            eyebrow="Destination schema" if destination else "Source schema",
            description=(
                f"{preview.get('status') or 'Schema preview'} · {size_text} · "
                f"Rows: {provenance_label(row_provenance)} · "
                f"Schema: {provenance_label(schema_provenance)}"
            ),
            level=3,
            density="compact",
            meta=Badge(status_label, tone=status_tone),
        ),
        Grid(
            Metric("Rows before run" if destination else "Source rows", row_value),
            Metric("Columns", f"{len(preview.get('columns') or []):,}"),
            Metric(
                "Primary key",
                ", ".join(str(item) for item in preview.get("primary_key") or []) or "None",
            ),
            columns=3,
            gap="sm",
        ),
        StateView(
            "Provider limitation" if limitation else "Preview facts",
            kind="empty",
            description=limitation
            or "These facts will be captured again by the worker during the run.",
        )
        if limitation and not preview_complete
        else None,
        _schema_columns_table(preview.get("columns") or [], f"Columns in {title}"),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _pipeline_schema_preview_panel(
    *,
    catalog_access: CatalogAccess,
    source_provider: str,
    source_schema: str,
    source_object: str,
    destination_provider: str,
    destination_schema: str,
    destination_object: str,
    destination_create: bool,
    csv_inspection: CsvInspection | None,
    include_id: bool = True,
):
    source = _route_schema_preview(
        catalog_access,
        source_provider,
        source_schema,
        source_object,
        csv_inspection=csv_inspection,
    )
    destination = _route_schema_preview(
        catalog_access,
        destination_provider,
        destination_schema,
        destination_object,
        destination=True,
        creating=destination_create,
    )
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        stacked_surface(
            PageHeader(
                "Schema & row counts",
                eyebrow="Pre-run review",
                description="Confirm the columns and current row counts before starting this route.",
                level=3,
                density="compact",
            ),
            Grid(
                _schema_preview_surface("Source", source),
                _schema_preview_surface("Destination", destination, destination=True),
                columns={"base": 1, "xl": 2},
                gap="sm",
            ),
            id="pipeline-schema-preview" if include_id else None,
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _csv_inspection(
    upload: PipelineUpload | None = None,
    inspection: CsvInspection | None = None,
    *,
    error: str = "",
    include_id: bool = True,
):
    if error:
        content = StateView(
            "CSV scan failed",
            kind="error",
            description=error,
        )
    elif upload is not None and inspection is not None:
        content = Stack(
            PageHeader(
                inspection.filename,
                eyebrow="CSV source",
                description="Schema inspection completed and this file is ready to map.",
                level=3,
                density="compact",
                meta=Badge("Schema detected", tone="success"),
            ),
            Grid(
                Metric("Rows", f"{inspection.row_count:,}"),
                Metric("Columns", f"{len(inspection.columns)}"),
                Metric("File size", _format_file_size(inspection.size_bytes)),
                columns=3,
                gap="sm",
            ),
            ScrollRegion(
                apply_data_recipe(
                    Table(
                        rows=[
                            [
                                html.strong(column.name),
                                Badge(column.inferred_type, tone="info", size="sm"),
                                (
                                    f"{column.populated / inspection.row_count:.0%}"
                                    if inspection.row_count
                                    else "—"
                                ),
                                html.code(column.example or "—"),
                            ]
                            for column in inspection.columns
                        ],
                        columns=[
                            TableColumn(header="Column"),
                            TableColumn(header="Inferred type"),
                            TableColumn(
                                header="Complete",
                                align="end",
                                numeric=True,
                                size="narrow",
                            ),
                            TableColumn(header="Example", size="wide"),
                        ],
                        density="compact",
                        sticky_header=True,
                        zebra=True,
                    )
                ),
                axis="block",
                size="md",
                label=f"Detected columns in {inspection.filename}",
            ),
            gap="md",
        )
    else:
        content = StateView(
            "No CSV scanned yet",
            kind="empty",
            description="Choose a UTF-8 CSV up to 5 MB to detect its columns and data types.",
        )

    return html.div(
        html.input(
            type="hidden",
            name="source_upload_id",
            value=upload.id if upload is not None else "",
            id="pipeline-source-upload-id",
        ),
        content,
        id="pipeline-csv-inspection" if include_id else None,
        data=(
            {
                "csv-ready": "true",
                "csv-filename": inspection.filename,
                "csv-rows": str(inspection.row_count),
                "csv-columns": _csv_columns_json(inspection),
                "csv-size": _format_file_size(inspection.size_bytes),
                "csv-megabytes": f"{inspection.size_bytes / (1024 * 1024):.4f}",
            }
            if upload is not None and inspection is not None
            else {"csv-ready": "false"}
        ),
    )


def _csv_upload_control(request: Request, *, oob: bool = False) -> NodeLike:
    attrs = hx_attrs(
        request,
        path="/pipeline/csv/inspect",
        target="#pipeline-csv-inspection",
        trigger="change",
        include="#pipeline-form [name='csrf_token']",
        indicator="#pipeline-csv-upload-state",
    )
    attrs["hx-encoding"] = "multipart/form-data"
    if oob:
        attrs["hx-swap-oob"] = "outerHTML:#pipeline-csv-file"
    return AttrHost(
        FileUpload(
            name="csv_file",
            accept=".csv,text/csv",
            maximum_size=MAX_CSV_UPLOAD_BYTES,
            label="Choose CSV file",
            hint="UTF-8 CSV · scanned locally before use",
            status="Select a file to inspect its schema.",
            appearance="soft",
            density="comfortable",
        ),
        id="pipeline-csv-file",
        attrs={str(key): str(value) for key, value in attrs.items()},
    )


def _provider_node(
    *,
    kind: str,
    catalog: ProviderCatalog | None,
    detail: str,
    configured: bool,
    runtime: str = "",
    include_id: bool = True,
):
    if catalog is None:
        mark = "—"
        label = "No connection"
        technology = "Setup required"
        region = "Connections"
        connection_label = "Configure a connection"
    else:
        mark = catalog.mark
        label = catalog.label
        technology = catalog.technology
        region = catalog.region
        connection_label = (
            ("Scanned CSV" if configured else "Upload required")
            if catalog.name == "csv"
            else "Stored credentials"
            if configured
            else "Setup required"
        )
    return ConnectorNode(
        label,
        leading=Avatar(
            mark or label,
            size="md",
            appearance="soft",
            shape="rounded",
        ),
        state="ready" if configured else "blocked",
        kind="source" if kind == "source" else "target",
        detail=f"{kind.title()} · {connection_label}",
        runtime=runtime or technology,
        children=DescriptionList(
            (
                "Object",
                html.span(detail, id=f"pipeline-{kind}-detail", title=detail),
            ),
            (
                "Region",
                html.span(
                    region or "Not specified",
                    id=f"pipeline-{kind}-region",
                    title=region or "Not specified",
                ),
            ),
            density="compact",
            layout="inline",
        ),
        id=f"pipeline-{kind}-node" if include_id else None,
    )


def _new_destination_basename(pipeline: PipelineDefinition) -> str:
    name = pipeline.destination_table
    if pipeline.destination_provider in {"mss", "mcscop"}:
        name = name.removesuffix(".snappy.parquet").removesuffix(".parquet")
    return name


def _saved_pipeline_data(pipeline: PipelineDefinition, *, run: bool = False) -> dict[str, str]:
    source_namespace, source_object, destination_namespace, destination_object = (
        _pipeline_form_locations(pipeline)
    )
    upload_fields: dict[str, str] = {}
    if pipeline.source_provider == "csv" and pipeline.source_upload is not None:
        inspection = inspection_from_upload(pipeline.source_upload)
        upload_fields = {
            "source_upload_id": pipeline.source_upload.id,
            "source_upload_name": inspection.filename,
            "source_upload_rows": str(inspection.row_count),
            "source_upload_size": _format_file_size(inspection.size_bytes),
            "source_upload_megabytes": f"{inspection.size_bytes / (1024 * 1024):.4f}",
            "source_upload_columns": _csv_columns_json(inspection),
        }
    return saved_pipeline_form_data(
        SavedPipelineFields(
            pipeline_id=pipeline.id,
            name=pipeline.name,
            source_provider=pipeline.source_provider,
            source_namespace=source_namespace,
            source_object=source_object,
            destination_provider=pipeline.destination_provider,
            destination_namespace=destination_namespace,
            destination_object=destination_object,
            destination_create=pipeline.destination_create,
            destination_new_name=_new_destination_basename(pipeline),
            write_mode=pipeline.write_mode,
            **upload_fields,
        ),
        run=run,
    )


def _saved_pipeline_cards(
    request: Request,
    pipelines: list[PipelineDefinition],
    connections: dict[str, dict[str, str | bool]],
    *,
    csrf_token: str,
    writer_policy: WriterPolicy,
    latest_runs: dict[str, object] | None = None,
):
    latest_runs = latest_runs or {}
    if not pipelines:
        return StateView(
            "No saved pipelines yet",
            kind="empty",
            description="Name this route and save it to make it reusable.",
        )
    cards = []
    for pipeline in pipelines:
        source_namespace, source_object, destination_namespace, destination_object = (
            _pipeline_form_locations(pipeline)
        )
        source_configured = pipeline.source_provider == "csv" or _connection_configured(
            connections[pipeline.source_provider]
        )
        target_configured = _connection_configured(connections[pipeline.destination_provider])
        connections_configured = source_configured and target_configured
        source_runnable = pipeline.source_provider == "csv" or _connection_runnable(
            connections[pipeline.source_provider]
        )
        target_runnable = _connection_runnable(
            connections[pipeline.destination_provider]
        ) and writer_policy(pipeline.destination_provider)
        runnable = (
            connections_configured
            and source_runnable
            and target_runnable
            and route_allowed(pipeline.source_provider, pipeline.destination_provider)
        )
        latest = latest_runs.get(pipeline.id)
        state = getattr(latest, "status", "Saved" if runnable else "Connection required")
        if latest is None:
            state = "Saved" if runnable else "Connection required"
        state_key = str(state).lower()
        state_tone = "info"
        if not runnable:
            state_tone = "warning"
        elif state_key == "succeeded":
            state_tone = "success"
        elif state_key in {"failed", "failed_needs_reconciliation"}:
            state_tone = "danger"
        elif state_key == "cancelled":
            state_tone = "warning"
        cards.append(
            ResourceRow(
                pipeline.name,
                description=(
                    f"{source_object if pipeline.source_provider == 'csv' else f'{source_namespace}.{source_object}'} → "
                    f"{destination_namespace}.{destination_object} · Updated "
                    f"{pipeline.updated_at.strftime('%b %d, %H:%M')} · {_saved_run_summary(latest)}"
                ),
                meta=Inline(
                    Badge(str(state).replace("_", " ").title(), tone=state_tone),
                    html.small(
                        f"{_provider_label(pipeline.source_provider)} → "
                        f"{_provider_label(pipeline.destination_provider)}"
                    ),
                    gap="sm",
                ),
                actions=ActionGroup(
                    Button(
                        "Load",
                        type="button",
                        variant="secondary",
                        size="sm",
                        attrs={
                            **{
                                f"data-{key}": value
                                for key, value in _saved_pipeline_data(pipeline).items()
                            },
                            **hx_attrs(
                                request,
                                method="get",
                                path=f"/pipeline?pipeline_id={pipeline.id}",
                                target="#main-panel",
                                swap="outerHTML",
                                push_url=True,
                                select="#main-panel",
                            ),
                        },
                        disabled=not connections_configured,
                    ),
                    html.form(
                        html.input(type="hidden", name="pipeline_id", value=pipeline.id),
                        html.input(type="hidden", name="csrf_token", value=csrf_token),
                        Button(
                            "Run now",
                            type="submit",
                            variant="secondary",
                            size="sm",
                            attrs={
                                **{
                                    f"data-{key}": value
                                    for key, value in _saved_pipeline_data(
                                        pipeline, run=True
                                    ).items()
                                },
                                **hx_attrs(
                                    request,
                                    path="/pipeline/runs",
                                    method="post",
                                    target="#pipeline-run-monitor",
                                    swap="outerHTML",
                                    busy="region",
                                ),
                            },
                            disabled=not runnable,
                        ),
                        action=form_action(request, "/pipeline/runs"),
                        method="post",
                        id=f"pipeline-run-form-{pipeline.id}",
                    ),
                    gap="xs",
                    collapse="never",
                ),
            )
        )
    return ResourceList(
        *cards,
        label="Saved pipelines",
        density="comfortable",
        id="pipeline-run-history",
    )


def _pipeline_preview_fragment(
    *,
    request: Request,
    catalog_access: CatalogAccess,
    source_provider: str,
    source_schema: str,
    source_table: str,
    target_provider: str,
    target_schema: str,
    target_table: str,
    destination_table_new: str = "",
    source_upload_id: str = "",
    write_mode: str = "",
    conflict_columns: str = "",
    csv_inspection: CsvInspection | None = None,
    csv_upload: PipelineUpload | None = None,
    connections: dict[str, dict[str, str | bool]],
    writer_policy: WriterPolicy,
):
    if source_provider != "csv":
        source_schema, source_table = _normalized_selection(
            catalog_access,
            source_provider,
            source_schema,
            source_table,
            freeform_namespace=source_provider in {"mss", "mcscop"},
        )
    if target_provider:
        target_schema, target_table = _normalized_selection(
            catalog_access,
            target_provider,
            target_schema,
            target_table,
            preserve_create=True,
            destination=True,
        )
    source_catalog = (
        CSV_SOURCE_CATALOG
        if source_provider == "csv"
        else require_catalog_provider(source_provider)
    )
    target_catalog = None if not target_provider else require_catalog_provider(target_provider)
    csv_ready = source_provider == "csv" and csv_upload is not None and csv_inspection is not None
    source_runtime_ready = (source_provider == "csv" and csv_ready) or (
        source_provider != "csv" and _connection_runnable(connections[source_provider])
    )
    target_runtime_ready = target_catalog is not None and _connection_runnable(
        connections[target_provider]
    )
    availability_message = (
        _destination_unavailable_message(connections, source_provider, writer_policy=writer_policy)
        if target_catalog is None
        else "Upload and scan a CSV source."
        if source_provider == "csv" and not csv_ready
        else "CSV source is ready. Configure destination settings."
        if source_provider == "csv"
        else f"Validate the {source_catalog.label} connection before running."
        if not source_runtime_ready
        else f"Validate the {target_catalog.label} connection before running."
        if not target_runtime_ready
        else "Source and destination connections are ready."
    )
    source_object_name = source_table
    table_name = (
        _committed_new_table_name(destination_table_new) or destination_table_new or target_table
    )
    if target_table == CREATE_TABLE_VALUE:
        table_name = (
            _committed_new_table_name(destination_table_new) or destination_table_new or "new_table"
        )
    route_overlap = _selection_overlaps(
        source_provider,
        source_schema,
        source_table,
        target_provider,
        target_schema,
        table_name,
        destination_table_new,
    )
    swap_eligibility = _can_swap_direction(
        catalog_access,
        connections,
        source_provider=source_provider,
        source_schema=source_schema,
        source_table=source_table,
        target_provider=target_provider,
        target_schema=target_schema,
        target_table=target_table,
        writer_policy=writer_policy,
        destination_table_new=destination_table_new,
    )
    if route_overlap:
        availability_message = "Choose a destination object different from the source object."
    field_count = len(csv_inspection.columns) if csv_ready and csv_inspection is not None else 0
    source_table_options = (
        [
            _option(
                "",
                csv_inspection.filename
                if csv_ready and csv_inspection is not None
                else "Upload required",
                selected=True,
                disabled=True,
            )
        ]
        if source_provider == "csv"
        else None
    )
    target_schema_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _destination_namespace_options(
            catalog_access,
            target_catalog,
            target_provider,
            preferred_schema=target_schema,
        )
    )
    target_table_options = (
        [_option("", "No connection available", selected=True, disabled=True)]
        if target_catalog is None
        else _destination_object_options(
            catalog_access,
            target_catalog,
            target_provider,
            target_schema,
            preferred_table=table_name
            if target_table != CREATE_TABLE_VALUE
            else CREATE_TABLE_VALUE,
            additional_tables=_created_destination_tables([], target_provider, target_schema),
        )
    )
    upsert_keys = _upsert_keys(
        catalog_access,
        target_provider,
        target_schema,
        table_name if target_table != CREATE_TABLE_VALUE else CREATE_TABLE_VALUE,
    )
    return html.div(
        html.span(
            f"{source_catalog.label} to "
            f"{target_catalog.label if target_catalog else 'destination not selected'}",
            id="pipeline-route-description",
            **{"hx-swap-oob": "outerHTML:#pipeline-route-description"},
        ),
        html.div(
            _capability_surface(source_catalog, target_catalog),
            id="pipeline-capabilities",
            **{"hx-swap-oob": "outerHTML:#pipeline-capabilities"},
        ),
        _source_provider_select(
            request,
            connections,
            selected=source_provider,
            target_provider=target_provider,
            writer_policy=writer_policy,
            oob=True,
        ),
        html.span(
            Status(
                "Ready to transfer"
                if source_runtime_ready and target_runtime_ready and not route_overlap
                else "Setup required",
                tone="success"
                if source_runtime_ready and target_runtime_ready and not route_overlap
                else "warning",
                live=False,
                variant="compact",
            ),
            id="pipeline-route-readiness",
            **{"hx-swap-oob": "outerHTML:#pipeline-route-readiness"},
        ),
        _csv_upload_control(request, oob=True) if source_provider != "csv" else None,
        _destination_provider_select(
            request,
            connections,
            source_provider=source_provider,
            selected=target_provider,
            writer_policy=writer_policy,
            oob=True,
        ),
        (
            _source_namespace_control(
                request,
                catalog_access,
                source_provider,
                preferred_schema=source_schema,
                oob=True,
            )
            if source_provider != "csv"
            else _select_fragment(
                request,
                "pipeline-source-schema-select",
                [
                    _option(
                        "uploaded",
                        "Scanned CSV" if csv_ready else "Upload a CSV to inspect its schema",
                        selected=True,
                        disabled=True,
                    )
                ],
                name="source_schema",
                label="Upload",
            )
        ),
        (
            _source_object_control(
                request,
                catalog_access,
                source_provider,
                source_schema,
                preferred_object=source_table,
                oob=True,
            )
            if source_provider != "csv"
            else _select_fragment(
                request,
                "pipeline-source-table-select",
                source_table_options or [],
                name="source_table",
                label=source_catalog.objects_label,
            )
        ),
        *_source_catalog_suggestions(
            catalog_access,
            source_provider,
            source_schema,
            oob=True,
        ),
        _select_fragment(
            request,
            "pipeline-target-schema-select",
            target_schema_options,
            name="destination_schema",
            label=target_catalog.namespaces_label if target_catalog else "Schema",
            disabled=target_catalog is None,
        ),
        _select_fragment(
            request,
            "pipeline-target-table-select",
            target_table_options,
            name="destination_table",
            label=target_catalog.objects_label if target_catalog else "Table",
            disabled=target_catalog is None,
        ),
        _write_mode_select(
            request,
            target_catalog,
            selected=write_mode,
            upsert_available=bool(upsert_keys),
            oob=True,
        ),
        _upsert_key_select(upsert_keys, selected=conflict_columns, oob=True),
        _dataset_creator(
            request,
            target_catalog,
            needs_dataset=bool(
                target_catalog and target_catalog.dataset_creation and not target_schema
            ),
            oob=True,
        ),
        _swap_direction_button(
            request,
            can_swap=swap_eligibility.allowed,
            reason=swap_eligibility.reason,
            oob=True,
        ),
        html.p(
            source_schema + "." + source_object_name
            if source_provider != "csv"
            else (
                csv_inspection.filename
                if csv_ready and csv_inspection is not None
                else "Choose a CSV file"
            ),
            id="pipeline-source-detail",
            **{"hx-swap-oob": "outerHTML:#pipeline-source-detail"},
        ),
        html.p(
            target_schema + "." + table_name if target_provider else "Configure a connection",
            id="pipeline-target-detail",
            **{"hx-swap-oob": "outerHTML:#pipeline-target-detail"},
        ),
        html.span(
            f"{field_count} fields" if field_count else "See schema preview",
            id="pipeline-field-map-label",
            class_="hedron-process-flow-description",
            **{"hx-swap-oob": "outerHTML:#pipeline-field-map-label"},
        ),
        html.div(
            Alert(
                availability_message,
                tone=(
                    "success"
                    if source_runtime_ready and target_runtime_ready and not route_overlap
                    else "warning"
                ),
            ),
            id="pipeline-availability-note",
            **{"hx-swap-oob": "outerHTML:#pipeline-availability-note"},
        ),
    )


def _pipeline_body(
    request: Request,
    catalog_access: CatalogAccess,
    connections: dict[str, dict[str, str | bool]],
    pipelines: list[PipelineDefinition],
    *,
    loaded_pipeline: PipelineDefinition | None = None,
    loaded_source_upload: PipelineUpload | None = None,
    loaded_source_inspection: CsvInspection | None = None,
    csrf_token: str,
    notice: str = "",
    latest_runs: dict[str, object] | None = None,
    run_monitor: NodeLike = None,
    demo_mode: bool = True,
    writer_policy: WriterPolicy,
):
    source_catalogs = _configured_catalogs(connections, role="source", writer_policy=writer_policy)
    destination_catalogs = _configured_catalogs(
        connections, role="destination", writer_policy=writer_policy
    )
    catalogs = tuple(dict.fromkeys((*source_catalogs, *destination_catalogs)))
    ready_count = sum(1 for details in connections.values() if _connection_runnable(details))
    compatible_pairs = [
        (source, target)
        for source in source_catalogs
        for target in destination_catalogs
        if route_allowed(source.name, target.name)
    ]
    # Prefer a cross-system route for a fresh workspace so the default remains
    # useful when a provider also supports copying within itself. Same-system
    # routes remain available in the selectors.
    source_catalog, target_catalog = next(
        ((source, target) for source, target in compatible_pairs if source.name != target.name),
        compatible_pairs[0]
        if compatible_pairs
        else (
            (CSV_SOURCE_CATALOG, destination_catalogs[0])
            if destination_catalogs
            else (source_catalogs[0] if source_catalogs else CSV_SOURCE_CATALOG, None)
        ),
    )
    source_provider = source_catalog.name
    target_provider = target_catalog.name if target_catalog is not None else ""
    configured_providers = {catalog.name for catalog in catalogs}

    if loaded_pipeline is not None:
        if (
            loaded_pipeline.source_provider in configured_providers
            or loaded_pipeline.source_provider == "csv"
        ):
            source_provider = loaded_pipeline.source_provider
        if loaded_pipeline.destination_provider in configured_providers:
            target_provider = loaded_pipeline.destination_provider
    destinations = _eligible_destinations(connections, source_provider, writer_policy=writer_policy)
    if target_provider not in {catalog.name for catalog in destinations}:
        target_provider = destinations[0].name if destinations else ""
    source_catalog = (
        CSV_SOURCE_CATALOG
        if source_provider == "csv"
        else require_catalog_provider(source_provider)
    )
    target_catalog = require_catalog_provider(target_provider) if target_provider else None
    field_count = (
        len(loaded_source_inspection.columns)
        if source_provider == "csv" and loaded_source_inspection is not None
        else 0
    )
    source_schema_name = (
        _first_namespace(catalog_access, source_provider)
        if source_provider != "csv"
        else "uploaded"
    )
    target_schema_name = (
        _first_namespace(catalog_access, target_provider) if target_provider else ""
    )

    loaded_locations = (
        _pipeline_form_locations(loaded_pipeline) if loaded_pipeline is not None else None
    )
    if loaded_locations is not None:
        source_schema_name, source_table_display, target_schema_name, target_table_name = (
            loaded_locations
        )
    else:
        source_table_display = ""
        target_table_name = ""
    pipeline_id = loaded_pipeline.id if loaded_pipeline is not None else ""
    pipeline_name = loaded_pipeline.name if loaded_pipeline is not None else "Daily readiness sync"
    requested_write_mode = loaded_pipeline.write_mode if loaded_pipeline is not None else ""
    new_target_table_name = (
        _new_destination_basename(loaded_pipeline)
        if loaded_pipeline is not None and loaded_pipeline.destination_create
        else ""
    )
    if loaded_pipeline is not None and loaded_pipeline.destination_create:
        target_table_name = CREATE_TABLE_VALUE
    if loaded_pipeline is not None and loaded_pipeline.source_provider == "csv":
        source_schema_name = "uploaded"
        source_object_name = loaded_pipeline.source_table
        source_table_display = loaded_pipeline.source_table
        if loaded_pipeline.source_upload is not None and loaded_source_upload is None:
            loaded_source_upload = loaded_pipeline.source_upload
        if loaded_source_upload is not None and loaded_source_inspection is None:
            try:
                loaded_source_inspection = inspection_from_upload(loaded_source_upload)
            except ValueError:
                loaded_source_upload = None
                loaded_source_inspection = None

    if source_provider != "csv":
        source_schema_name, source_table_display = _normalized_selection(
            catalog_access,
            source_provider,
            source_schema_name,
            source_table_display,
            freeform_namespace=source_provider in {"mss", "mcscop"},
        )
    if target_provider:
        target_schema_name, target_table_name = _normalized_selection(
            catalog_access,
            target_provider,
            target_schema_name,
            target_table_name,
            preserve_create=target_table_name == CREATE_TABLE_VALUE,
            destination=True,
        )
    source_object_name = source_table_display
    target_object_name = (
        target_table_name or _first_object(catalog_access, target_provider, target_schema_name)
        if target_provider
        else ""
    )
    route_overlap = _selection_overlaps(
        source_provider,
        source_schema_name,
        source_object_name,
        target_provider,
        target_schema_name,
        target_table_name,
        new_target_table_name,
    )
    swap_eligibility = _can_swap_direction(
        catalog_access,
        connections,
        source_provider=source_provider,
        source_schema=source_schema_name,
        source_table=source_object_name,
        target_provider=target_provider,
        target_schema=target_schema_name,
        target_table=target_table_name,
        writer_policy=writer_policy,
        destination_table_new=new_target_table_name,
    )
    upsert_keys = _upsert_keys(
        catalog_access,
        target_provider,
        target_schema_name,
        target_object_name if target_table_name != CREATE_TABLE_VALUE else CREATE_TABLE_VALUE,
    )
    saved_conflict_columns = ""
    if loaded_pipeline is not None:
        try:
            loaded_policy = parse_write_policy(json.loads(loaded_pipeline.write_policy_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded_policy = None
        if isinstance(loaded_policy, PostgresUpsertPolicy):
            saved_conflict_columns = ",".join(loaded_policy.conflict_columns)
    write_mode = _resolved_write_mode(
        target_catalog, requested_write_mode, upsert_available=bool(upsert_keys)
    )
    csv_source_ready = source_provider == "csv" and loaded_source_upload is not None
    source_runtime_ready = (
        source_provider != "csv" and _connection_runnable(connections[source_provider])
        if source_provider != "csv"
        else csv_source_ready
    )
    target_runtime_ready = target_catalog is not None and _connection_runnable(
        connections[target_provider]
    )
    latest_loaded_run = (latest_runs or {}).get(loaded_pipeline.id) if loaded_pipeline else None
    latest_facts = _run_manifest(getattr(latest_loaded_run, "verification_json", None))
    reconciliation_blocked = bool(
        latest_loaded_run is not None
        and (
            getattr(latest_loaded_run, "reconciliation_required", False)
            or latest_facts.get("reconciliation_required")
        )
        and not (
            getattr(latest_loaded_run, "reconciliation_reviewed_at", None)
            or latest_facts.get("reconciliation_reviewed_at")
        )
    )
    initial_run_ready = (
        source_runtime_ready
        and target_runtime_ready
        and not route_overlap
        and not reconciliation_blocked
    )
    if route_overlap:
        availability_message = "Choose a destination object different from the source object."
    elif target_catalog is None:
        availability_message = _destination_unavailable_message(
            connections, source_provider, writer_policy=writer_policy
        )
    elif source_provider == "csv":
        availability_message = (
            "Upload and scan a CSV source."
            if not csv_source_ready
            else "CSV source is ready. Configure destination settings."
        )
    elif not source_runtime_ready:
        availability_message = f"Validate the {source_catalog.label} connection before running."
    elif not target_runtime_ready:
        availability_message = f"Validate the {target_catalog.label} connection before running."
    elif reconciliation_blocked:
        availability_message = (
            "Review the previous destination state before starting another transfer."
        )
    else:
        availability_message = "Source and destination connections are ready."
    if initial_run_ready and not pipeline_id:
        availability_message = (
            "Source and destination are ready. Save this pipeline to enable runs."
        )
    connection_summary = ActionGroup(
        Badge(
            f"{ready_count}/{len(connections)} connections ready",
            tone="success" if connections and ready_count == len(connections) else "info",
        ),
        Badge(
            "Demo mode" if demo_mode else "Real transfers",
            tone="warning" if demo_mode else "success",
        ),
        align="end",
        gap="sm",
        collapse="never",
    )
    pipeline_run_attrs = hx_attrs(
        request,
        path="/pipeline/runs",
        method="post",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        include="#pipeline-form",
        indicator=INDICATOR,
        busy="region",
    )
    setup_flow = ProcessFlow(
        FlowStep(
            "Connect",
            status=("complete" if connections and ready_count == len(connections) else "current"),
            description=(
                f"{ready_count} of {len(connections)} connections validated."
                if connections
                else "Add a source and destination connection."
            ),
            status_text=(
                "Ready"
                if connections and ready_count == len(connections)
                else "Needs attention"
                if connections
                else "Start here"
            ),
        ),
        FlowStep(
            "Configure",
            status="complete" if pipeline_id else "current",
            description="Choose the source, destination, and write policy.",
            status_text="Saved" if pipeline_id else "In progress",
        ),
        FlowStep(
            "Run",
            status="current" if pipeline_id and initial_run_ready else "pending",
            description=(
                "Start a transfer and follow each persisted worker event."
                if pipeline_id and initial_run_ready
                else "Save a ready route to enable transfers."
            ),
            status_text="Ready" if pipeline_id and initial_run_ready else "Next",
        ),
        label="Pipeline workflow",
        direction="horizontal",
        collapse="never",
        density="compact",
        appearance="plain",
    )
    return [
        PageHeader(
            "Pipeline workspace",
            eyebrow="Data movement",
            description=(
                "Choose a route, run the transfer, then follow every stage from one focused "
                "workspace."
            ),
            actions=connection_summary,
            density="compact",
        ),
        setup_flow,
        html.div(
            alert_box(
                "Pipeline saved. You can load or run it any time." if notice == "saved" else "",
                kind="success",
            ),
            id="pipeline-save-notice",
        ),
        NavigationTabs(
            (
                "Route setup",
                surface_card(
                    html.form(
                        csrf_hidden(csrf_token),
                        html.input(
                            type="hidden", name="pipeline_id", value=pipeline_id, id="pipeline-id"
                        ),
                        _catalog_data(catalog_access, catalogs, pipelines),
                        PageHeader(
                            pipeline_name if pipeline_id else "Create a pipeline",
                            eyebrow="Current route" if pipeline_id else "New route",
                            meta=html.span(
                                f"{source_catalog.label} to "
                                f"{target_catalog.label if target_catalog is not None else 'destination not selected'}",
                                id="pipeline-route-description",
                            ),
                            level=2,
                            density="compact",
                            actions=ActionGroup(
                                _swap_direction_button(
                                    request,
                                    can_swap=swap_eligibility.allowed,
                                    reason=swap_eligibility.reason,
                                ),
                                Button(
                                    "Save pipeline",
                                    variant="secondary",
                                    size="sm",
                                    type="submit",
                                ),
                                Button(
                                    "Run transfer",
                                    type="button",
                                    variant="primary",
                                    size="md",
                                    attrs={
                                        "data-pipeline-start": "true",
                                        **pipeline_run_attrs,
                                        "aria-describedby": "pipeline-availability-note",
                                    },
                                    disabled=not initial_run_ready or not pipeline_id,
                                ),
                                gap="sm",
                                collapse="never",
                            ),
                        ),
                        html.div(
                            Alert(
                                availability_message,
                                tone="success" if initial_run_ready else "warning",
                            ),
                            id="pipeline-availability-note",
                        ),
                        html.p(
                            "Save your changes before running this pipeline.",
                            id="pipeline-unsaved-note",
                            hidden=True,
                            role="status",
                        ),
                        FormGrid(
                            FormField(
                                name="pipeline_name",
                                label="Pipeline name",
                                id="pipeline-name",
                                required=True,
                                control=html.input(
                                    id="pipeline-name",
                                    name="pipeline_name",
                                    value=pipeline_name,
                                    maxlength="120",
                                    required=True,
                                ),
                            ),
                            FormField(
                                name="write_mode",
                                label="Write mode",
                                id="pipeline-mode-select",
                                control=_write_mode_select(
                                    request,
                                    target_catalog,
                                    selected=write_mode,
                                    upsert_available=bool(upsert_keys),
                                ),
                            ),
                            FormField(
                                name="conflict_columns",
                                label="Upsert key",
                                id="pipeline-upsert-key-select",
                                control=_upsert_key_select(
                                    upsert_keys,
                                    selected=saved_conflict_columns,
                                ),
                            ),
                            columns={"base": 1, "lg": 3},
                            gap="md",
                        ),
                        Grid(
                            DATA_MOVER_DESIGN.apply(
                                "data-mover-inset",
                                stacked_surface(
                                    PageHeader(
                                        "Source",
                                        eyebrow="Read from",
                                        description="Choose a connected object or scan a local CSV.",
                                        level=3,
                                        density="compact",
                                        meta=html.span(
                                            Badge(source_catalog.label, tone="success"),
                                            id="pipeline-source-provider-label",
                                        ),
                                    ),
                                    Stack(
                                        FormField(
                                            name="source_provider",
                                            label="Source type",
                                            id="pipeline-source-select",
                                            control=_source_provider_select(
                                                request,
                                                connections,
                                                selected=source_provider,
                                                target_provider=target_provider,
                                                writer_policy=writer_policy,
                                            ),
                                        ),
                                        FormGrid(
                                            FormField(
                                                name="source_schema",
                                                label=source_catalog.namespaces_label,
                                                id="pipeline-source-schema-select",
                                                control=(
                                                    _source_namespace_control(
                                                        request,
                                                        catalog_access,
                                                        source_provider,
                                                        preferred_schema=source_schema_name,
                                                    )
                                                    if source_provider != "csv"
                                                    else html.select(
                                                        _option(
                                                            "uploaded",
                                                            "Scanned CSV"
                                                            if csv_source_ready
                                                            else "Upload required",
                                                            selected=True,
                                                            disabled=True,
                                                        ),
                                                        id="pipeline-source-schema-select",
                                                        name="source_schema",
                                                        data={
                                                            "pipeline-control": "source-schema",
                                                            "field-label": "Upload",
                                                        },
                                                    )
                                                ),
                                            ),
                                            FormField(
                                                name="source_table",
                                                label=(
                                                    "File(s)"
                                                    if source_provider in {"mss", "mcscop"}
                                                    else source_catalog.objects_label
                                                ),
                                                id="pipeline-source-table-select",
                                                control=(
                                                    _source_object_control(
                                                        request,
                                                        catalog_access,
                                                        source_provider,
                                                        source_schema_name,
                                                        preferred_object=source_table_display,
                                                    )
                                                    if source_provider != "csv"
                                                    else html.select(
                                                        _option(
                                                            "",
                                                            loaded_source_inspection.filename
                                                            if csv_source_ready
                                                            and loaded_source_inspection is not None
                                                            else "Upload required",
                                                            selected=True,
                                                            disabled=True,
                                                        ),
                                                        id="pipeline-source-table-select",
                                                        name="source_table",
                                                        data={"pipeline-control": "source-table"},
                                                    )
                                                ),
                                            ),
                                            columns=2,
                                            gap="sm",
                                        ),
                                        *_source_catalog_suggestions(
                                            catalog_access,
                                            source_provider,
                                            source_schema_name,
                                        ),
                                        Expander(
                                            "CSV alternative · Upload a local file",
                                            PageHeader(
                                                "CSV alternative",
                                                eyebrow="Local source",
                                                description="Scan a UTF-8 CSV and use its detected schema.",
                                                level=4,
                                                density="compact",
                                                meta=html.span(
                                                    Badge("5 MB maximum", tone="neutral"),
                                                    id="pipeline-csv-upload-state",
                                                ),
                                            ),
                                            _csv_upload_control(request),
                                            _csv_inspection(
                                                loaded_source_upload
                                                if source_provider == "csv"
                                                else None,
                                                loaded_source_inspection
                                                if source_provider == "csv"
                                                else None,
                                            ),
                                            open=source_provider == "csv",
                                            enhance="native",
                                            id="pipeline-csv-alternative",
                                        ),
                                        gap="md",
                                    ),
                                ),
                            ),
                            DATA_MOVER_DESIGN.apply(
                                "data-mover-inset",
                                stacked_surface(
                                    PageHeader(
                                        "Destination",
                                        eyebrow="Write to",
                                        description="Select a connected target and write policy.",
                                        level=3,
                                        density="compact",
                                        meta=html.span(
                                            Badge(
                                                target_catalog.label
                                                if target_catalog is not None
                                                else "Not selected",
                                                tone="info",
                                            ),
                                            id="pipeline-target-provider-label",
                                        ),
                                    ),
                                    Stack(
                                        FormField(
                                            name="destination_provider",
                                            label="Connection",
                                            id="pipeline-target-select",
                                            control=_destination_provider_select(
                                                request,
                                                connections,
                                                source_provider=source_provider,
                                                selected=target_provider,
                                                writer_policy=writer_policy,
                                            ),
                                        ),
                                        _dataset_creator(
                                            request,
                                            target_catalog,
                                            needs_dataset=bool(
                                                target_catalog
                                                and target_catalog.dataset_creation
                                                and not target_schema_name
                                            ),
                                        ),
                                        FormGrid(
                                            FormField(
                                                name="destination_schema",
                                                label=(
                                                    target_catalog.namespaces_label
                                                    if target_catalog is not None
                                                    else "Schema"
                                                ),
                                                id="pipeline-target-schema-select",
                                                control=html.select(
                                                    *(
                                                        _destination_namespace_options(
                                                            catalog_access,
                                                            target_catalog,
                                                            target_provider,
                                                            preferred_schema=target_schema_name,
                                                        )
                                                        if target_catalog is not None
                                                        else [
                                                            _option(
                                                                "",
                                                                "No connection available",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-target-schema-select",
                                                    name="destination_schema",
                                                    data={"pipeline-control": "target-schema"},
                                                    disabled=target_catalog is None,
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                    ),
                                                ),
                                            ),
                                            FormField(
                                                name="destination_table",
                                                label=(
                                                    target_catalog.objects_label
                                                    if target_catalog is not None
                                                    else "Table"
                                                ),
                                                id="pipeline-target-table-select",
                                                control=html.select(
                                                    *(
                                                        _destination_object_options(
                                                            catalog_access,
                                                            target_catalog,
                                                            target_provider,
                                                            target_schema_name,
                                                            preferred_table=target_table_name,
                                                            additional_tables=_created_destination_tables(
                                                                pipelines,
                                                                target_provider,
                                                                target_schema_name,
                                                            ),
                                                        )
                                                        if target_catalog is not None
                                                        else [
                                                            _option(
                                                                "",
                                                                "No connection available",
                                                                selected=True,
                                                                disabled=True,
                                                            )
                                                        ]
                                                    ),
                                                    id="pipeline-target-table-select",
                                                    name="destination_table",
                                                    data={"pipeline-control": "target-table"},
                                                    disabled=target_catalog is None,
                                                    **hx_attrs(
                                                        request,
                                                        path="/pipeline/preview",
                                                        method="post",
                                                        target="#pipeline-preview-region",
                                                        swap="none",
                                                        include="#pipeline-form",
                                                        trigger="change",
                                                    ),
                                                ),
                                            ),
                                            columns=2,
                                            gap="sm",
                                        ),
                                        html.div(
                                            FormField(
                                                name="destination_table_new",
                                                label=(
                                                    "New file name"
                                                    if target_catalog is not None
                                                    and target_catalog.dataset_creation
                                                    else "New table name"
                                                ),
                                                id="pipeline-target-table-new",
                                                help=(
                                                    "Used only when Create a new file is selected. Parquet is added automatically."
                                                    if target_catalog is not None
                                                    and target_catalog.dataset_creation
                                                    else "Used only when Create a new table is selected."
                                                ),
                                                control=html.input(
                                                    id="pipeline-target-table-new",
                                                    name="destination_table_new",
                                                    value=new_target_table_name,
                                                    maxlength="63",
                                                    placeholder=(
                                                        "readiness_export"
                                                        if target_catalog is not None
                                                        and target_catalog.dataset_creation
                                                        else "readiness_events_copy"
                                                    ),
                                                    pattern="[A-Za-z][A-Za-z0-9_]{0,62}",
                                                    disabled=target_table_name
                                                    != CREATE_TABLE_VALUE,
                                                ),
                                            ),
                                            class_="data-mover-new-destination-name",
                                            hidden=target_table_name != CREATE_TABLE_VALUE,
                                        ),
                                        gap="md",
                                    ),
                                ),
                            ),
                            columns={"base": 1, "xl": 2},
                            gap="md",
                        ),
                        ConnectorFlow(
                            _provider_node(
                                kind="source",
                                catalog=source_catalog,
                                detail=(
                                    f"{source_schema_name}.{source_object_name}"
                                    if source_provider != "csv"
                                    else loaded_source_inspection.filename
                                    if loaded_source_inspection is not None
                                    else "Choose a CSV file"
                                ),
                                configured=source_runtime_ready,
                                runtime=(
                                    str(connections[source_provider]["runtime"])
                                    if source_provider != "csv"
                                    else ""
                                ),
                            ),
                            ConnectorTrack(
                                Inline(
                                    Badge("Encrypted", tone="success"),
                                    html.span(
                                        f"{field_count} fields"
                                        if field_count
                                        else "See schema preview",
                                        id="pipeline-field-map-label",
                                        class_="hedron-process-flow-description",
                                    ),
                                    gap="sm",
                                ),
                                html.span(
                                    Status(
                                        "Ready to transfer"
                                        if initial_run_ready
                                        else "Setup required",
                                        tone="success" if initial_run_ready else "warning",
                                        live=False,
                                        variant="compact",
                                    ),
                                    id="pipeline-route-readiness",
                                ),
                                label="Encrypted transfer route",
                            ),
                            _provider_node(
                                kind="target",
                                catalog=target_catalog,
                                detail=(
                                    f"{target_schema_name}.{target_object_name}"
                                    if target_catalog is not None
                                    else "Configure a connection"
                                ),
                                configured=target_runtime_ready,
                                runtime=(
                                    str(connections[target_provider]["runtime"])
                                    if target_catalog is not None
                                    else ""
                                ),
                            ),
                            direction="horizontal",
                            collapse="lg",
                            appearance="soft",
                            background="dots",
                            overflow="auto",
                            min_size="sm",
                            id="pipeline-canvas",
                        ),
                        _pipeline_schema_preview_panel(
                            catalog_access=catalog_access,
                            source_provider=source_provider,
                            source_schema=source_schema_name,
                            source_object=source_object_name,
                            destination_provider=target_provider,
                            destination_schema=target_schema_name,
                            destination_object=target_object_name,
                            destination_create=target_table_name == CREATE_TABLE_VALUE,
                            csv_inspection=(
                                loaded_source_inspection if source_provider == "csv" else None
                            ),
                        ),
                        html.div(
                            _capability_surface(source_catalog, target_catalog),
                            id="pipeline-capabilities",
                        ),
                        html.div(id="pipeline-preview-region", hidden=True),
                        action=form_action(request, "/pipeline/save"),
                        method="post",
                        id="pipeline-form",
                    ),
                    id="pipeline-builder",
                ),
            ),
            (
                "Live transfer",
                surface_card(
                    PageHeader(
                        "Live transfer",
                        eyebrow="Run activity",
                        description=(
                            "Follow each stage, counter, and persisted worker event as it happens."
                        ),
                        level=2,
                        density="compact",
                    ),
                    html.div(id="pipeline-run-feedback"),
                    run_monitor
                    or html.div(
                        StateView(
                            "Ready for a live transfer",
                            kind="empty",
                            description=(
                                "Save a pipeline and choose Run transfer. Live progress and the "
                                "worker event feed will appear here."
                            ),
                        ),
                        id="pipeline-run-monitor",
                    ),
                ),
            ),
            (
                "Saved routes",
                surface_card(
                    PageHeader(
                        "Saved routes",
                        eyebrow="Reusable pipelines",
                        description="Load an existing route or start it immediately.",
                        level=2,
                        density="compact",
                        meta=Badge(f"{len(pipelines)} total", tone="neutral"),
                    ),
                    _saved_pipeline_cards(
                        request,
                        pipelines,
                        connections,
                        csrf_token=csrf_token,
                        writer_policy=writer_policy,
                        latest_runs=latest_runs,
                    ),
                    Alert(
                        (
                            "Demo connectors stay on this host and never call external endpoints."
                            if demo_mode
                            else "The worker decrypts credentials only for the claimed run and records persisted facts."
                        ),
                        title="Safe to explore" if demo_mode else "Live transfers",
                        tone="warning" if demo_mode else "success",
                    ),
                ),
            ),
            active=("Route setup" if notice == "saved" or run_monitor is None else "Live transfer"),
            id="pipeline-workspace-tabs",
        ),
    ]


def register_pipeline_routes(
    app: Hedron,
    fragment_router: HedronRouter,
    *,
    catalog_runner_factory: Callable[[Request], CatalogOperationRunner],
    authoring_operation_factory: Callable[[Request], PipelineAuthoringOperation],
) -> None:
    def bound_with_user_catalog(settings, user_id, request, operation):
        return with_user_catalog(
            settings, user_id, request, operation, catalog_runner_factory(request)
        )

    @app.page(
        "/pipeline",
        fragment_regions=(MAIN_PANEL, SIDE_NAV, PIPELINE_SAVE_NOTICE),
        include_in_schema=False,
    )
    async def pipeline_page(
        request: Request,
        auth: Auth,
        db: DbSession,
        settings: SettingsDep,
        notice: NoticeQuery = "",
        pipeline_id: str = "",
        run_id: str = "",
    ) -> Response:
        request.state.hedron_authenticated = True
        connections = {
            provider.name: {
                "configured": secret is not None,
                "validation": secret.validation_status if secret is not None else "unconfigured",
                "runtime": secret.runtime_status if secret is not None else "",
            }
            for provider, secret in list_user_secrets(db, auth.user)
        }
        pipelines = list_pipelines(db, auth.user)
        latest_runs = latest_run_map(
            db, user=auth.user, pipeline_ids=[item.id for item in pipelines]
        )
        loaded_pipeline: PipelineDefinition | None = None
        loaded_source_upload: PipelineUpload | None = None
        loaded_source_inspection: CsvInspection | None = None
        if pipeline_id:
            pipeline = db.get(PipelineDefinition, pipeline_id)
            if pipeline is None or pipeline.user_id != auth.user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
                )
            loaded_pipeline = pipeline
            if loaded_pipeline.source_upload is not None:
                loaded_source_upload = loaded_pipeline.source_upload
                try:
                    loaded_source_inspection = inspection_from_upload(loaded_source_upload)
                except ValueError:
                    loaded_source_upload = None
                    loaded_source_inspection = None
        if run_id:
            try:
                displayed_run = owned_run(db, user=auth.user, run_id=run_id)
            except LookupError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        elif loaded_pipeline is not None:
            displayed_run = latest_runs.get(loaded_pipeline.id)
        else:
            displayed_run = max(
                latest_runs.values(),
                key=lambda item: item.created_at,
                default=None,
            )
        body = await run_owned_sync(
            request,
            _pipeline_body_in_thread,
            request,
            settings,
            auth.user.id,
            connections,
            pipelines,
            auth.session.csrf_token,
            notice,
            loaded_pipeline,
            loaded_source_upload,
            loaded_source_inspection,
            latest_runs,
            (
                _run_status_fragment(
                    request,
                    db,
                    displayed_run,
                    csrf_token=auth.session.csrf_token,
                )
                if displayed_run is not None
                else None
            ),
            settings.is_demo_mode,
            catalog_runner_factory(request),
        )
        return await render_authenticated_view(
            request,
            body=body,
            auth=auth,
            settings=settings,
            page_title="Pipeline",
            csrf_token=auth.session.csrf_token,
            push_path="/pipeline",
            headers={"Cache-Control": "no-store"},
        )

    from app.ui.routes.pipeline_csv import register_pipeline_csv_routes
    from app.ui.routes.pipeline_datasets import register_pipeline_dataset_routes
    from app.ui.routes.pipeline_preview import (
        PipelinePreviewDependencies,
        register_pipeline_preview_routes,
    )
    from app.ui.routes.pipeline_runs import register_pipeline_run_routes
    from app.ui.routes.pipeline_save import register_pipeline_save_routes

    register_pipeline_csv_routes(
        app,
        inspection_fragment=_csv_inspection,
    )
    register_pipeline_dataset_routes(
        app,
        dataset_creator_fragment=_dataset_creator,
        schema_options=_schema_options,
        table_options=_table_options,
        with_user_session=with_user_session,
        with_user_catalog=bound_with_user_catalog,
    )
    register_pipeline_preview_routes(
        app,
        dependencies=PipelinePreviewDependencies(
            with_user_catalog=bound_with_user_catalog,
            can_swap_direction=_can_swap_direction,
            eligible_destinations=_eligible_destinations,
            normalized_selection=_normalized_selection,
            pipeline_preview_fragment=_pipeline_preview_fragment,
            pipeline_schema_preview_panel=_pipeline_schema_preview_panel,
            committed_new_table_name=_committed_new_table_name,
            provider_node=_provider_node,
            csv_inspection_fragment=_csv_inspection,
            connection_configured=_connection_configured,
            connection_runnable=_connection_runnable,
        ),
    )
    register_pipeline_save_routes(
        app,
        authoring_operation_factory=authoring_operation_factory,
    )

    register_pipeline_run_routes(
        app,
        fragment_router,
        status_fragment=_run_status_fragment,
        events_loader=_events_after_for_run_routes,
    )


def _events_after_for_run_routes(db, *, run, after_sequence=0):
    """Load run events for the extracted lifecycle routes."""

    return pipeline_run_service.events_after(db, run=run, after_sequence=after_sequence)


def _pipeline_body_in_thread(
    request,
    settings,
    user_id,
    connections,
    pipelines,
    csrf_token,
    notice,
    loaded_pipeline,
    loaded_source_upload,
    loaded_source_inspection,
    latest_runs,
    run_monitor,
    demo_mode,
    catalog_runner: CatalogOperationRunner,
):
    """Build the provider-backed pipeline body with a thread-owned session."""

    metadata = request_metadata(settings, request)
    return catalog_runner(
        ActorContext(user_id=user_id, request_id=metadata.request_id, source_ip=metadata.source_ip),
        lambda catalog_access: _pipeline_body(
            request,
            catalog_access,
            connections,
            pipelines,
            csrf_token=csrf_token,
            notice=notice,
            loaded_pipeline=loaded_pipeline,
            loaded_source_upload=loaded_source_upload,
            loaded_source_inspection=loaded_source_inspection,
            latest_runs=latest_runs,
            run_monitor=run_monitor,
            demo_mode=demo_mode,
            writer_policy=lambda provider: writer_enabled(provider, settings=settings),
        ),
    )


def _run_manifest(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_schema_surface(title: str, manifest: dict[str, Any], row_value: str):
    schema_value = manifest.get("schema")
    schema: dict[str, Any] = schema_value if isinstance(schema_value, dict) else {}
    columns_value = schema.get("columns")
    columns = columns_value if isinstance(columns_value, list) else []
    primary_key_value = schema.get("primary_key")
    primary_key = primary_key_value if isinstance(primary_key_value, list) else []
    schema_before = manifest.get("schema_before")
    metadata_value = manifest.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    row_metadata_value = metadata.get("rows")
    row_metadata: dict[str, Any] = (
        row_metadata_value if isinstance(row_metadata_value, dict) else {}
    )
    schema_metadata_value = metadata.get("schema")
    schema_metadata: dict[str, Any] = (
        schema_metadata_value if isinstance(schema_metadata_value, dict) else {}
    )
    snapshot_label = "Before + after" if isinstance(schema_before, dict) else "Captured"
    return Surface(
        PageHeader(
            title,
            eyebrow="Persisted schema",
            description="Schema and counts captured by the worker for this run.",
            level=3,
            density="compact",
            meta=ActionGroup(
                Badge(
                    snapshot_label, tone="success" if isinstance(schema_before, dict) else "info"
                ),
                Badge(
                    provenance_label(str(row_metadata.get("provenance") or "unavailable")),
                    tone="success"
                    if row_metadata.get("provenance") in {"exact", "captured"}
                    else "info",
                ),
                Badge(
                    provenance_label(str(schema_metadata.get("provenance") or "unavailable")),
                    tone="success" if schema_metadata.get("available") else "warning",
                ),
                gap="xs",
                collapse="never",
            ),
        ),
        Grid(
            Metric("Rows", row_value),
            Metric("Columns", f"{len(columns):,}"),
            Metric("Primary key", ", ".join(str(item) for item in primary_key) or "None"),
            columns={"base": 2, "lg": 3},
            gap="sm",
        ),
        _schema_columns_table(columns, f"Persisted columns in {title}"),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _run_schema_results(run):
    source_manifest = _run_manifest(run.source_manifest_json)
    destination_manifest = _run_manifest(run.destination_manifest_json)
    verification = _run_manifest(run.verification_json)
    before = verification.get("destination_rows_before")
    after = verification.get("destination_rows_after")
    destination_rows = (
        f"{before:,} → {after:,}"
        if isinstance(before, int) and isinstance(after, int)
        else "Unavailable"
    )
    source_rows = source_manifest.get("rows", run.source_rows)
    differences = schema_diff(source_manifest, destination_manifest)
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        stacked_surface(
            PageHeader(
                "Run schema & row counts",
                eyebrow="After-run review",
                description="Compare the schema captured by the worker with the destination count change.",
                level=3,
                density="compact",
            ),
            Expander(
                "Source and destination manifests",
                Grid(
                    _run_schema_surface("Source", source_manifest, f"{int(source_rows):,}"),
                    _run_schema_surface("Destination", destination_manifest, destination_rows),
                    columns={"base": 1, "xl": 2},
                    gap="sm",
                ),
                open=False,
                enhance="native",
            ),
            _schema_diff_surface(differences),
            id="pipeline-run-schema-results",
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _schema_diff_surface(differences: list[dict[str, str]]):
    if not differences:
        return StateView(
            "Schema comparison unavailable",
            kind="empty",
            description="One or both run manifests did not include column metadata.",
        )
    status_labels = {
        "match": ("Match", "success"),
        "changed": ("Changed", "warning"),
        "missing_destination": ("Missing at destination", "danger"),
        "extra_destination": ("Extra at destination", "info"),
    }
    issues = sum(row["status"] != "match" for row in differences)
    comparison_label = (
        f"Review {issues} schema differences"
        if issues
        else f"All {len(differences)} columns match · view comparison"
    )
    return Surface(
        PageHeader(
            "Schema comparison",
            eyebrow="Source versus destination",
            description="Review column names, types, and nullability captured for this run.",
            level=3,
            density="compact",
        ),
        Expander(
            comparison_label,
            ScrollRegion(
                Table(
                    rows=[
                        [
                            Badge(
                                status_labels.get(row["status"], ("Review", "warning"))[0],
                                tone=cast(
                                    Literal["neutral", "info", "success", "warning", "danger"],
                                    status_labels.get(row["status"], ("Review", "warning"))[1],
                                ),
                                size="sm",
                            ),
                            html.strong(row["name"]),
                            f"{row['source_type']} · {row['source_nullable']}",
                            f"{row['destination_type']} · {row['destination_nullable']}",
                        ]
                        for row in differences
                    ],
                    columns=[
                        TableColumn(header="Status"),
                        TableColumn(header="Column"),
                        TableColumn(header="Source"),
                        TableColumn(header="Destination"),
                    ],
                    density="compact",
                    sticky_header=True,
                    zebra=True,
                ),
                axis="block",
                size="sm",
                label="Schema comparison",
            ),
            open=issues > 0,
            enhance="native",
        ),
        appearance="plain",
        padding="sm",
        elevation="none",
    )


def _run_recovery_surface(request: Request, run, *, csrf_token: str):
    run_status = str(run.status or "")
    facts = _run_manifest(run.verification_json)
    reconciliation_required = bool(
        getattr(run, "reconciliation_required", False) or facts.get("reconciliation_required")
    )
    if run_status not in {"failed", "failed_needs_reconciliation", "cancelled"}:
        return None
    review_recorded = bool(
        getattr(run, "reconciliation_reviewed_at", None) or facts.get("reconciliation_reviewed_at")
    )
    retry_form = None
    can_retry = (run_status == "failed" and run.retryable) or (
        reconciliation_required and review_recorded
    )
    if can_retry and run.pipeline_definition_id:
        retry_form = html.form(
            csrf_hidden(csrf_token),
            html.input(type="hidden", name="pipeline_id", value=run.pipeline_definition_id),
            Button(
                "Retry run",
                type="submit",
                variant="primary",
                size="sm",
                attrs=hx_attrs(
                    request,
                    path="/pipeline/runs",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                ),
            ),
            action=form_action(request, "/pipeline/runs"),
            method="post",
        )
    review_form = None
    if reconciliation_required and not review_recorded:
        review_form = html.form(
            csrf_hidden(csrf_token),
            Button(
                "Review recorded" if review_recorded else "Record reconciliation review",
                type="submit",
                variant="secondary",
                size="sm",
                disabled=review_recorded,
                attrs=hx_attrs(
                    request,
                    path=f"/pipeline/runs/{run.id}/reconcile",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                    confirm="Confirm that an operator reviewed the destination before any retry.",
                ),
            ),
            action=form_action(request, f"/pipeline/runs/{run.id}/reconcile"),
            method="post",
        )
    outcome = run_outcome(run)
    impact = outcome.data_impact if outcome is not None else DataImpact.UNCERTAIN
    return DATA_MOVER_DESIGN.apply(
        "data-mover-inset",
        Surface(
            PageHeader(
                "Recovery guidance",
                eyebrow="Operator action required",
                description=(
                    "Review recorded. Start a deliberate retry only after confirming the destination state."
                    if can_retry and reconciliation_required
                    else "The transfer failed and is eligible for retry."
                    if can_retry
                    else "The destination was not changed. Correct the issue before starting another run."
                    if impact == DataImpact.UNCHANGED
                    else "The destination changes were rolled back. Start the transfer again when ready."
                    if impact == DataImpact.ROLLED_BACK
                    else "Destination state may be uncertain. Inspect it before retrying."
                ),
                level=3,
                density="compact",
            ),
            feedback_panel(outcome, label="Pipeline recovery feedback"),
            ActionGroup(retry_form, review_form, gap="sm", collapse="never")
            if retry_form or review_form
            else None,
            appearance="plain",
            padding="sm",
            elevation="none",
        ),
    )


def _run_status_fragment(
    request: Request,
    db,
    run,
    csrf_token: str = "",
    events=None,
):
    lines = events if events is not None else events_after(db, run=run, after_sequence=0)
    next_sequence = lines[-1].sequence if lines else None
    monitor_active = run.status not in {
        "succeeded",
        "failed",
        "cancelled",
        "failed_needs_reconciliation",
    }
    run_status = (run.status or "idle").lower()
    facts = _run_manifest(run.verification_json)
    reconciliation_required = bool(
        getattr(run, "reconciliation_required", False) or facts.get("reconciliation_required")
    )
    reconciliation_reviewed = bool(
        getattr(run, "reconciliation_reviewed_at", None) or facts.get("reconciliation_reviewed_at")
    )
    run_badge_text = "Standing by"
    run_badge_tone = "info"
    if run_status == "succeeded":
        run_badge_text = "Succeeded"
    elif run_status in {
        "queued",
        "running",
        "validating",
        "extracting",
        "transforming",
        "loading",
        "verifying",
    }:
        run_badge_text = run_stage_copy(run_status)[0]
        run_badge_tone = "info"
    elif run_status == "cancelled":
        run_badge_text = "Cancelled"
        run_badge_tone = "warning"
    elif run_status in {"failed", "failed_needs_reconciliation"}:
        run_badge_text = "Failed"
        run_badge_tone = "danger"
    progress_value = run_progress(run_status)
    action_state = run_action_state(
        run,
        progress=progress_value if not monitor_active else None,
        revision=next_sequence,
    )
    stage_label, stage_description = run_stage_copy(run_status)
    if not stage_description:
        stage_description = "Worker state persisted to the run log."
    flow_statuses = run_flow_statuses(
        run_status, getattr(run, "last_safe_stage", "") or facts.get("last_safe_stage", "")
    )
    snapshot = parse_snapshot(run.definition_snapshot_json)
    source_label = _provider_label(snapshot.source_provider)
    target_label = _provider_label(snapshot.destination_provider)
    failed = run_status in {"failed", "failed_needs_reconciliation"}
    source_state = (
        "failed"
        if failed
        else "succeeded"
        if run_status in {"loading", "verifying", "succeeded"}
        else "running"
        if monitor_active
        else "ready"
    )
    target_state = (
        "failed"
        if failed
        else "succeeded"
        if run_status == "succeeded"
        else "running"
        if run_status in {"loading", "verifying"}
        else "ready"
    )
    hx_poll = hx_attrs(
        request,
        path=f"/pipeline/runs/{run.id}/status",
        method="get",
        target="#pipeline-run-monitor",
        swap="outerHTML",
        polling=1.5,
        indicator=INDICATOR,
        busy="region",
    )
    run_again_form = (
        html.form(
            csrf_hidden(csrf_token),
            html.input(
                type="hidden",
                name="pipeline_id",
                value=run.pipeline_definition_id or "",
            ),
            Button(
                "Retry run" if run_status == "failed" and run.retryable else "Run again",
                type="submit",
                variant="primary",
                size="sm",
                disabled=(
                    monitor_active
                    or not run.pipeline_definition_id
                    or (reconciliation_required and not reconciliation_reviewed)
                    or (run_status == "failed" and not run.retryable)
                ),
                attrs=hx_attrs(
                    request,
                    path="/pipeline/runs",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                ),
            ),
            action=form_action(request, "/pipeline/runs"),
            method="post",
            id=f"pipeline-run-again-form-{run.id}",
        )
        if run.pipeline_definition_id
        and (
            run_status not in {"failed", "failed_needs_reconciliation"}
            or run.retryable
            or (reconciliation_required and reconciliation_reviewed)
        )
        else None
    )
    cancel_form = (
        html.form(
            csrf_hidden(csrf_token),
            Button(
                "Cancel run",
                type="submit",
                variant="danger",
                size="sm",
                attrs=hx_attrs(
                    request,
                    path=f"/pipeline/runs/{run.id}/cancel",
                    method="post",
                    target="#pipeline-run-monitor",
                    swap="outerHTML",
                    indicator=INDICATOR,
                    busy="region",
                    confirm="Cancel this transfer? The worker will stop at its next safe checkpoint.",
                ),
            ),
            action=form_action(request, f"/pipeline/runs/{run.id}/cancel"),
            method="post",
        )
        if monitor_active
        else None
    )
    status_bar = AsyncRegion(
        ActionGroup(
            DATA_MOVER_DESIGN.apply(
                "data-mover-operational-status",
                Status(
                    run_badge_text,
                    tone=run_badge_tone,
                    live=True,
                    variant="activity" if monitor_active else "compact",
                ),
            ),
            Badge(snapshot.name, tone="neutral"),
            cancel_form,
            run_again_form,
            align="between",
            gap="sm",
            collapse="never",
        ),
        state=action_state.phase,
        label=f"Pipeline run {run.id} status",
        mark="pipeline-run-status",
    )
    return html.div(
        status_bar,
        ConnectorFlow(
            ConnectorNode(
                source_label,
                leading=Avatar(source_label, size="md", appearance="soft", shape="rounded"),
                state=source_state,
                kind="source",
                detail="Source",
                runtime=_run_locator_label(snapshot.source),
            ),
            ConnectorTrack(
                Inline(
                    CircularProgress(
                        None if monitor_active else progress_value,
                        indeterminate=monitor_active,
                        label=f"Transfer {progress_value}% complete",
                    ),
                    Status(
                        f"{progress_value}% · {stage_label}",
                        tone=run_badge_tone,
                        live=False,
                        variant="activity" if monitor_active else "compact",
                    ),
                    gap="sm",
                ),
                Progress(
                    progress_value,
                    label=f"Transfer progress: {progress_value}%",
                ),
                active=monitor_active,
                label=(
                    f"Data moving from {source_label} to {target_label}"
                    if monitor_active
                    else f"Transfer from {source_label} to {target_label}"
                ),
            ),
            ConnectorNode(
                target_label,
                leading=Avatar(target_label, size="md", appearance="soft", shape="rounded"),
                state=target_state,
                kind="target",
                detail="Destination",
                runtime=_run_locator_label(snapshot.destination),
            ),
            direction="horizontal",
            collapse="never",
            appearance="soft",
            density="compact",
            background="dots",
            overflow="auto",
            min_size="sm",
        ),
        ProcessFlow(
            *run_flow_steps(flow_statuses),
            label="Live transfer stages",
            direction="horizontal",
            collapse="never",
            density="compact",
            appearance="plain",
        ),
        Alert(
            stage_description,
            title=f"{stage_label} stage",
            tone="danger" if failed else "success" if run_status == "succeeded" else "info",
        ),
        _run_recovery_surface(request, run, csrf_token=csrf_token),
        Grid(
            Metric(
                "Extracted",
                f"{run.source_rows:,} rows",
                delta=_format_file_size(run.source_bytes),
                delta_tone="up" if run.source_rows else "neutral",
            ),
            Metric(
                "Loaded",
                f"{run.loaded_rows:,} rows",
                delta=_format_file_size(run.loaded_bytes),
                delta_tone="up" if run.loaded_rows else "neutral",
            ),
            destination_count_metric(run),
            Metric(
                "Worker stage",
                stage_label,
                delta=f"Attempt {run.attempt}",
            ),
            columns={"base": 2, "xl": 4},
            gap="sm",
        ),
        _run_schema_results(run)
        if run_status in {"succeeded", "failed", "cancelled", "failed_needs_reconciliation"}
        else None,
        Text(
            verification_summary(run),
            role="caption",
            overflow="wrap",
        )
        if run_status == "succeeded"
        else None,
        Expander(
            "Live event feed",
            Status(
                "Listening for worker events" if monitor_active else "Persisted run history",
                tone="info" if monitor_active else run_badge_tone,
                live=False,
                variant="activity" if monitor_active else "compact",
            ),
            ScrollRegion(
                Timeline(
                    [
                        (
                            event.occurred_at.strftime("%H:%M:%S"),
                            EVENT_STAGE_LABELS.get(event.stage, event.stage.title()),
                            event.message,
                        )
                        for event in lines
                    ],
                    label=f"Event feed for {snapshot.name}",
                ),
                id="pipeline-run-log",
                axis="block",
                size="md",
                label="Live event feed",
            ),
            open=monitor_active,
            enhance="native",
        ),
        id="pipeline-run-monitor",
        aria={"live": "polite"},
        **({} if not monitor_active else hx_poll),
    )
