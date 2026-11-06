"""Shared HTTP and MCP projections of persisted result inspections."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from lyra.contracts.inspection import (
    ActiveResultOutput,
    CompactProvenance,
    ResultOutputInspection,
    TerminalResultOutput,
    TruncationOutput,
)
from lyra.contracts.jobs import ResultOutputMetadata, result_ref_for_job

if TYPE_CHECKING:
    from lyra.contracts.jobs import JobRunProvenance
    from lyra.server.job_store import JobObservation

MAX_DISPLAY_CHARACTERS = 500


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
        elif key == "failures" and isinstance(value, list):
            compact[key] = [
                {
                    **entry,
                    "message": _display(str(entry.get("message", "")), truncation),
                }
                for entry in value[:20]
                if isinstance(entry, dict)
            ]
        elif key == "dependency_path" and isinstance(value, list):
            compact[key] = value
        elif isinstance(value, dict | list):
            compact[key] = _compact_input(value)
            truncation.omitted_sections.append("error details")
        else:
            compact[key] = value
    return compact


def project_observation(
    job_id: str, observation: JobObservation
) -> ActiveResultOutput | TerminalResultOutput:
    """Project lifecycle, provenance, and named outputs from retained results.

    Returns:
        A typed active or terminal projection; callers handle missing results.
    """
    snapshot = observation.snapshot
    provenance = observation.provenance
    truncation = TruncationOutput()
    if snapshot.status in {"queued", "running"}:
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
                "metric": provenance.accepted.plan.metric
                if provenance
                else snapshot.metric,
                "edition": provenance.accepted.plan.edition if provenance else None,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.updated_at,
                "started_at": snapshot.started_at,
                "progress": progress,
                "truncation": truncation,
            }
        )
    status = snapshot.status
    error = snapshot.error
    output = TerminalResultOutput.model_validate(
        {
            "job_id": job_id,
            "result_ref": result_ref_for_job(job_id),
            "status": status,
            "result_kind": "table" if status == "succeeded" else "failed",
            "metric": provenance.accepted.plan.metric
            if provenance
            else snapshot.metric,
            "edition": provenance.accepted.plan.edition if provenance else None,
            "completed_at": snapshot.completed_at,
            "lifetime": observation.lifetime,
            "download": f"/jobs/{job_id}/result/download",
            "error": _error_details(error, truncation),
            "truncation": truncation,
        }
    )
    if provenance:
        output.provenance = CompactProvenance(
            metric=provenance.accepted.plan.metric,
            edition=provenance.accepted.plan.edition,
            cache_revision=provenance.accepted.plan.cache_revision,
            catalog_fingerprint=provenance.accepted.catalog_fingerprint,
            plugin=provenance.accepted.plugin,
            created_at=provenance.accepted.created_at,
            input={
                key: (
                    {name: _compact_input(item) for name, item in value.items()}
                    if key == "parameters" and isinstance(value, dict)
                    else _compact_input(value)
                )
                for key, value in _submitted_input(provenance).items()
            },
        )
        truncation.omitted_sections.append("provenance.accepted.plan.contracts")
        if output.provenance.input != _submitted_input(provenance):
            truncation.omitted_sections.append("provenance.input details")
    output.outputs = inspect_outputs(observation)
    truncation.omitted_sections = list(dict.fromkeys(truncation.omitted_sections))
    return output


def inspect_outputs(observation: JobObservation) -> dict[str, ResultOutputInspection]:
    """Return saved artifact statistics with the job's accepted declarations."""
    if observation.inspection is None or observation.provenance is None:
        return {}
    declarations = observation.provenance.accepted.plan.output_metadata
    outputs = {}
    for name, saved in observation.inspection.items():
        item = deepcopy(saved)
        metadata = item.pop("metadata")
        if metadata is not None:
            columns = metadata["summary"]
            declaration = declarations[name].model_copy(
                update={
                    "columns": [
                        column
                        for column in declarations[name].columns
                        if column.name in columns
                        or column.name == declarations[name].geometry
                    ]
                }
            )
            metadata = ResultOutputMetadata(**metadata, declaration=declaration)
        outputs[name] = ResultOutputInspection(metadata=metadata, **item)
    return outputs


def _submitted_input(provenance: JobRunProvenance) -> dict[str, Any]:
    """Return submitted public inputs without inserting defaults."""
    request = provenance.accepted.plan.request
    result = {
        name: value.model_dump(mode="json") for name, value in request.inputs.items()
    }
    if "parameters" in request.supplied_fields:
        result["parameters"] = request.parameters
    return result
