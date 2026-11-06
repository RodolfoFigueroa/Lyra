import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pyarrow as pa
import pytest
from shapely.geometry import Point

from lyra.data import arrow_conversion
from lyra.data.typed_storage import Snapshot
from lyra.sdk import storage_schema
from test_support.imports import assert_fresh_imports
from test_support.location_helpers import output_schema


def test_native_storage_without_pandera() -> None:
    assert_fresh_imports(
        [
            "lyra.data.arrow_conversion",
            "lyra.data.typed_storage",
            "lyra.data.location_snapshot",
        ],
        blocked=("pandera",),
        assertions="""
        import pandas as pd
        import geopandas as gpd
        import pyarrow as pa
        from shapely.geometry import Point
        from lyra.data.arrow_conversion import to_arrow, from_arrow
        from lyra.data.typed_storage import Snapshot
        from lyra.data.location_snapshot import LocationSnapshot
        schema = pa.schema([pa.field('id', pa.string(), nullable=False)])
        frame = pd.DataFrame({'id': ['a']})
        assert from_arrow(to_arrow(frame, schema)).equals(frame)
        assert Snapshot.from_frame(frame, schema).to_frame().equals(frame)
        spatial = gpd.GeoDataFrame(frame, geometry=[Point(1, 2)], crs=4326)
        value = LocationSnapshot.from_locations(
            spatial, schema, collection='places', unit_type='point'
        )
        assert value.table.num_rows == value.geometry.num_rows == 1
        """,
    )


@pytest.mark.parametrize("rows", [0, 3])
def test_native_zero_columns(rows: int) -> None:
    frame = pd.DataFrame(index=range(rows))
    snapshot = Snapshot.from_frame(frame, pa.schema([]))
    assert snapshot.table.num_rows == rows
    assert snapshot.to_frame().shape == (rows, 0)
    assert snapshot.to_frame().index.equals(pd.RangeIndex(rows))


def test_native_nullability_and_finite_values() -> None:
    schema = pa.schema([pa.field("value", pa.float64(), nullable=False)])
    for value, message in [(None, "nullable"), (float("inf"), "infinity")]:
        with pytest.raises(ValueError, match=message):
            arrow_conversion.to_arrow(pd.DataFrame({"value": [value]}), schema)


def test_adapter_checks_once_and_preserves_coercion() -> None:
    calls = []

    def check(frame: pd.DataFrame) -> bool:
        calls.append(len(frame))
        return True

    declaration = pandera.DataFrameSchema(
        {"value": pandera.Column("int8", coerce=True)},
        checks=pandera.Check(check),
    )
    source = pd.DataFrame({"value": ["2"]})
    prepared = storage_schema.prepare(source, declaration)
    snapshot = Snapshot.from_frame(
        prepared.frame, prepared.schema, columns=prepared.columns
    )
    assert calls == [1]
    assert snapshot.to_frame().value.dtype == "int8"
    assert source.value.tolist() == ["2"]


def test_location_build_and_revalidation_check_once() -> None:
    calls = []

    def check(frame: pd.DataFrame) -> bool:
        calls.append(len(frame))
        return True

    schema = output_schema(value="Int64")
    schema.schema.checks = [pandera.Check(check, name="count_checks")]
    frame = gpd.GeoDataFrame(
        {"id": ["a"], "value": pd.Series([2], dtype="Int64")},
        geometry=[Point(1, 2)],
        crs=4326,
    )
    value = schema.build(frame, collection="test:places", unit_type="point")
    assert calls == [1]
    schema.validate(value)
    assert calls == [1, 1]


def test_native_constructor_rejects_extra_columns() -> None:
    with pytest.raises(ValueError, match="declared columns"):
        Snapshot.from_frame(
            pd.DataFrame({"value": [1], "extra": [2]}),
            pa.schema([("value", pa.int64())]),
        )
