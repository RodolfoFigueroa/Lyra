"""Public snapshot ownership, round trips, and boundary validation."""

import json

import pytest
from lyra.sdk.models.location import location_snapshot
from pydantic import ValidationError

from tests.location_data import assert_exact, variants
from tests.location_helpers import stored_locations


@pytest.mark.parametrize("raw", variants().values())
def test_public_roundtrip_and_exports(raw: dict) -> None:
    value = stored_locations(raw)
    assert_exact(value.to_dict(), raw)
    assert_exact(json.loads(value.to_json()), raw)
    assert_exact(stored_locations(value.to_dict()).to_dict(), raw)
    assert not hasattr(value, "__dict__")
    value.crs.properties.name = "EPSG:3857"
    list(value.ids).clear()
    exported = value.to_features()
    if exported:
        for feature in exported:
            feature.properties["changed"] = 9
        changed = stored_locations({**value.to_dict(), "features": exported})
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
        stored_locations(stored_locations(value.to_dict() | {"collection": "invalid"}))
