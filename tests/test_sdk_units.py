"""Column units have the same meaning across declarations and serialized metadata."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pandera.pandas as pa
import pint
import pytest
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate
from pandera.typing import Series
from pydantic import ValidationError

from lyra.contracts.inspection import TerminalResultOutput
from lyra.contracts.jobs import JobStatusInfo, TableJobResult
from lyra.contracts.outputs import ColumnMetadata
from lyra.contracts.plugins import PluginManifest
from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.sdk.output_schema import compile_output
from lyra.sdk.storage_schema import describe, prepare_native
from lyra.server.result_projection import project_observation
from lyra.server.results import ResultService
from lyra.server.routes import jobs
from test_support.location_schema import StrictConfig
from tests.client_helpers import _result_descriptor_response
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.job_id_helpers import CLIENT_JOB_ID
from tests.provenance_helpers import result_provenance
from tests.result_helpers import result_observation

BASE = {"name": "value", "dtype": "float64", "description": "Test quantity."}


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("km", "kilometer"),
        ("kilometres", "kilometer"),
        ("m**2", "meter ** 2"),
        ("count/km**2", "count / kilometer ** 2"),
        ("%", "percent"),
        ("degC", "degree_Celsius"),
        ("joule", "joule"),
        ("dimensionless", "dimensionless"),
        (None, None),
    ],
)
def test_units_round_trip_through_python_json_schema_and_manifest(
    unit: str | None, expected: str | None
) -> None:
    raw = {**BASE, "unit": unit}
    column = ColumnMetadata.model_validate(raw)
    assert column.unit == expected
    assert ColumnMetadata.model_validate_json(json.dumps(raw)) == column
    assert column.model_dump(mode="json")["unit"] == expected
    payload = plugin_manifest(metric_manifest())
    payload["nodes"][0]["outputs"]["light_metric"]["columns"] = [raw]
    manifest = PluginManifest.model_validate(payload)
    serialized = manifest.model_dump_json()
    assert (
        json.loads(serialized)["nodes"][0]["outputs"]["light_metric"]["columns"][0][
            "unit"
        ]
        == expected
    )
    assert PluginManifest.model_validate_json(serialized) == manifest


def declaration(metadata: dict[str, object]) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"value": pa.Column("Float64", description="Measurement", metadata=metadata)},
        strict=True,
    )


@pytest.mark.parametrize(
    "unit",
    [
        "",
        "  ",
        "unknown_unit",
        "m**",
        "(",
        "m+m",
        "2 km",
        1,
        b"km",
        pint.Unit("km"),
        2 * pint.Unit("m"),
    ],
)
def test_invalid_units_fail_at_declaration_and_contract_boundaries(
    unit: object,
) -> None:
    with pytest.raises(ValidationError, match="unit"):
        ColumnMetadata.model_validate({**BASE, "unit": unit})
    with pytest.raises(ValueError, match="unit"):
        compile_output(declaration({"unit": unit}))
    with pytest.raises(ValueError, match="unit"):
        describe(declaration({"unit": unit}))


def test_unit_is_required_even_when_not_applicable() -> None:
    with pytest.raises(ValidationError, match="unit"):
        ColumnMetadata.model_validate(BASE)
    with pytest.raises(ValueError, match="unit"):
        compile_output(declaration({}))
    with pytest.raises(ValueError, match="unit"):
        describe(declaration({}))


def test_editing_column_metadata_preserves_the_unit_contract() -> None:
    column = ColumnMetadata.model_validate({**BASE, "unit": None})
    column.unit = "km"
    assert column.model_dump(mode="json")["unit"] == "kilometer"
    with pytest.raises(ValidationError, match="unit"):
        column.unit = "unknown_unit"
    assert column.unit == "kilometer"


def test_json_schema_describes_required_string_or_null() -> None:
    for schema in (
        ColumnMetadata.model_json_schema(),
        ColumnMetadata.model_json_schema(mode="serialization"),
    ):
        for unit in ("outside_the_catalog", None):
            validate({**BASE, "unit": unit}, schema)
        for raw in (BASE, {**BASE, "unit": 1}):
            with pytest.raises(SchemaValidationError):
                validate(raw, schema)


def test_model_and_schema_compile_independent_normalized_metadata() -> None:
    class Area(pa.DataFrameModel):
        Config = StrictConfig
        value: Series[pd.Float64Dtype] = pa.Field(
            description="Measurement", metadata={"unit": "m**2", "source": "survey"}
        )

    source = Area.to_schema()
    for declared in (Area, source):
        compiled, output = compile_output(declared)
        assert output.columns[0].unit == "meter ** 2"
        assert compiled.columns["value"].metadata == {
            "unit": "meter ** 2",
            "source": "survey",
        }
    assert source.columns["value"].metadata == {"unit": "m**2", "source": "survey"}


@pytest.mark.parametrize(
    ("unit", "expected", "value"),
    [
        ("km", "kilometer", 2.5),
        ("%", "percent", 50.0),
        ("degC", "degree_Celsius", -10.0),
    ],
)
def test_unit_scale_and_dtype_survive_storage(
    unit: str, expected: str, value: float
) -> None:
    schema = declaration({"unit": unit})
    frame = pd.DataFrame({"value": pd.Series([value], dtype="Float64")})
    validated, layout = prepare_native(frame, schema)
    pd.testing.assert_frame_equal(validated, frame)
    assert layout.units == describe(schema).units == (expected,)
    restored = frame_from_rows(rows_from_validated(validated, layout), layout)
    pd.testing.assert_frame_equal(restored, frame)


@pytest.mark.parametrize("column_type", ["int64", "float64", "str", "bool"])
def test_null_unit_is_independent_of_column_type(column_type: str) -> None:
    column = ColumnMetadata.model_validate({**BASE, "dtype": column_type, "unit": None})
    assert column.unit is None
    assert column.nullable is False


@pytest.mark.parametrize(
    ("unit", "expected"), [("count/ha", "count / hectare"), (None, None)]
)
def test_rest_result_descriptor_preserves_unit(
    unit: str | None,
    expected: str | None,
) -> None:

    payload = _result_descriptor_response()
    declaration = payload["outputs"]["result"]["metadata"]["declaration"]
    declaration["columns"][0]["unit"] = unit
    provenance = result_provenance(
        {
            "metric": "heavy_metric",
            "outputs": {"result": declaration},
            "plugin": {"name": "fixture", "version": "1"},
        }
    )
    observation = result_observation(
        snapshot=JobStatusInfo(
            job_id=CLIENT_JOB_ID,
            status="succeeded",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        result=TableJobResult(
            job_id=CLIENT_JOB_ID,
            outputs={"result": []},
            output_metadata={"result": declaration},
            provenance=provenance,
        ),
    )
    service = Mock(spec=ResultService)
    service.observe_job = AsyncMock(return_value=observation)
    service.inspect.side_effect = lambda item: project_observation(
        item.snapshot.job_id, item
    )
    response = asyncio.run(jobs.inspect_job_result(CLIENT_JOB_ID, service))
    wire = json.loads(bytes(response.body))
    assert (
        wire["outputs"]["result"]["metadata"]["declaration"]["columns"][0]["unit"]
        == expected
    )
    TerminalResultOutput.model_validate(wire)
