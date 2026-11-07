"""Standard Arrow IPC artifacts with declarative Lyra metadata."""

from __future__ import annotations

import json
from dataclasses import asdict

import pyarrow as pa
import pyproj
import shapely

from lyra.contracts.locations import LocationContractReference
from lyra.data.location_snapshot import LocationSnapshot
from lyra.data.typed_storage import Scope, Snapshot

_KEY = b"lyra"


def encode(snapshot: Snapshot) -> bytes:
    """Write one uncompressed Arrow IPC file.

    Returns:
        Uncompressed standard IPC file bytes.
    """
    data = snapshot.arrow
    scope = None if snapshot.scope is None else asdict(snapshot.scope)
    if scope is not None and snapshot.scope is not None:
        scope["contract"] = (
            None
            if snapshot.scope.contract is None
            else snapshot.scope.contract.model_dump()
        )
        scope.pop("columns")
        scope.pop("crs")
    metadata = {"version": 3, "scope": scope, "fingerprints": snapshot.fingerprints}
    schema = data.schema.with_metadata(
        {
            **(data.schema.metadata or {}),
            _KEY: json.dumps(metadata, sort_keys=True).encode(),
        }
    )
    sink = pa.BufferOutputStream()
    # Record batches preserve row counts for tables with zero columns.
    with pa.ipc.new_file(sink, schema) as writer:
        for batch in data.to_batches():
            writer.write_batch(batch.replace_schema_metadata(schema.metadata))
    return sink.getvalue().to_pybytes()


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
    frame = table.to_pandas()
    if list(frame.columns) != table.column_names:
        msg = "Inconsistent pandas reconstruction column names"
        raise ValueError(msg)


def decode(data: bytes | bytearray | memoryview) -> Snapshot:
    """Read owned contents and verify stored content identity.

    Returns:
        An owned snapshot with verified content fingerprints.

    Raises:
        ValueError: If the supplied data is invalid.
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
        OSError,
        shapely.errors.GEOSException,
        pyproj.exceptions.CRSError,
    ) as error:
        msg = f"Invalid typed IPC artifact: {error}"
        raise ValueError(msg) from error


def _decode(data: bytes) -> Snapshot:
    if data[:6] != b"ARROW1":
        msg = "Expected Arrow IPC file"
        raise ValueError(msg)
    with pa.ipc.open_file(pa.BufferReader(bytes(data))) as reader:
        table = reader.read_all()
    metadata = dict(table.schema.metadata or {})
    saved = json.loads(metadata.pop(_KEY))
    if saved["version"] != 3:
        msg = "Unsupported artifact protocol"
        raise ValueError(msg)
    table = pa.Table.from_batches(
        [
            batch.replace_schema_metadata(metadata or None)
            for batch in table.to_batches()
        ],
        schema=table.schema.with_metadata(metadata or None),
    )
    scope_data = saved["scope"]
    scope = None
    if scope_data is not None:
        field = table.schema.field(scope_data["geometry_column"])
        field_metadata = field.metadata or {}
        if field_metadata[b"ARROW:extension:name"] != b"geoarrow.wkb":
            msg = "Expected WKB geometry"
            raise ValueError(msg)
        crs = json.loads(field_metadata[b"ARROW:extension:metadata"])["crs"]
        scope_data["crs"] = "" if crs is None else pyproj.CRS(crs).to_string()
        scope_data["columns"] = tuple(table.column_names)
        reference = scope_data["contract"]
        scope_data["contract"] = (
            None
            if reference is None
            else LocationContractReference.model_validate(reference)
        )
        scope = Scope(**scope_data)
    properties, geometry = Snapshot.split(table, scope)
    cls = LocationSnapshot if scope is not None and not scope.metric else Snapshot
    snapshot = cls(properties, geometry, scope)
    _check_pandas(snapshot.table)
    if list(snapshot.fingerprints) != saved["fingerprints"]:
        msg = "Content fingerprint mismatch"
        raise ValueError(msg)
    return snapshot
