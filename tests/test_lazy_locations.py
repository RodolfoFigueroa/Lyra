from collections.abc import Iterable
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pandas as pd
import pytest
from lyra.sdk import (
    MISSING,
    Locations,
    content_fingerprints,
    geometry_buffers,
    property_buffers,
)
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import (
    location_fingerprint,
    location_snapshot,
    locations_from_snapshot,
)


def value() -> Locations:
    return Locations.from_dict(
        {
            "collection": "test:units",
            "unit_type": "point",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [
                {
                    "type": "Feature",
                    "id": str(i),
                    "geometry": {"type": "Point", "coordinates": [i, -0.0]},
                    "properties": row,
                }
                for i, row in enumerate(
                    [
                        {},
                        {"v": None},
                        {"v": 2**100},
                        {"v": True},
                        {"v": -0.0},
                    ]
                )
            ],
        }
    )


def test_metadata_and_projection_never_export_rows() -> None:
    source = value()
    with (
        patch.object(geometry_buffers, "geometry_rows", side_effect=AssertionError),
        patch.object(property_buffers, "property_data", side_effect=AssertionError),
        patch.object(LocationSnapshot, "to_features", side_effect=AssertionError),
    ):
        assert len(source) == 5
        assert source
        assert not source.is_empty
        assert "count=5" in repr(source)
        assert source.collection == "test:units"
        assert source.unit_type == "point"
        assert source.crs.properties.name == "EPSG:4326"
        assert source.property_names == ("v",)
        assert source.ids[-1] == "4"
        assert list(source.ids[::-2]) == ["4", "2", "0"]
        assert source.property("v")[0] is MISSING
        assert source.property("v")[1] is None
        assert type(source.property("v")[3]) is bool
        assert source.property("v")[2] == 2**100
        frame = source.to_dataframe()
        assert frame.v.dtype == object
        assert frame.v[0] is pd.NA
        assert frame.v[1] is None
        assert list(source.to_dataframe(properties=[])) == ["id"]
        assert source.slice(0, 0).is_empty
    with pytest.raises(FrozenInstanceError):
        setattr(source, "_" + "snapshot", None)
    assert not isinstance(source, Iterable)
    with pytest.raises(IndexError):
        source.ids[5]


def test_selection_ownership_identity_and_caching() -> None:
    source = value()
    snapshot = location_snapshot(source)
    with patch.object(
        content_fingerprints, "compute", wraps=content_fingerprints.compute
    ) as compute:
        selected = source.slice(1, 3).select_ids(["3", "1"]).slice(1)
        assert compute.call_count == 0
        assert list(selected.ids) == ["1"]
        expected = Locations.from_dict(
            source.to_dict() | {"features": source.to_dict()["features"][1:2]}
        )
        assert compute.call_count == 1
        assert selected == expected
        assert compute.call_count == 2
        assert location_fingerprint(selected) == location_fingerprint(expected)
        assert compute.call_count == 2
    shared = location_snapshot(source.slice(1, 2))
    assert (
        shared.properties.column(0).chunk(0).buffers()[1].address
        == snapshot.properties.column(0).chunk(0).buffers()[1].address
    )
    assert memoryview(shared.properties.column(0).chunk(0).buffers()[1]).readonly
    assert source.select_ids([]).property_names == ("v",)
    assert source.slice(999).is_empty
    with pytest.raises(KeyError):
        source.select_ids(["missing"])
    for ids in [["1", "1"], [""], [True]]:
        with pytest.raises(ValueError, match="Selection IDs"):
            source.select_ids(ids)
    with pytest.raises(TypeError):
        locations_from_snapshot(object.__new__(LocationSnapshot))


def test_bounded_feature_iteration_and_exports() -> None:
    source = value()
    with patch.object(
        geometry_buffers, "geometry_rows", wraps=geometry_buffers.geometry_rows
    ) as decode:
        iterator = source.iter_features(batch_size=2, limit=3)
        assert decode.call_count == 0
        first = next(iterator)
        assert decode.call_args.args[0].num_rows == 2
        first.properties["changed"] = True
        next(iterator)
        assert decode.call_count == 1
        next(iterator)
        assert decode.call_args.args[0].num_rows == 1
        assert decode.call_count == 2
        with pytest.raises(StopIteration):
            next(iterator)
        assert source.to_features(limit=0) == []
        assert decode.call_count == 2
    assert "changed" not in source.to_features()[0].properties
    assert Locations.from_json(source.to_json()) == source
    assert source.to_dict(limit=0)["features"] == []
    assert list(source.to_geodataframe(properties=[])) == ["id", "geometry"]


@pytest.mark.parametrize("bad", [True, -1, 1.5])
def test_invalid_limits_and_slices(bad: int) -> None:
    source = value()
    for call in [
        lambda: source.slice(bad),
        lambda: source.slice(0, bad),
        lambda: source.to_dict(limit=bad),
        lambda: source.iter_features(limit=bad),
        lambda: source.iter_features(batch_size=bad),
    ]:
        with pytest.raises(ValueError, match="integer"):
            call()


@pytest.mark.parametrize("names", [["v", "v"], ["unknown"]])
def test_invalid_projections(names: list[str]) -> None:
    with pytest.raises((ValueError, KeyError)):
        value().to_dataframe(properties=names)


@pytest.mark.parametrize(("first", "second"), [(True, 1), (1, 1.0), (0.0, -0.0)])
def test_equality_preserves_scalar_types_and_bits(
    first: object, second: object
) -> None:
    raw = value().to_dict(limit=1)
    raw["features"][0]["properties"] = {"v": first}
    left = Locations.from_dict(raw)
    raw["features"][0]["properties"] = {"v": second}
    right = Locations.from_dict(raw)
    assert left != right
    with pytest.raises(TypeError):
        hash(left)


def test_large_bounds_truncate_and_projections_are_detached() -> None:
    source = value()
    assert source.slice(2**100).is_empty
    assert source.slice(1, 2**100) == source.slice(1)
    assert source.to_dict(limit=2**100) == source.to_dict()
    frame = source.to_dataframe()
    frame.loc[2, "v"] = 0
    assert source.property("v")[2] == 2**100
    with pytest.raises(ValueError, match="collides"):
        source.to_dataframe(id_column="v")
    with pytest.raises(ValueError, match="integer"):
        source.iter_features(batch_size=0)
