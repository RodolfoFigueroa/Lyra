"""Immutable ownership boundary for internal Arrow storage."""

import pyarrow as pa


def own(table: pa.Table) -> pa.Table:
    """Return a table backed exclusively by immutable owned bytes."""
    table.validate(full=True)
    table = table.combine_chunks()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    # Arrow allocations alone remain writable through Buffer/memoryview. Python
    # bytes provide immutable backing, including nested children and sliced arrays.
    return pa.ipc.open_stream(sink.getvalue().to_pybytes()).read_all()
