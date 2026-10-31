"""Standalone typed artifacts; stored contracts do not execute producer checks.

Native Arrow pandas metadata supplies reconstruction, including its known empty
categorical limitations. The envelope binds metadata and uncompressed IPC bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
from dataclasses import asdict

import pyarrow as pa
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.typed_storage import Scope, Snapshot

_MAGIC = b"LYRAIPC\x02"
_HEADER = struct.Struct("<Q")


def _write(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def encode(snapshot: Snapshot) -> bytes:
    """Encode an owned metric or location snapshot as a standalone artifact.

    Returns:
        Uncompressed typed artifact bytes.
    """
    table = _write(snapshot.table)
    geometry = b"" if snapshot.geometry is None else _write(snapshot.geometry)
    metadata = json.dumps(
        {
            "protocol": 2,
            "content_protocol": 2,
            "scope": None if snapshot.scope is None else asdict(snapshot.scope),
            "fingerprints": snapshot.fingerprints,
            "table_length": len(table),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    body = _HEADER.pack(len(metadata)) + metadata + table + geometry
    return _MAGIC + hashlib.sha256(body).digest() + body


def _read(payload: bytes) -> pa.Table:
    source = pa.BufferReader(payload)
    with pa.ipc.open_stream(source) as reader:
        table = reader.read_all()
    if source.tell() != len(payload):
        msg = "Trailing IPC data"
        raise ValueError(msg)
    return table


def _check_pandas(table: pa.Table) -> None:
    metadata = table.schema.metadata or {}
    if b"pandas" in metadata:
        pandas = json.loads(metadata[b"pandas"])
        if (
            pandas["index_columns"]
            or [column["field_name"] for column in pandas["columns"]]
            != table.column_names
        ):
            msg = "Inconsistent pandas reconstruction metadata"
            raise ValueError(msg)
    # Exercise native reconstruction before releasing usable data. No registered
    # schema or executable validation function is consulted.
    frame = table.to_pandas()
    if list(frame.columns) != table.column_names:
        msg = "Inconsistent pandas reconstruction column names"
        raise ValueError(msg)


def decode(data: bytes | bytearray | memoryview) -> Snapshot:
    """Verify structure, stored contract and integrity, then return owned data.

    Returns:
        An immutable snapshot independent of the input buffer.

    Raises:
        ValueError: If the artifact is unsupported, malformed or inconsistent.
    """
    try:
        return _decode(bytes(data))
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        pa.ArrowException,
        struct.error,
    ) as error:
        msg = f"Invalid typed IPC artifact: {error}"
        raise ValueError(msg) from error


def _decode(payload: bytes) -> Snapshot:
    if payload[: len(_MAGIC)] != _MAGIC:
        msg = "Unsupported artifact protocol"
        raise ValueError(msg)
    digest = payload[len(_MAGIC) : len(_MAGIC) + 32]
    body = payload[len(_MAGIC) + 32 :]
    if not hmac.compare_digest(digest, hashlib.sha256(body).digest()):
        msg = "Artifact integrity mismatch"
        raise ValueError(msg)
    (length,) = _HEADER.unpack(body[: _HEADER.size])
    end = _HEADER.size + length
    metadata = json.loads(body[_HEADER.size : end])
    if set(metadata) != {
        "protocol",
        "content_protocol",
        "scope",
        "fingerprints",
        "table_length",
    }:
        msg = "Invalid artifact metadata fields"
        raise ValueError(msg)
    if metadata["protocol"] != 2 or metadata["content_protocol"] != 2:
        msg = "Unsupported artifact or content protocol"
        raise ValueError(msg)
    table_length = metadata["table_length"]
    if type(table_length) is not int or not 0 < table_length <= len(body) - end:
        msg = "Invalid IPC table length"
        raise ValueError(msg)
    table = _read(body[end : end + table_length])
    remainder = body[end + table_length :]
    scope_data = metadata["scope"]
    scope = None
    if scope_data is not None:
        scope_data["columns"] = tuple(scope_data["columns"])
        scope = Scope(**scope_data)
    snapshot_type = (
        LocationSnapshot if scope is not None and not scope.metric else Snapshot
    )
    snapshot = snapshot_type(table, _read(remainder) if remainder else None, scope)
    _check_pandas(snapshot.table)
    if list(snapshot.fingerprints) != metadata["fingerprints"]:
        msg = "Content fingerprint mismatch"
        raise ValueError(msg)
    return snapshot
