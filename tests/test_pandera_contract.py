from datetime import date
from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.geopandas as ga
import pandera.pandas as pa
import pytest
from hypothesis import given, settings
from lyra.sdk import MetricResultError, PluginDefinition, metric
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.results import normalize_native_result, serialize_outputs
from pandera.config import config_context
from pandera.engines.geopandas_engine import Geometry
from pandera.engines.numpy_engine import String
from pandera.engines.pandas_engine import Date
from pandera.typing import Index, Series
from pandera.typing.geopandas import GeoSeries
from shapely.geometry import Point


def column(
    dtype: type | str | pa.DataType = int,
    *,
    checks: pa.Check | None = None,
    nullable: bool = False,
    coerce: bool = False,
    required: bool = True,
) -> pa.Column:
    return pa.Column(
        dtype,
        description="Value",
        metadata={"unit": None},
        checks=checks,
        nullable=nullable,
        coerce=coerce,
        required=required,
    )


class Values(pa.DataFrameModel):
    value: Series[int] = pa.Field(description="Value", metadata={"unit": None}, ge=0)


def plugin(schema: OutputSchema) -> PluginDefinition:
    @metric(name="values", description="Values", outputs={"values": schema})
    def values() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    return PluginDefinition(metrics=[values])


def test_model_and_object_equivalence_and_snapshot() -> None:
    schema = pa.DataFrameSchema({"value": column(checks=pa.Check.ge(0))})
    assert compile_output(Values)[1] == compile_output(schema)[1]
    definition = plugin(schema)
    schema.columns.clear()
    frame = pd.DataFrame({"value": [1]}, index=["private"])
    result = definition.normalize_result("values", frame)["values"]
    pd.testing.assert_frame_equal(result, frame.reset_index(drop=True))
    assert result is not frame


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"value": [-1]}),
        pd.DataFrame({"value": [1.0]}),
        pd.DataFrame({"extra": [1]}),
        pd.DataFrame({"value": [1], "extra": [2]}),
        pd.DataFrame([[1, 2]], columns=["value", "value"]),
    ],
)
def test_invalid_results_even_with_ambient_validation_disabled(
    frame: pd.DataFrame,
) -> None:
    with config_context(validation_enabled=False), pytest.raises(MetricResultError):
        plugin(Values).normalize_result("values", frame)


def test_coercion_nullable_and_empty() -> None:
    definition = plugin(
        pa.DataFrameSchema({"value": column("Int64", nullable=True, coerce=True)})
    )
    result = definition.normalize_result(
        "values", pd.DataFrame({"value": ["1", None]})
    )["values"]
    assert str(result.value.dtype) == "Int64"
    definition.normalize_result("values", result.iloc[:0])


@pytest.mark.parametrize(
    "schema",
    [
        pa.DataFrameSchema({"value": column(object)}),
        pa.DataFrameSchema({"value": column(required=False)}),
        pa.DataFrameSchema(
            {
                "value": pa.Column(
                    int, regex=True, description="Value", metadata={"unit": None}
                )
            }
        ),
        pa.DataFrameSchema({"value": pa.Column(int)}),
        pa.DataFrameSchema({"value": column()}, strict="filter"),
        pa.DataFrameSchema(
            {"value": column(checks=pa.Check.ge(0, raise_warning=True))}
        ),
    ],
)
def test_unsupported_declarations(schema: pa.DataFrameSchema) -> None:
    with pytest.raises(ValueError, match=r"Unsupported|Output|Column|Checks"):
        compile_output(schema)


def test_spatial_contract() -> None:
    definition = plugin(
        pa.DataFrameSchema({"shape": column("geometry", nullable=True)})
    )
    frame = gpd.GeoDataFrame({"shape": [Point(0, 0), None]}, geometry="shape", crs=3857)
    definition.normalize_result("values", frame)
    definition.normalize_result("values", frame.iloc[:0])
    frame = gpd.GeoDataFrame({"shape": [Point(0, 0), None]}, geometry="shape")
    with pytest.raises(MetricResultError, match="CRS"):
        definition.normalize_result("values", frame)


