"""Standalone IPC location artifacts; storage and producer validation are separate."""

import json

import pyarrow as pa
from lyra.sdk.location_snapshot import LocationSnapshot

_VERSION = 1
_BATCH_SIZE = 65536
_COMPRESSION_LEVEL = 3


class ArtifactDecodeError(ValueError):
    """Malformed or unsupported standalone location artifact."""


def encode(snapshot: LocationSnapshot) -> bytes:
    """Encode complete location columns and identity as a compressed IPC file.

    Returns:
        Standalone bytes containing scope, CRS, representation and fingerprints.
    """
    metadata = {
        "version": _VERSION,
        "representation": "lyra-location-columns",
        "content_protocol": 1,
        "geometry_fingerprint": snapshot.geometry_fingerprint,
        "non_geometry_fingerprint": snapshot.non_geometry_fingerprint,
        "fingerprint": snapshot.fingerprint,
    }
    table = pa.Table.from_arrays(
        [*snapshot.geometry.columns, *snapshot.properties.columns],
        schema=pa.schema(
            [*snapshot.geometry.schema, *snapshot.properties.schema],
            metadata={
                b"artifact": json.dumps(metadata).encode(),
                b"location": snapshot.properties.schema.metadata[b"location"],
            },
        ),
    )
    sink = pa.BufferOutputStream()
    options = pa.ipc.IpcWriteOptions(
        compression=pa.Codec("zstd", compression_level=_COMPRESSION_LEVEL)
    )
    with pa.ipc.new_file(sink, table.schema, options=options) as writer:
        writer.write_table(table, max_chunksize=_BATCH_SIZE)
    return sink.getvalue().to_pybytes()


def decode(source: bytes | bytearray | memoryview) -> LocationSnapshot:
    """Validate an IPC artifact and return detached immutable location columns.

    Returns:
        A fully validated snapshot with recomputed content identity.

    Raises:
        ArtifactDecodeError: For unsupported metadata, corrupt IPC or invalid data.
    """
    try:
        return _decode(source)
    except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
        message = f"Cannot decode location artifact: {error}"
        raise ArtifactDecodeError(message) from error


def _decode(source: bytes | bytearray | memoryview) -> LocationSnapshot:
    table = pa.ipc.open_file(bytes(source)).read_all()
    table.validate(full=True)
    metadata = table.schema.metadata or {}
    if set(metadata) != {b"artifact", b"location"}:
        message = "Invalid artifact metadata fields"
        raise ValueError(message)
    stored = json.loads(metadata[b"artifact"])
    expected_fields = {
        "version",
        "representation",
        "content_protocol",
        "geometry_fingerprint",
        "non_geometry_fingerprint",
        "fingerprint",
    }
    if not isinstance(stored, dict) or set(stored) != expected_fields:
        message = "Invalid artifact identity metadata"
        raise ValueError(message)
    if (
        type(stored["version"]) is not int
        or stored["version"] != _VERSION
        or type(stored["content_protocol"]) is not int
        or stored["content_protocol"] != 1
        or stored["representation"] != "lyra-location-columns"
    ):
        message = "Unsupported artifact representation or protocol version"
        raise ValueError(message)
    snapshot = LocationSnapshot(
        table.select([0, 1]).replace_schema_metadata(None),
        table.select(list(range(2, table.num_columns))).replace_schema_metadata(
            {b"location": metadata[b"location"]}
        ),
    )
    for field in (
        "geometry_fingerprint",
        "non_geometry_fingerprint",
        "fingerprint",
    ):
        if stored[field] != getattr(snapshot, field):
            message = f"Artifact {field} mismatch"
            raise ValueError(message)
    return snapshot
