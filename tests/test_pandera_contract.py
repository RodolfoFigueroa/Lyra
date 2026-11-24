from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.geopandas as ga
import pandera.pandas as pa
import pytest
from hypothesis import given, settings
from pandera.api.pandas.model_config import BaseConfig
from pandera.config import config_context
from pandera.engines.geopandas_engine import Geometry
from pandera.engines.numpy_engine import String
from pandera.typing import Index, Series
from pandera.typing.geopandas import GeoSeries
from shapely.geometry import Point

from lyra.contracts.errors import MetricResultError
from lyra.data.results import serialize_outputs
from lyra.runtime.invocation import BoundPlugin
from lyra.sdk.output_schema import OutputSchema, compile_output
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.results import normalize_native_result


def column(
    dtype: object = int,
    *,
    checks: pa.Check | None = None,
    nullable: bool = False,
    required: bool = True,
) -> pa.Column:
    if dtype == "geometry":
        dtype = Geometry(crs=4326)
    if isinstance(dtype, Geometry) and checks is None:
        checks = pa.Check(lambda s: s.is_valid, name="valid_geometry")
    return pa.Column(
        dtype,
        description="Value",
        metadata={"unit": None},
        checks=checks,
        nullable=nullable,
        required=required,
    )


class StrictConfig(BaseConfig):
    """Require complete column contracts."""

    strict = True


class Values(pa.DataFrameModel):
    Config: type[BaseConfig] = StrictConfig

    value: Series[int] = pa.Field(description="Value", metadata={"unit": None}, ge=0)


def plugin(schema: OutputSchema) -> BoundPlugin:
    @metric(
        code_revision="1",
        name="values",
        description="Values",
        outputs={"values": schema},
    )
    def values() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    return BoundPlugin(PluginDefinition(metrics=[values]))


def test_model_and_object_equivalence_and_snapshot() -> None:
    schema = pa.DataFrameSchema({"value": column(checks=pa.Check.ge(0))}, strict=True)
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


def test_nullable_and_empty() -> None:
    definition = plugin(
        pa.DataFrameSchema({"value": column("Int64", nullable=True)}, strict=True)
    )
    result = definition.normalize_result(
        "values", pd.DataFrame({"value": pd.array([1, None], dtype="Int64")})
    )["values"]
    assert str(result.value.dtype) == "Int64"
    definition.normalize_result("values", result.iloc[:0])


@pytest.mark.parametrize(
    "schema",
    [
        pa.DataFrameSchema({"value": column(object)}, strict=True),
        pa.DataFrameSchema({"value": column(required=False)}, strict=True),
        pa.DataFrameSchema(
            {
                "value": pa.Column(
                    int, regex=True, description="Value", metadata={"unit": None}
                )
            },
            strict=True,
        ),
        pa.DataFrameSchema({"value": pa.Column(int)}, strict=True),
        pa.DataFrameSchema({"value": column()}, strict="filter"),
        pa.DataFrameSchema(
            {"value": column(checks=pa.Check.ge(0, raise_warning=True))}, strict=True
        ),
    ],
)
def test_unsupported_declarations(schema: pa.DataFrameSchema) -> None:
    with pytest.raises(ValueError, match=r"Unsupported|Output|Column|Checks"):
        compile_output(schema)


@pytest.mark.parametrize("geometries", [[Point(0, 0), None], [None], []])
def test_spatial_contract(geometries: list[Point | None]) -> None:
    definition = plugin(
        pa.DataFrameSchema({"geometry": column("geometry", nullable=True)}, strict=True)
    )
    frame = gpd.GeoDataFrame({"geometry": geometries}, geometry="geometry")
    declared = frame.set_crs(4326)
    accepted = definition.normalize_result("values", declared)["values"]
    pd.testing.assert_frame_equal(accepted, declared)
    for invalid in (frame, frame.set_crs(3857)):
        with pytest.raises(MetricResultError, match="CRS"):
            definition.normalize_result("values", invalid)