def test_infinity_and_lazy_diagnostics() -> None:
    with pytest.raises(MetricResultError, match="infinity"):
        plugin(pa.DataFrameSchema({"value": column(float)})).normalize_result(
            "values", pd.DataFrame({"value": [float("inf")]})
        )
    schema = pa.DataFrameSchema(
        {str(i): column(checks=pa.Check.ge(0)) for i in range(25)}
    )
    with pytest.raises(MetricResultError) as error:
        plugin(schema).normalize_result(
            "values", pd.DataFrame({str(i): [-1] for i in range(25)}), lazy=True
        )
    assert len(error.value.failures) == 20
    assert error.value.truncated
    assert error.value.__cause__ is not None


@settings(max_examples=10, deadline=None)
@given(Values.to_schema().strategy(size=5))
def test_generated_plugin_tables(frame: pd.DataFrame) -> None:
    plugin(Values).normalize_result("values", frame)


@pytest.mark.parametrize("values", [[float("inf")], [(1, 2)]])
def test_categorical_values_reject_unsupported_scalars(values: list) -> None:
    definition = plugin(pa.DataFrameSchema({"value": column("category")}))
    frame = pd.DataFrame({"value": pd.Categorical(values)})
    for candidate in (frame, frame.iloc[:0]):
        with pytest.raises(MetricResultError, match=r"infinity|unsupported"):
            definition.normalize_result("values", candidate)


@pytest.mark.parametrize(
    "index", [pa.Index(int), pa.Index(unique=True), pa.MultiIndex([pa.Index(int)])]
)
def test_explicit_indexes_rejected(index: pa.Index | pa.MultiIndex) -> None:
    with pytest.raises(ValueError, match="cannot declare indexes"):
        plugin(pa.DataFrameSchema({"value": column(int)}, index=index))


def test_model_index_rejected() -> None:
    class Indexed(Values):
        row: Index[int]

    with pytest.raises(ValueError, match="cannot declare indexes"):
        plugin(Indexed)


@pytest.mark.parametrize(
    "index",
    [
        pd.Index([8, 3], name="source"),
        pd.Index([1, 1]),
        pd.MultiIndex.from_tuples([(2, "b"), (1, "a")]),
    ],
)
def test_normalized_index_and_order(index: pd.Index) -> None:
    schema = pa.DataFrameSchema(
        {"key": column(int), "value": column(int)},
        ordered=True,
        unique=["key", "value"],
        checks=pa.Check(
            lambda frame: (
                frame.index.equals(pd.RangeIndex(len(frame)))
                and list(frame.columns) == ["key", "value"]
            ),
            name="normalized",
        ),
    )
    frame = pd.DataFrame({"value": [4, 3], "key": [1, 2]}, index=index)
    original = frame.copy(deep=True)
    result = plugin(schema).normalize_result("values", frame)["values"]
    pd.testing.assert_frame_equal(frame, original)
    pd.testing.assert_frame_equal(
        result, original[["key", "value"]].reset_index(drop=True)
    )
    with pytest.raises(MetricResultError):
        plugin(schema).normalize_result("values", pd.concat([frame, frame]))
    for invalid in [
        frame.drop(columns="key"),
        frame.assign(extra=1),
        pd.concat([frame, frame[["key"]]], axis=1),
    ]:
        for candidate in [invalid, invalid.iloc[:0]]:
            with pytest.raises(MetricResultError, match="exactly the declared columns"):
                plugin(schema).normalize_result("values", candidate)


