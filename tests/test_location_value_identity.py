import geopandas as gpd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point

from lyra.data.location_artifacts import decode, encode
from lyra.data.locations import (
    Locations,
    location_snapshot,
    locations_from_snapshot,
)
from test_support.location_helpers import output_schema
from test_support.location_schema import column, contract, dataset, declaration


def value(**changes: object) -> Locations:
    schema = output_schema()
    return schema.build(
        schema.empty_frame("EPSG:4326"),
        collection="example:places",
        unit_type="point",
    ).with_contract(dataset(**changes))


def test_concrete_identity_and_revision_are_distinct() -> None:
    original = value()
    assert original != value(source="other")
    assert original != value(geographical_edition="other")
    assert original != value(edition="named")
    corrected = value(data_revision="r2")
    assert corrected == original
    assert corrected.contract == original.contract
    assert corrected.data_revision == "r2"


def test_empty_selection_and_artifact_keep_binding() -> None:
    original = value(data_revision="r1")
    assert original.contract is not None
    for selected in (original.slice(0), original.select_ids([])):
        restored = locations_from_snapshot(decode(encode(location_snapshot(selected))))
        assert restored == original
        assert restored.contract == original.contract
        assert restored.data_revision == "r1"
        assert restored.to_dict()["contract"] == original.contract.model_dump()
    snapshot = location_snapshot(original)
    assert snapshot.fingerprints is snapshot.fingerprints


def test_scope_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="scope"):
        value().with_binding(
            dataset().reference.model_copy(update={"unit_type": "lot"}), None
        )


def test_ordered_nonempty_values_share_owned_buffers() -> None:
    schema = output_schema(count=int)
    frame = gpd.GeoDataFrame(
        {"id": ["a", "b"], "count": [1, 2], "geometry": [Point(1, 2), Point(3, 4)]},
        crs="EPSG:4326",
    )
    original = schema.build(frame, collection="example:places", unit_type="point")
    bound = original.with_contract(dataset(data_revision="r1"))
    assert (
        location_snapshot(bound).table["count"].chunk(0).buffers()[1].address
        == location_snapshot(original).table["count"].chunk(0).buffers()[1].address
    )
    frame.loc[0, "count"] = 99
    selected = bound.select_ids(["b", "a"])
    assert list(selected.ids) == ["b", "a"]
    assert list(selected.property("count")) == [2, 1]
    assert selected.contract == bound.contract
    assert selected.data_revision == "r1"
    assert schema.validate(bound) is bound
    for result in (selected, bound.slice(1, 1), bound.slice(2)):
        restored = locations_from_snapshot(decode(encode(location_snapshot(result))))
        assert restored == result
        assert restored.data_revision == "r1"
        assert restored.to_dict() == result.to_dict()


def test_contract_construction_checks_shared_schema() -> None:
    from_contract = dataset()

    schema = contract(declaration(count=column(int, checks=pa.Check.ge(0))))
    result = schema.build_contract(schema.empty_frame("EPSG:4326"), from_contract)
    assert result.contract == from_contract.reference
    with pytest.raises(ValueError, match="structure"):
        output_schema().build_contract(
            output_schema().empty_frame("EPSG:4326"), from_contract
        )
