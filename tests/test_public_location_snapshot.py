"""Public snapshot ownership, round trips, and boundary validation."""

import pytest
from lyra.sdk.models.location import Locations, location_snapshot
from pydantic import ValidationError

from benchmarks.location_baseline.fixtures import assert_exact, variants


@pytest.mark.parametrize("raw", variants().values())
def test_public_roundtrip_and_exports(raw: dict) -> None:
    value = Locations.from_dict(raw)
    assert_exact(value.to_dict(), raw)
    assert_exact(Locations.from_json(value.to_json()).to_dict(), raw)
    assert_exact(Locations.from_dict(value.to_dict()).to_dict(), raw)
    assert not hasattr(value, "__dict__")
    value.crs.properties.name = "EPSG:3857"
    list(value.ids).clear()
    exported = value.to_features()
    if exported:
        exported[0].properties["changed"] = 9
        changed = Locations.from_dict({**value.to_dict(), "features": exported})
        changed_snapshot = location_snapshot(changed)
        original_snapshot = location_snapshot(value)
        assert (
            changed_snapshot.geometry_fingerprint
            == original_snapshot.geometry_fingerprint
        )
        assert (
            changed_snapshot.non_geometry_fingerprint
            != original_snapshot.non_geometry_fingerprint
        )
    assert_exact(value.to_dict(), raw)
    dumped = value.to_dict()
    dumped["features"].clear()
    assert_exact(value.to_dict(), raw)
    with pytest.raises(ValidationError):
        Locations.from_dict(
            Locations.from_dict(value.to_dict() | {"collection": "invalid"})
        )
