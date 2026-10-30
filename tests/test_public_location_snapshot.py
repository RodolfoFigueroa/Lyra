"""Public snapshot ownership, round trips, and boundary validation."""

import pytest
from lyra.sdk.models.location import Locations
from pydantic import ValidationError

from benchmarks.location_baseline.fixtures import assert_exact, variants


@pytest.mark.parametrize("raw", variants().values())
def test_public_roundtrip_and_exports(raw: dict) -> None:
    value = Locations.model_validate(raw)
    assert_exact(value.model_dump(), raw)
    assert_exact(
        Locations.model_validate_json(value.model_dump_json()).model_dump(), raw
    )
    assert_exact(Locations.model_validate(value).model_dump(), raw)
    assert value.__dict__ == {}
    value.crs.properties.name = "EPSG:3857"
    value.ids.clear()
    exported = value.features
    if exported:
        exported[0].properties["changed"] = 9
        changed = Locations.model_validate({**value.model_dump(), "features": exported})
        assert changed.__pydantic_private__ is not None
        assert value.__pydantic_private__ is not None
        changed_snapshot = changed.__pydantic_private__["_snapshot"]
        original_snapshot = value.__pydantic_private__["_snapshot"]
        assert (
            changed_snapshot.geometry_fingerprint
            == original_snapshot.geometry_fingerprint
        )
        assert (
            changed_snapshot.non_geometry_fingerprint
            != original_snapshot.non_geometry_fingerprint
        )
    assert_exact(value.model_dump(), raw)
    dumped = value.model_dump()
    dumped["features"].clear()
    assert_exact(value.model_dump(), raw)
    with pytest.raises(ValidationError):
        Locations.model_validate(value.model_copy(update={"collection": "invalid"}))
