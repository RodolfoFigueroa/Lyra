"""Internal snapshot exports have independent mutable values."""

import pytest

from lyra.data.location_snapshot import LocationSnapshot
from test_support.location_data import assert_exact, columns, variants


@pytest.mark.parametrize("raw", variants().values())
def test_detached_snapshot_exports(raw: dict) -> None:
    snapshot = LocationSnapshot(*columns(raw))
    first = snapshot.to_dict()
    assert_exact(first, raw)
    first["features"].clear()
    for feature in snapshot.to_features():
        feature.properties["changed"] = 9
    assert_exact(snapshot.to_dict(), raw)
