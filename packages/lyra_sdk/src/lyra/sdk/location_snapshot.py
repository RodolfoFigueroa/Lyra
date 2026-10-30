"""Owned internal location columns, with validated construction and fresh exports.

Only this concrete type establishes snapshot validity. Model instances and caller
flags do not. Construction always detaches and validates, including reconstruction
from another snapshot's columns. The IPC stream is an ownership-copy mechanism,
not a persisted artifact format. Its immutable bytes back the Arrow tables directly;
no feature models or serialized duplicate are retained.
"""

from dataclasses import dataclass
from typing import Any, final

import pyarrow as pa
from lyra.sdk import content_fingerprints, geometry_buffers, property_buffers


def _own(table: pa.Table) -> pa.Table:
    table.validate(full=True)
    table = table.combine_chunks()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    # Arrow allocations alone remain writable through Buffer/memoryview. Python
    # bytes provide immutable backing, including nested children and sliced arrays.
    return pa.ipc.open_stream(sink.getvalue().to_pybytes()).read_all()


@final
@dataclass(frozen=True, slots=True, init=False)
class LocationSnapshot:
    """Private validated snapshot; column access exposes only immutable buffers."""

    geometry: pa.Table
    properties: pa.Table
    geometry_fingerprint: str
    non_geometry_fingerprint: str
    fingerprint: str

    def __init__(self, geometry: pa.Table, properties: pa.Table) -> None:
        """Own and validate all columns before making a snapshot available.

        Raises:
            ValueError: If components are malformed or row counts disagree.
        """
        owned_geometry = _own(geometry)
        owned_properties = _own(properties)
        if owned_geometry.num_rows != owned_properties.num_rows:
            message = "Geometry and property row counts must match"
            raise ValueError(message)
        geometry_buffers.decode(owned_geometry)
        property_buffers.decode(owned_properties)
        fingerprints = content_fingerprints.compute(owned_geometry, owned_properties)
        for name, value in zip(
            ("geometry_fingerprint", "non_geometry_fingerprint", "fingerprint"),
            fingerprints,
            strict=True,
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "geometry", owned_geometry)
        object.__setattr__(self, "properties", owned_properties)

    def to_dict(self) -> dict[str, Any]:
        """Return independently mutable collection data.

        Returns:
            Scope, CRS, and ordered features with independent nested contents.
        """
        data = property_buffers.decode(self.properties)
        ids = data.pop("ids")
        rows = data.pop("properties")
        data["features"] = [
            {
                "type": "Feature",
                "id": identifier,
                "properties": properties,
                "geometry": geometry.model_dump(),
            }
            for identifier, properties, geometry in zip(
                ids, rows, geometry_buffers.decode(self.geometry), strict=True
            )
        ]
        return data
