"""Content identity is exact, owned, layout independent, and cached."""

import copy
import hashlib
import struct
from unittest.mock import patch

import pyarrow as pa
from lyra.sdk import content_fingerprints
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import columns, payload, variants


def snapshot(raw: dict) -> LocationSnapshot:
    return LocationSnapshot(*columns(raw))


def test_components_and_independent_combination() -> None:
    values = {key: snapshot(raw) for key, raw in variants().items()}
    base = values["base"]
    assert base.fingerprint == values["reordered"].fingerprint
    for key in ("scope", "crs", "null"):
        assert base.geometry_fingerprint == values[key].geometry_fingerprint
        assert base.non_geometry_fingerprint != values[key].non_geometry_fingerprint
    assert base.geometry_fingerprint != values["changed"].geometry_fingerprint
    assert base.non_geometry_fingerprint == values["changed"].non_geometry_fingerprint
    assert base.fingerprint != values["feature_order"].fingerprint
    encoded = b"lyra-content\x00\x02snapshot"
    for digest in (base.geometry_fingerprint, base.non_geometry_fingerprint):
        encoded += struct.pack("<Q", 32) + bytes.fromhex(digest)
    assert hashlib.sha256(encoded).hexdigest() == base.fingerprint


def test_scalar_distinctions() -> None:
    fingerprints = []
    for scalar in [None, True, 1, 1.0, -0.0, 0.0, "1"]:
        raw = payload(1)
        raw["features"][0]["properties"] = {"value": scalar}
        fingerprints.append(snapshot(raw).fingerprint)
    assert len(set(fingerprints)) == len(fingerprints)


def test_layout_keys_mutation_and_cached_access() -> None:
    raw = payload()
    base = snapshot(raw)
    properties, geometry, scope = columns(raw)
    chunked = LocationSnapshot(
        pa.concat_tables([properties.slice(0, 2), properties.slice(2)]),
        pa.concat_tables([geometry.slice(0, 2), geometry.slice(2)]),
        scope,
    )
    assert chunked.fingerprint == base.fingerprint
    assert (
        LocationSnapshot(properties.slice(2), geometry.slice(2), scope).fingerprint
        == snapshot({**raw, "features": raw["features"][2:]}).fingerprint
    )
    exported = base.to_dict()
    exported["features"][0]["geometry"]["coordinates"].clear()
    raw["features"].clear()
    with patch.object(
        content_fingerprints, "typed_snapshot", side_effect=AssertionError
    ):
        for _ in range(100):
            assert base.fingerprint == chunked.fingerprint


def test_geometry_representation() -> None:
    raw = payload(1)
    fingerprints = []
    for geometry in [
        {"type": "Point", "coordinates": [0.0, 2.0]},
        {"type": "Point", "coordinates": [-0.0, 2.0]},
        {"type": "Point", "coordinates": [0.0, 2.0, 3.0]},
        {"type": "MultiPoint", "coordinates": [[0.0, 2.0]]},
    ]:
        changed = copy.deepcopy(raw)
        changed["features"][0]["geometry"] = geometry
        fingerprints.append(snapshot(changed).geometry_fingerprint)
    assert len(set(fingerprints)) == len(fingerprints)


def test_null_string_backing_bytes_do_not_affect_identity() -> None:
    properties, geometry, scope = columns(payload(1))
    clean = pa.array([None], type=pa.string())
    hidden = pa.Array.from_buffers(
        pa.string(),
        1,
        [
            pa.py_buffer(b"\x00"),
            pa.py_buffer(struct.pack("<ii", 0, 6)),
            pa.py_buffer(b"hidden"),
        ],
    )
    base = LocationSnapshot(properties.set_column(1, "value", clean), geometry, scope)
    rebuilt = LocationSnapshot(
        properties.set_column(1, "value", hidden), geometry, scope
    )
    assert rebuilt.to_dict() == base.to_dict()
    assert rebuilt.fingerprint == base.fingerprint
