"""Explicit producer contracts and independent stored artifact authority."""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import Locations, read_location_artifact
from lyra.sdk.location_artifacts import encode
from lyra.sdk.models.location import location_snapshot
from shapely.geometry import Point

from tests.location_helpers import output_schema

if TYPE_CHECKING:
    from collections.abc import Callable


def frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"id": ["b", "a"], "count": pd.Series([1, None], dtype="Int64")},
        geometry=[Point(1, 2), Point(3, 4)],
        crs=4326,
        index=pd.Index([0, 1], name="ignored"),
    )


def test_schema_is_mandatory_and_payload_factories_are_absent() -> None:
    factory = cast("Callable[..., Locations]", Locations.from_geodataframe)
    with pytest.raises(TypeError, match="schema"):
        factory(frame(), collection="test:units", unit_type="point")
    with pytest.raises(TypeError, match="LocationOutputSchema"):
        factory(frame(), collection="test:units", unit_type="point", schema=None)
    assert not hasattr(Locations, "from_dict")
    assert not hasattr(Locations, "from_json")


def test_declared_types_retained_without_mutation() -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    source = frame()[["geometry", "count", "id"]]
    original = source.copy(deep=True)
    value = Locations.from_geodataframe(
        source, schema=schema, collection="test:units", unit_type="point"
    )
    pd.testing.assert_frame_equal(source, original)
    stored = location_snapshot(value).table.schema
    assert stored is not None
    assert stored.names == ["id", "count"]
    assert str(stored.field("count").type) == "int64"
    assert stored.field("count").nullable
    assert location_snapshot(value.slice(0, 0)).table.schema == stored
    assert location_snapshot(value.select_ids(["a"])).table.schema == stored
    source.loc[0, "count"] = 99
    assert value.property("count") == [1, None]


@pytest.mark.parametrize("empty", [False, True])
def test_exact_columns_required_even_when_empty(*, empty: bool) -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    source = frame().iloc[:0] if empty else frame()
    for invalid in [source.drop(columns="count"), source.assign(extra=1)]:
        with pytest.raises(ValueError, match="declared columns"):
            Locations.from_geodataframe(
                invalid, schema=schema, collection="test:units", unit_type="point"
            )


def test_standalone_artifact_read_needs_no_producer_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = True
    value = schema.build(frame(), collection="test:units", unit_type="point")
    path = tmp_path / "locations.arrow"
    path.write_bytes(encode(location_snapshot(value)))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Artifact restoration must not construct a producer value")

    monkeypatch.setattr(Locations, "from_geodataframe", forbidden)
    assert read_location_artifact(path).to_locations() == value


@pytest.mark.parametrize("boundary", ["build", "validate"])
@pytest.mark.parametrize("nullable", [False, True])
def test_nullability_after_schema_processing(boundary: str, *, nullable: bool) -> None:
    schema = output_schema(count="Int64")
    schema.schema.columns["count"].nullable = nullable
    source = frame().fillna({"count": 2})
    original = source.copy(deep=True)
    value = schema.build(source, collection="test:units", unit_type="point")

    def introduce_null(result: pd.DataFrame) -> bool:
        result.loc[0, "count"] = None
        return True

    schema.schema.checks = [pa.Check(introduce_null)]

    def construct() -> Locations:
        if boundary == "validate":
            return schema.validate(value)
        return schema.build(source, collection="test:units", unit_type="point")

    if nullable:
        assert list(construct().property("count")) == [None, 2]
    else:
        with pytest.raises(ValueError, match="not nullable"):
            construct()
    pd.testing.assert_frame_equal(source, original)
    assert list(value.property("count")) == [1, 2]
