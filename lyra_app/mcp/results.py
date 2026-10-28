"""Bounded MCP result projections and authenticated HTTP handoffs."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

from lyra.sdk.models.job import TableJobResult, describe_output, result_ref_for_job

from lyra_app.mcp.models import (
    ActiveResultOutput,
    CompactProvenance,
    LyraAPIHandoffOutput,
    ResultOutputInspection,
    TerminalResultOutput,
    TruncationOutput,
)

if TYPE_CHECKING:
    from lyra_app.job_store import JobObservation

MAX_DISPLAY_CHARACTERS = 500


def handoff(base_url: str, job_id: str, suffix: str) -> LyraAPIHandoffOutput:
    """Build authenticated access metadata without exposing credentials.

    Returns:
        An absolute URL preserving the configured base path.
    """
    base = urlsplit(base_url)
    path = f"{base.path.rstrip('/')}/jobs/{quote(job_id, safe='-._~')}/result/{suffix}"
    return LyraAPIHandoffOutput.model_validate(
        {
            "method": "GET",
            "url": urlunsplit((base.scheme, base.netloc, path, "", "")),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        }
    )


def _display(value: str, truncation: TruncationOutput) -> str:
    if len(value) <= MAX_DISPLAY_CHARACTERS:
        return value
    truncation.shortened_strings += 1
    return value[: MAX_DISPLAY_CHARACTERS - 1] + "…"


def _compact_crs(value: object) -> object:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            name = properties.get("name")
            if isinstance(name, str) and len(name) <= MAX_DISPLAY_CHARACTERS:
                return name
    if isinstance(value, str) and len(value) <= MAX_DISPLAY_CHARACTERS:
        return value
    return None


def _compact_input(value: object) -> object:
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and value.get("type") in {
            "FeatureCollection",
            "Feature",
            "Point",
            "Polygon",
            "MultiPolygon",
            "LineString",
            "MultiPoint",
            "MultiLineString",
            "GeometryCollection",
        }:
            return {
                "type": value["type"],
                "feature_count": len(value.get("features", []))
                if value["type"] == "FeatureCollection"
                else 1,
                "crs": _compact_crs(value.get("crs")),
            }
        if (
            "value" in value
            and isinstance(value["value"], dict)
            and "type" in value["value"]
        ):
            return _compact_input(value["value"])
        return {"type": "object", "count": len(value)}
    if isinstance(value, list):
        return {"type": "array", "count": len(value)}
    if isinstance(value, str) and len(value) > MAX_DISPLAY_CHARACTERS:
        return {"type": "string", "characters": len(value)}
    return value


def _error_details(
    error: dict[str, Any] | None, truncation: TruncationOutput
) -> dict[str, Any] | None:
    if error is None:
        return None
    compact: dict[str, Any] = {}
    for key, value in error.items():
        if len(key) > MAX_DISPLAY_CHARACTERS:
            truncation.omitted_sections.append("error field")
        elif isinstance(value, str):
            # Codes and field names are identifiers, never abbreviate them.
            if key in {"code", "type", "field", "node"}:
                compact[key] = value
            else:
                compact[key] = _display(value, truncation)
        elif key == "dependency_path" and isinstance(value, list):
            compact[key] = value
        elif isinstance(value, dict | list):
            compact[key] = _compact_input(value)
            truncation.omitted_sections.append("error details")
        else:
            compact[key] = value
    return compact


def project_observation(
    job_id: str, observation: JobObservation, base_url: str
) -> ActiveResultOutput | TerminalResultOutput:
    """Project lifecycle, provenance, and named outputs from retained results.

    Returns:
        A typed active or terminal projection; callers handle missing results.
    """
    snapshot, result = observation.snapshot, observation.result
    truncation = TruncationOutput()
    if (
        result is None
        and snapshot is not None
        and snapshot.status in {"queued", "running"}
    ):
        progress = (
            snapshot.progress.model_copy(deep=True) if snapshot.progress else None
        )
        if progress and progress.message:
            progress.message = _display(progress.message, truncation)
        return ActiveResultOutput.model_validate(
            {
                "job_id": job_id,
                "result_ref": result_ref_for_job(job_id),
                "status": snapshot.status,
                "metric": snapshot.metric,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.updated_at,
                "started_at": snapshot.started_at,
                "progress": progress,
                "truncation": truncation,
            }
        )
    status = result.status if result else snapshot.status if snapshot else None
    error = (
        getattr(result, "error", None)
        if result
        else snapshot.error
        if snapshot
        else None
    )
    output = TerminalResultOutput.model_validate(
        {
            "job_id": job_id,
            "result_ref": result_ref_for_job(job_id),
            "status": status,
            "result_kind": result.kind if result else status,
            "completed_at": snapshot.completed_at
            if snapshot and snapshot.status == status
            else None,
            "lifetime": observation.lifetime,
            "descriptor": handoff(base_url, job_id, "descriptor"),
            "error": _error_details(error, truncation),
            "truncation": truncation,
        }
    )
    provenance = observation.provenance
    if provenance:
        output.provenance = CompactProvenance(
            metric=provenance.metric,
            catalog_fingerprint=provenance.catalog_fingerprint,
            plugin=provenance.plugin,
            created_at=provenance.created_at,
            input={
                key: _compact_input(value) for key, value in provenance.input.items()
            },
        )
        truncation.omitted_sections.append("provenance.outputs")
        if output.provenance.input != provenance.input:
            truncation.omitted_sections.append("provenance.input details")
    if isinstance(result, TableJobResult):
        output.outputs = _inspect_outputs(result)
    truncation.omitted_sections = list(dict.fromkeys(truncation.omitted_sections))
    return output


def _inspect_outputs(result: TableJobResult) -> dict[str, ResultOutputInspection]:
    outputs = {}
    remaining_rows = 5
    remaining_columns = 20
    for name, value in result.outputs.items():
        metadata = describe_output(value, result.output_metadata[name])
        omitted = TruncationOutput()
        columns = list(metadata.summary)[:remaining_columns]
        remaining_columns -= len(columns)
        omitted.omitted_columns = metadata.column_count - len(columns)
        metadata.summary = {key: metadata.summary[key] for key in columns}
        metadata.declaration = metadata.declaration.model_copy(
            update={
                "columns": [
                    column
                    for column in metadata.declaration.columns
                    if column.name in columns
                ]
            }
        )
        rows = value if isinstance(value, list) else value["features"]
        rows = cast("list[dict[str, Any]]", rows)
        preview_rows = deepcopy(rows[:remaining_rows])
        remaining_rows -= len(preview_rows)
        omitted.omitted_rows = len(rows) - len(preview_rows)
        _trim_properties(
            preview_rows, columns, omitted, spatial=isinstance(value, dict)
        )
        preview = (
            preview_rows
            if isinstance(value, list)
            else {"type": "FeatureCollection", "features": preview_rows}
        )
        outputs[name] = ResultOutputInspection(
            metadata=metadata, preview=preview, truncation=omitted
        )
    return outputs


def _trim_properties(
    rows: list[dict[str, Any]],
    columns: list[str],
    omitted: TruncationOutput,
    *,
    spatial: bool,
) -> None:
    for row in rows:
        properties = row.get("properties") if spatial else row
        if not isinstance(properties, dict):
            continue
        for key in list(properties):
            if key not in columns:
                del properties[key]
            elif isinstance(properties[key], str):
                properties[key] = _display(properties[key], omitted)
