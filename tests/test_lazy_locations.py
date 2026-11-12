import json
from collections.abc import Iterable, Sequence
from dataclasses import FrozenInstanceError
from typing import cast
from unittest.mock import patch

import pandas as pd
import pytest

from lyra.data import content_fingerprints, geometry_buffers
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.locations import (
    Locations,
    location_fingerprint,
    location_snapshot,
    locations_from_snapshot,
)
from test_support.location_helpers import stored_locations


def value() -> Locations:
    return stored_locations(
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
                        {"v": None},
                        {"v": None},
                        {"v": 2**60 + 1},
                        {"v": 3},
                        {"v": 0},
                    ]
                )
            ],
        }
    )


def test_metadata_and_projection_never_export_rows() -> None:
    source = value()
    with (
        patch.object(geometry_buffers, "geometry_rows", side_effect=AssertionError),
        patch.object(LocationSnapshot, "property_data", side_effect=AssertionError),
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
        assert source.property("v")[0] is None
        assert source.property("v")[1] is None
        assert type(source.property("v")[3]) is int
        assert source.property("v")[2] == 2**60 + 1
        frame = source.to_dataframe()
        assert frame.v.dtype == pd.Int64Dtype()
        assert frame.v[0] is pd.NA
        assert frame.v[1] is pd.NA
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
        content_fingerprints,
        "typed_snapshot",
        wraps=content_fingerprints.typed_snapshot,
    ) as compute:
        selected = source.slice(1, 3).select_ids(["3", "1"]).slice(1)
        compute.assert_not_called()
        assert list(selected.ids) == ["1"]
        expected = stored_locations(
            source.to_dict() | {"features": source.to_dict()["features"][1:2]}
        )
        compute.assert_not_called()
        assert selected == expected
        identity = location_fingerprint(selected)
        assert identity == location_fingerprint(expected)
        # Once requested, subsequent identity access must reuse cached work.
        compute.reset_mock()
        assert location_fingerprint(selected) == identity
        assert location_fingerprint(expected) == identity
        assert selected == expected
        compute.assert_not_called()
    # Contiguous slices promise shared immutable buffers, not copied storage.
    shared = location_snapshot(source.slice(1, 2))
    assert (
        shared.table.column(0).chunk(0).buffers()[1].address
        == snapshot.table.column(0).chunk(0).buffers()[1].address
    )
    assert memoryview(shared.table.column(0).chunk(0).buffers()[1]).readonly
    assert source.select_ids([]).property_names == ("v",)
    assert source.slice(999).is_empty
    with pytest.raises(KeyError):
        source.select_ids(["missing"])
    for ids in [["1", "1"], [""], [True]]:
        with pytest.raises(ValueError, match="Selection IDs"):
            source.select_ids(cast("Sequence[str]", ids))
    with pytest.raises(TypeError):
        locations_from_snapshot(object.__new__(LocationSnapshot))


@pytest.mark.parametrize(
    ("batch_size", "limit"), [(1, 0), (1, 3), (2, 3), (8, 3), (2, None)]
)
def test_bounded_feature_iteration_and_exports(
    batch_size: int, limit: int | None
) -> None:
    source = value()
    expected = source.to_features()[:limit]
    with patch.object(
        geometry_buffers, "geometry_rows", wraps=geometry_buffers.geometry_rows
    ) as decode:
        iterator = source.iter_features(batch_size=batch_size, limit=limit)
        decode.assert_not_called()
        exported = []
        for index, feature in enumerate(iterator):
            exported.append(feature)
            sizes = [call.args[0].num_rows for call in decode.call_args_list]
            # Bound materialized rows and read-ahead, allowing smaller batches.
            assert all(0 < size <= batch_size for size in sizes)
            assert sum(sizes) <= min(len(expected), index + batch_size)
        assert exported == expected
        assert sum(call.args[0].num_rows for call in decode.call_args_list) <= len(
            expected
        )
        decode.reset_mock()
        assert source.to_features(limit=0) == []
        decode.assert_not_called()
    if exported:
        exported[0].properties["changed"] = True
    assert "changed" not in source.to_features()[0].properties
    assert json.loads(source.to_json()) == source.to_dict()
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
    left = stored_locations(raw)
    raw["features"][0]["properties"] = {"v": second}
    right = stored_locations(raw)
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
    assert source.property("v")[2] == 2**60 + 1
    with pytest.raises(ValueError, match="collides"):
        source.to_dataframe(id_column="v")
    with pytest.raises(ValueError, match="integer"):
        source.iter_features(batch_size=0)
