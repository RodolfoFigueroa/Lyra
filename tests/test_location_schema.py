from datetime import date

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import LocationOutputSchema, Locations, MetricResultError
from lyra.sdk.output_schema import OutputSchema
from pandera.config import config_context
from pandera.engines.geopandas_engine import Geometry
from pandera.engines.pandas_engine import Date
from pandera.typing import Series
from pandera.typing.geopandas import GeoSeries
from pydantic import ValidationError


def column(
    dtype: object,
    *,
    checks: pa.Check | None = None,
    nullable: bool = False,
    coerce: bool = False,
) -> pa.Column:
    return pa.Column(
        dtype,
        description="Column",
        metadata={"unit": None},
        checks=checks,
        nullable=nullable,
        coerce=coerce,
    )


def declaration(**properties: pa.Column) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"key": column(str), "shape": column(Geometry), **properties}, ordered=True
    )


def locations(*properties: dict) -> Locations:
    return Locations.model_validate(
        {
            "collection": "example:places",
            "unit_type": "point",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(index),
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                    "properties": values,
                }
                for index, values in enumerate(properties)
            ],
        }
    )


def contract(schema: OutputSchema) -> LocationOutputSchema:
    return LocationOutputSchema(schema, id_column="key", geometry_column="shape")


class Places(pa.DataFrameModel):
    key: Series[str] = pa.Field(description="Column", metadata={"unit": None})
    shape: GeoSeries = pa.Field(description="Column", metadata={"unit": None})
    count: Series[pd.Int64Dtype] = pa.Field(
        description="Column", metadata={"unit": None}, nullable=True
    )


@pytest.mark.parametrize(
    "schema", [Places, declaration(count=column("Int64", nullable=True))]
)
def test_model_and_schema_metadata_snapshot_and_validation(
    schema: OutputSchema,
) -> None:
    compiled = contract(schema)
    value = locations({"count": 1}, {"count": None})
    assert compiled.validate(value) == value
    assert compiled.validate(value) is not value
    assert compiled.metadata.id_column == "key"
    assert compiled.metadata.output.geometry == "shape"
    assert [c.name for c in compiled.metadata.output.columns] == [
        "key",
        "shape",
        "count",
    ]
    assert compiled.metadata.model_dump(mode="json")["output"]["columns"][2]["nullable"]
    if isinstance(schema, pa.DataFrameSchema):
        schema.columns.clear()
    assert compiled.validate(value) == value


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        *[
            (dtype, [1, 2])
            for dtype in [
                "int8",
                "int16",
                "int32",
                "int64",
                "uint8",
                "uint16",
                "uint32",
                "uint64",
            ]
        ],
        *[
            (dtype, [1, None])
            for dtype in [
                "Int8",
                "Int16",
                "Int32",
                "Int64",
                "UInt8",
                "UInt16",
                "UInt32",
                "UInt64",
            ]
        ],
        *[
            (dtype, [1.5, None])
            for dtype in ["float16", "float32", "float64", "Float32", "Float64"]
        ],
        ("bool", [True, False]),
        ("boolean", [True, None]),
        ("str", ["a", None]),
        ("string", ["a", None]),
        ("category", ["a", None]),
        (pd.CategoricalDtype(["a", "b"], ordered=True), ["a", None]),
        (pd.CategoricalDtype([date(2026, 1, 1)]), ["2026-01-01", None]),
        (
            pd.CategoricalDtype([pd.Timestamp("2026-01-01T00:00:00")]),
            ["2026-01-01T00:00:00", None],
        ),
        (Date, ["2026-01-01", None]),
        ("datetime64[ns]", ["2026-01-01T12:30:00", None]),
        (pd.DatetimeTZDtype(tz="UTC"), ["2026-01-01T12:30:00+00:00", None]),
    ],
)
def test_supported_dtype_reconstruction_and_empty(dtype: object, values: list) -> None:
    compiled = contract(declaration(value=column(dtype, nullable=True)))
    value = locations(*[{"value": item} for item in values])
    assert compiled.validate(value) == value
    assert compiled.validate(locations()) == locations()


@pytest.mark.parametrize("values", [{}, {"count": 1, "extra": 2}])
def test_strict_properties_on_every_row(values: dict) -> None:
    compiled = contract(declaration(count=column("Int64", nullable=True)))
    with pytest.raises(ValueError, match="properties"):
        compiled.validate(locations({"count": 1}, values))


