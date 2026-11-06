import hashlib
import json
import struct
from collections.abc import Callable

import pyarrow as pa


def alter(payload: bytes, change: Callable) -> bytes:
    body = payload[40:]
    length = struct.unpack("<Q", body[:8])[0]
    metadata = json.loads(body[8 : 8 + length])
    data = body[8 + length :]
    table = pa.ipc.open_stream(data[: metadata["table_length"]]).read_all()
    metadata, table, geometry = change(
        metadata, table, data[metadata["table_length"] :]
    )
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    encoded = sink.getvalue().to_pybytes()
    metadata["table_length"] = len(encoded)
    header = json.dumps(metadata).encode()
    body = struct.pack("<Q", len(header)) + header + encoded + geometry
    return payload[:8] + hashlib.sha256(body).digest() + body
