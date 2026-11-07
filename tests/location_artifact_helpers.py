"""Mutate standard IPC artifacts for isolated integrity tests."""

import json
from collections.abc import Callable

import pyarrow as pa


def alter(payload: bytes, change: Callable) -> bytes:
    table = pa.ipc.open_file(payload).read_all()
    metadata = json.loads(table.schema.metadata[b"lyra"])
    metadata, table, _ = change(metadata, table, b"")
    table = table.replace_schema_metadata(
        {**table.schema.metadata, b"lyra": json.dumps(metadata).encode()}
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()
