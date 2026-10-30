"""Content identity is exact, owned, layout independent, and cached."""

import copy
import hashlib
import struct
from unittest.mock import patch

import pyarrow as pa
from lyra.sdk import content_fingerprints
from lyra.sdk.location_snapshot import LocationSnapshot

from benchmarks.location_baseline.fixtures import payload, variants
from tests.test_location_snapshot import columns


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
    encoded = b"lyra-content\x00\x01snapshot"
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
    raw = payload(1)
    assert snapshot(raw).fingerprint not in fingerprints


def test_layout_keys_mutation_and_cached_access() -> None:
    raw = payload()
    raw["features"][0]["properties"] = {"z": 3, "a": -0.0}
    base = snapshot(raw)
    raw["features"][0]["properties"] = {"a": -0.0, "z": 3}
    assert snapshot(raw).fingerprint == base.fingerprint
    geometry, properties = columns(raw)
    chunked = LocationSnapshot(
        *[
            pa.concat_tables([table.slice(0, 2), table.slice(2)])
            for table in (geometry, properties)
        ]
    )
    assert chunked.fingerprint == base.fingerprint
    assert (
        LocationSnapshot(geometry.slice(2), properties.slice(2)).fingerprint
        == snapshot({**raw, "features": raw["features"][2:]}).fingerprint
    )
    exported = base.to_dict()
    exported["features"][0]["geometry"]["coordinates"].clear()
    raw["features"].clear()
    with patch.object(content_fingerprints, "compute", side_effect=AssertionError):
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


def test_protocol_vector() -> None:
    assert snapshot(payload()).fingerprint == (
        "3628b666f93f565c66d0eb1deb18fa8c443b5e171864de91404d441f67ec318d"
    )


def test_null_string_backing_bytes_do_not_affect_identity() -> None:
    raw = payload(1)
    raw["features"][0]["properties"] = {"value": None}
    geometry, properties = columns(raw)
    base = LocationSnapshot(geometry, properties)
    cells = properties.column(1).combine_chunks()
    hidden = pa.Array.from_buffers(
        pa.large_string(),
        1,
        [
            pa.py_buffer(b"\x00"),
            pa.py_buffer(struct.pack("<qq", 0, 6)),
            pa.py_buffer(b"hidden"),
        ],
    )
    changed = pa.StructArray.from_arrays(
        [cells.field(0), hidden, *[cells.field(i) for i in range(2, 5)]],
        fields=list(cells.type),
    )
    rebuilt = LocationSnapshot(
        geometry, properties.set_column(1, properties.schema.field(1), changed)
    )
    assert rebuilt.to_dict() == base.to_dict()
    assert rebuilt.fingerprint == base.fingerprint