@pytest.mark.parametrize("schema_coerce", [False, True])
def test_explicit_coercion_is_returned_without_mutating_input(
    *,
    schema_coerce: bool,
) -> None:
    schema = declaration(count=column("Int64", coerce=not schema_coerce))
    schema.coerce = schema_coerce
    value = locations({"count": "2"})
    result = contract(schema).validate(value)
    assert result.features[0].properties == {"count": 2}
    assert value.features[0].properties == {"count": "2"}


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        ("int8", 256),
        ("int64", 1.5),
        ("int64", "2"),
        ("bool", 1),
        ("string", 1),
        (pd.CategoricalDtype(["a"]), "b"),
    ],
)
def test_reconstruction_does_not_silently_change_values(
    dtype: object, value: object
) -> None:
    with pytest.raises(ValueError, match="column"):
        contract(declaration(value=column(dtype, nullable=True))).validate(
            locations({"value": value})
        )


def test_checks_apply_to_populated_and_empty_values_despite_global_settings() -> None:
    schema = declaration(count=column(int, checks=pa.Check.ge(0)))
    schema.checks = [pa.Check(lambda frame: len(frame) > 0, name="nonempty")]
    with config_context(validation_enabled=False):
        for value in [locations(), locations({"count": -1})]:
            with pytest.raises(MetricResultError):
                contract(schema).validate(value, lazy=True)


@pytest.mark.parametrize("mutation", ["duplicate", "geometry", "id", "property"])
def test_shared_invariants_rechecked_on_existing_instances(mutation: str) -> None:
    value = locations({}, {})
    if mutation == "duplicate":
        value.features[1].id = value.features[0].id
    elif mutation == "geometry":
        geometry = value.features[0].geometry
        assert geometry.type == "Point"
        geometry.coordinates[0] = float("inf")
    elif mutation == "id":
        value.features[0].id = ""
    else:
        value.features[0].properties["x"] = []
    with pytest.raises(ValidationError):
        contract(declaration()).validate(value)


def test_coercion_cannot_invalidate_identity() -> None:
    schema = declaration()
    schema.columns["key"] = column(int, coerce=True)
    with pytest.raises((ValueError, TypeError), match="id"):
        contract(schema).validate(locations({}))


@pytest.mark.parametrize(
    ("id_column", "geometry_column"),
    [("absent", "shape"), ("key", "absent"), ("shape", "shape"), ("shape", "key")],
)
def test_structural_columns_must_be_declared(
    id_column: str, geometry_column: str
) -> None:
    with pytest.raises(ValueError, match="distinct ID and geometry"):
        LocationOutputSchema(
            declaration(), id_column=id_column, geometry_column=geometry_column
        )


@pytest.mark.parametrize("mutation", ["duplicate", "geometry"])
def test_invariants_are_checked_after_pandera_processing(mutation: str) -> None:
    def alter_frame(frame: pd.DataFrame) -> bool:
        if mutation == "duplicate":
            frame["key"] = "same"
        else:
            frame.loc[0, "shape"] = None
        return True

    schema = declaration()
    schema.checks = [pa.Check(alter_frame, name="alter_frame")]
    with pytest.raises((ValueError, TypeError), match=r"unique|geometry"):
        contract(schema).validate(locations({}, {}))


@pytest.mark.parametrize("coerce", [False, True])
def test_geometry_crs_check_is_preserved(*, coerce: bool) -> None:
    schema = declaration()
    schema.columns["shape"] = column(Geometry(crs="EPSG:27700"), coerce=coerce)
    with pytest.raises(ValueError, match="CRS"):
        contract(schema).validate(locations({}))


def test_snapshot_does_not_change_when_author_edits_declaration() -> None:
    schema = declaration(count=column(int))
    compiled = contract(schema)
    schema.columns.clear()
    assert compiled.validate(locations({"count": 1})).features[0].properties == {
        "count": 1
    }


def test_temporal_reconstruction_does_not_introduce_missing_values() -> None:
    with pytest.raises(ValueError, match="explicit coercion"):
        contract(declaration(value=column("datetime64[ns]", nullable=True))).validate(
            locations({"value": "NaT"})
        )


@pytest.mark.parametrize("coerce", [False, True])
def test_temporal_parsing_requires_explicit_coercion(*, coerce: bool) -> None:
    compiled = contract(declaration(value=column("datetime64[ns]", coerce=coerce)))
    value = locations({"value": "2026"})
    if coerce:
        result = compiled.validate(value)
        assert result.features[0].properties == {"value": "2026-01-01T00:00:00"}
        assert value.features[0].properties == {"value": "2026"}
    else:
        with pytest.raises(ValueError, match="explicit coercion"):
            compiled.validate(value)


def test_temporal_categories_are_reconstructed_with_explicit_coercion() -> None:
    schema = declaration(
        value=column(pd.CategoricalDtype([date(2026, 1, 1)]), coerce=True)
    )
    value = locations({"value": "2026-01-01"})
    assert contract(schema).validate(value) == value


def test_default_column_names() -> None:
    schema = pa.DataFrameSchema({"id": column(str), "geometry": column(Geometry)})
    value = locations({})
    assert LocationOutputSchema(schema).validate(value) == value