def test_lazy_collects_multiple_outputs_atomically() -> None:
    @metric(name="pair", description="Pair", outputs={"one": Values, "two": Values})
    def pair() -> dict[str, pd.DataFrame]:
        return {
            "one": pd.DataFrame({"value": [-1]}),
            "two": pd.DataFrame({"value": [-2]}),
        }

    definition = PluginDefinition(metrics=[pair])
    with pytest.raises(MetricResultError) as error:
        definition.normalize_result("pair", pair(), lazy=True)
    assert {entry["output"] for entry in error.value.failures} == {"one", "two"}
    assert error.value.__cause__ is not None


@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "uint64",
        "float32",
        "Int64",
        "Float64",
        "boolean",
        "string",
        "datetime64[ns, UTC]",
    ],
)
def test_supported_dtype_metadata_and_empty_tables(dtype: str) -> None:
    schema = pa.DataFrameSchema({"value": column(dtype, nullable=True)})
    definition = plugin(schema)
    frame = pd.DataFrame({"value": pd.Series([], dtype=dtype)})
    definition.normalize_result("values", frame)
    assert compile_output(schema)[1].columns[0].dtype


def test_metadata_models_are_not_executable_declarations() -> None:
    metadata = compile_output(Values)[1]
    with pytest.raises(TypeError, match="Pandera"):
        compile_output(cast("OutputSchema", metadata))


def test_declared_categorical_infinity_rejected_at_registration() -> None:
    with pytest.raises(ValueError, match="finite"):
        compile_output(
            pa.DataFrameSchema(
                {"value": column(pa.Category(categories=[float("inf")]))}
            )
        )


def test_spatial_model_equivalence_and_crs_constraints() -> None:

    class Places(ga.DataFrameModel):
        shape: GeoSeries = pa.Field(
            description="Value", metadata={"unit": None}, nullable=True
        )

    schema = ga.DataFrameSchema({"shape": column("geometry", nullable=True)})
    assert compile_output(Places)[1] == compile_output(schema)[1]
    constrained = plugin(ga.DataFrameSchema({"shape": column(Geometry(crs=4326))}))
    frame = gpd.GeoDataFrame({"shape": [Point(1, 2)]}, geometry="shape", crs=3857)
    with pytest.raises(MetricResultError):
        constrained.normalize_result("values", frame)
    constrained.normalize_result("values", frame.to_crs(4326))
    with pytest.raises(ValueError, match="Only one"):
        compile_output(
            ga.DataFrameSchema({"a": column("geometry"), "b": column("geometry")})
        )


def test_explicit_helper_enforces_exact_columns_and_reports_column() -> None:

    schema = pa.DataFrameSchema({"value": column(float)})
    with pytest.raises(MetricResultError):
        normalize_native_result(
            "values", {"values": schema}, pd.DataFrame({"value": [1.0], "extra": [2]})
        )
    with pytest.raises(MetricResultError) as error:
        normalize_native_result(
            "values", {"values": schema}, pd.DataFrame({"value": [float("inf")]})
        )
    assert error.value.failures[0]["column"] == "value"
    assert error.value.failures[0]["output"] == "values"


def test_object_backed_date_and_string_serialization() -> None:

    definition = plugin(
        pa.DataFrameSchema({"date": column(Date), "text": column(String)})
    )
    frame = pd.DataFrame(
        {"date": [date(2026, 1, 2)], "text": pd.Series(["example"], dtype=object)}
    )
    encoded = serialize_outputs("values", definition.normalize_result("values", frame))
    assert encoded["values"] == [{"date": "2026-01-02T00:00:00.000", "text": "example"}]


def test_geometry_infinity_fails_before_public_serialization() -> None:
    definition = plugin(pa.DataFrameSchema({"shape": column("geometry")}))
    frame = gpd.GeoDataFrame(
        {"shape": [Point(float("inf"), 0)]}, geometry="shape", crs=4326
    )
    with pytest.raises(MetricResultError, match="infinity") as error:
        definition.normalize_result("values", frame)
    assert error.value.failures[0]["column"] == "shape"
