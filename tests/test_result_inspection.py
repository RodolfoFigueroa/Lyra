"""Named inspection uses public rows and a shared, bounded output contract."""

import json
from datetime import UTC, datetime

import pytest
from lyra.sdk.models.job import (
    FailedJobResult,
    TableJobResult,
    build_result_descriptor,
)
from lyra.sdk.models.plugin import TableColumn, TableOutput
from mcp.types import TextContent

from lyra_app.job_observation import JobObservation
from lyra_app.mcp.models import TerminalResultOutput
from lyra_app.mcp.results import project_observation
from lyra_app.mcp.serialization import serialize_result, serialized_size


def column(name: str) -> TableColumn:
    return TableColumn(name=name, type="number", unit=None, description=name)


def inspect(result: TableJobResult | FailedJobResult) -> TerminalResultOutput:
    output = project_observation(
        "job", JobObservation(result=result), "https://lyra.test/api"
    )
    assert isinstance(output, TerminalResultOutput)
    return output


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("single", [False, True])
def test_named_summary_and_preview(*, empty: bool, single: bool) -> None:
    outputs = {"ordinary": [] if empty else [{"value": 7}, {"value": None}]}
    metadata = {"ordinary": TableOutput(columns=[column("value")])}
    if not single:
        outputs["spatial"] = {
            "type": "FeatureCollection",
            "features": []
            if empty
            else [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-99, 20]},
                    "properties": {"value": 42, "date": "2026-10-27", "null": None},
                }
            ],
        }
        metadata["spatial"] = TableOutput(columns=[column("value")], geometry="geom")
    result = TableJobResult(job_id="job", outputs=outputs, output_metadata=metadata)
    before = result.model_dump_json()
    descriptor = build_result_descriptor(result, completed_at=datetime.now(UTC))
    inspection = inspect(result)
    assert set(inspection.outputs) == set(outputs)
    for name, entry in inspection.outputs.items():
        assert entry.metadata == descriptor.outputs[name]
        assert entry.preview == outputs[name]
        assert entry.truncation.omitted_rows == 0
    ordinary = descriptor.outputs["ordinary"]
    assert ordinary.row_count == (0 if empty else 2)
    assert ordinary.crs is None
    assert ordinary.summary["value"].minimum == (None if empty else 7)
    assert ordinary.summary["value"].null_count == (0 if empty else 1)
    if not single:
        spatial = descriptor.outputs["spatial"]
        assert spatial.row_count == (0 if empty else 1)
        assert spatial.crs == "EPSG:4326"
        assert spatial.declaration.geometry == "geom"
        assert spatial.geometry_types == ([] if empty else ["Point"])
        assert spatial.summary["value"].maximum == (None if empty else 42)
        if not empty:
            assert spatial.summary["date"].minimum is None
            assert spatial.summary["null"].null_count == 1
    assert result.model_dump_json() == before


def test_many_outputs_share_preview_limits() -> None:
    result = TableJobResult(
        job_id="job",
        outputs={f"table{i}": [{"value": i}] * 3 for i in range(30)},
        output_metadata={f"table{i}": TableOutput(columns=[]) for i in range(30)},
    )
    inspection = inspect(result)
    assert len(inspection.outputs) == 30
    for entry in inspection.outputs.values():
        assert isinstance(entry.preview, list)
        assert entry.metadata is not None
    assert sum(len(entry.preview or []) for entry in inspection.outputs.values()) == 5
    assert (
        sum(entry.truncation.omitted_rows for entry in inspection.outputs.values())
        == 85
    )
    assert (
        sum(entry.truncation.omitted_columns for entry in inspection.outputs.values())
        == 10
    )
    assert (
        sum(
            len(entry.metadata.summary) if entry.metadata else 0
            for entry in inspection.outputs.values()
        )
        == 20
    )


@pytest.mark.parametrize("large", ["value", "geometry", "metadata"])
def test_wire_budget_and_explicit_omission(large: str) -> None:
    value = {"value": {"nested": ["界" * 20000]}}
    declaration = TableOutput(columns=[column("value")])
    if large == "metadata":
        declaration.columns[0].description = "界" * 40000
    if large == "geometry":
        value = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"value": 1},
                    "geometry": {"type": "LineString", "coordinates": [[1, 2]] * 10000},
                }
            ],
        }
    else:
        value = [value]
    result = TableJobResult(
        job_id="job", outputs={"output": value}, output_metadata={"output": declaration}
    )
    before = result.model_dump_json()
    wire = serialize_result(inspect(result).model_dump(mode="json"), bounded=True)
    assert serialized_size(wire) <= 65536
    assert not wire.isError
    payload = wire.structuredContent
    assert payload is not None
    TerminalResultOutput.model_validate(payload)
    entry = payload["outputs"]["output"]
    assert entry["preview"] is None
    assert entry["truncation"]["omitted_rows"] == 1
    assert "preview" in entry["truncation"]["omitted_sections"]
    if large == "metadata":
        assert entry["metadata"] is None
        assert "metadata" in entry["truncation"]["omitted_sections"]
    assert isinstance(wire.content[0], TextContent)
    assert json.loads(wire.content[0].text) == payload
    assert result.model_dump_json() == before


def test_graph_failure_path_survives_inspection() -> None:
    error = {
        "message": "Acquisition failed",
        "node": "plugin.source",
        "dependency_path": ["plugin.root", "plugin.middle", "plugin.source"],
    }
    observation = inspect(FailedJobResult(job_id="job", error=error))
    assert observation.status == "failed"
    assert observation.outputs == {}
    assert observation.error == error


def test_summaries_use_observed_values_without_schema_coercion() -> None:
    result = TableJobResult(
        job_id="job",
        outputs={
            "output": [
                {"mixed": 3, "boolean": True, "number": -2, "null": None},
                {"mixed": "4", "boolean": False, "number": 1.5},
                {},
            ]
        },
        output_metadata={
            "output": TableOutput(columns=[column("mixed"), column("absent")])
        },
    )
    metadata = build_result_descriptor(result, completed_at=datetime.now(UTC)).outputs[
        "output"
    ]
    assert metadata.column_count == 5
    assert metadata.summary["number"].model_dump() == {
        "null_count": 1,
        "non_null_count": 2,
        "minimum": -2,
        "maximum": 1.5,
    }
    for name in ["mixed", "boolean"]:
        assert metadata.summary[name].model_dump() == {
            "null_count": 1,
            "non_null_count": 2,
            "minimum": None,
            "maximum": None,
        }
    for name in ["null", "absent"]:
        assert metadata.summary[name].null_count == 3
        assert metadata.summary[name].non_null_count == 0
        assert metadata.summary[name].minimum is None
        assert metadata.summary[name].maximum is None
    assert inspect(result).outputs["output"].preview == result.outputs["output"]