def test_infinity_and_lazy_diagnostics() -> None:
    with pytest.raises(MetricResultError, match="infinity"):
        plugin(
            pa.DataFrameSchema({"value": column(float)}, strict=True)
        ).normalize_result("values", pd.DataFrame({"value": [float("inf")]}))
    schema = pa.DataFrameSchema(
        {str(i): column(checks=pa.Check.ge(0)) for i in range(25)}, strict=True
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
    with pytest.raises(ValueError, match="Unsupported output dtype"):
        compile_output(
            pa.DataFrameSchema(
                {"value": column(pd.CategoricalDtype(values))}, strict=True
            )
        )


@pytest.mark.parametrize(
    "index", [pa.Index(int), pa.Index(unique=True), pa.MultiIndex([pa.Index(int)])]
)
def test_explicit_indexes_rejected(index: pa.Index | pa.MultiIndex) -> None:
    with pytest.raises(ValueError, match="cannot declare indexes"):
        plugin(pa.DataFrameSchema({"value": column(int)}, strict=True, index=index))


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
        strict=True,
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
    @metric(
        code_revision="1",
        name="pair",
        description="Pair",
        outputs={"one": Values, "two": Values},
    )
    def pair() -> dict[str, pd.DataFrame]:
        return {
            "one": pd.DataFrame({"value": [-1]}),
            "two": pd.DataFrame({"value": [-2]}),
        }

    definition = BoundPlugin(PluginDefinition(metrics=[pair]))
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
    schema = pa.DataFrameSchema({"value": column(dtype, nullable=True)}, strict=True)
    if dtype in {"int8", "uint64", "float32", "datetime64[ns, UTC]"}:
        with pytest.raises(ValueError, match="Unsupported output dtype"):
            compile_output(schema)
        return
    definition = plugin(schema)
    frame = pd.DataFrame({"value": pd.Series([], dtype=dtype)})
    definition.normalize_result("values", frame)
    assert compile_output(schema)[1].columns[0].dtype


def test_metadata_models_are_not_executable_declarations() -> None:
    metadata = compile_output(Values)[1]
    with pytest.raises(TypeError, match="Pandera"):
        compile_output(cast("OutputSchema", metadata))


def test_declared_categorical_infinity_rejected_at_registration() -> None:
    with pytest.raises(ValueError, match="Unsupported output dtype"):
        compile_output(
            pa.DataFrameSchema(
                {"value": column(pa.Category(categories=[float("inf")]))}, strict=True
            )
        )


def test_spatial_model_equivalence_and_crs_constraints() -> None:

    class Places(ga.DataFrameModel):
        Config: type[BaseConfig] = StrictConfig
        geometry: GeoSeries = pa.Field(
            description="Value",
            metadata={"unit": None},
            nullable=True,
            dtype_kwargs={"crs": 4326},
        )

        @pa.check("geometry", name="valid_geometry")
        @classmethod
        def valid_geometry(cls, series: GeoSeries) -> pd.Series:
            return series.is_valid

    schema = ga.DataFrameSchema(
        {"geometry": column("geometry", nullable=True)}, strict=True
    )
    assert compile_output(Places)[1] == compile_output(schema)[1]
    constrained = plugin(
        ga.DataFrameSchema({"geometry": column(Geometry(crs=4326))}, strict=True)
    )
    frame = gpd.GeoDataFrame({"geometry": [Point(1, 2)]}, geometry="geometry", crs=3857)
    with pytest.raises(MetricResultError):
        constrained.normalize_result("values", frame)
    constrained.normalize_result("values", frame.to_crs(4326))
    with pytest.raises(ValueError, match="geometry"):
        compile_output(
            ga.DataFrameSchema(
                {"a": column("geometry"), "b": column("geometry")}, strict=True
            )
        )


def test_explicit_helper_enforces_exact_columns_and_reports_column() -> None:

    schema = pa.DataFrameSchema({"value": column(float)}, strict=True)
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
        pa.DataFrameSchema({"date": column(str), "text": column(String)}, strict=True)
    )
    frame = pd.DataFrame(
        {"date": ["2026-01-02"], "text": pd.Series(["example"], dtype=object)}
    )
    encoded = serialize_outputs("values", definition.normalize_result("values", frame))
    assert encoded["values"] == [{"date": "2026-01-02", "text": "example"}]


@pytest.mark.parametrize("value", [17, ["nested"]])
def test_numpy_string_metric_rejects_nonstring_object_cells(value: object) -> None:
    definition = plugin(pa.DataFrameSchema({"label": column(String)}, strict=True))
    frame = pd.DataFrame({"label": pd.Series([value], dtype=object)})
    with pytest.raises(MetricResultError, match="label"):
        definition.normalize_result("values", frame)


def test_geometry_infinity_fails_before_public_serialization() -> None:
    definition = plugin(
        pa.DataFrameSchema({"geometry": column("geometry")}, strict=True)
    )
    frame = gpd.GeoDataFrame(
        {"geometry": [Point(float("inf"), 0)]}, geometry="geometry", crs=4326
    )
    with pytest.raises(MetricResultError) as error:
        definition.normalize_result("values", frame)
    assert error.value.failures[0]["column"] == "geometry"
