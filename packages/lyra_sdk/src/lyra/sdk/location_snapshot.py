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
from lyra.sdk.models.geometry import Feature


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
        geometry_buffers.validate(owned_geometry)
        property_buffers.validate(owned_properties)
        fingerprints = content_fingerprints.compute(owned_geometry, owned_properties)
        for name, value in zip(
            ("geometry_fingerprint", "non_geometry_fingerprint", "fingerprint"),
            fingerprints,
            strict=True,
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "geometry", owned_geometry)
        object.__setattr__(self, "properties", owned_properties)

    def property_data(self) -> dict[str, Any]:
        """Return independently mutable scalar and identity contents."""
        return property_buffers.property_data(self.properties)

    def to_features(self) -> list[Feature]:
        """Return fresh models without revalidating owned contents."""
        data = self.property_data()
        return [
            Feature.model_construct(
                type="Feature",
                id=identifier,
                properties=properties,
                geometry=geometry_buffers.geometry_model(geometry),
            )
            for identifier, properties, geometry in zip(
                data["ids"],
                data["properties"],
                geometry_buffers.geometry_rows(self.geometry),
                strict=True,
            )
        ]

    def to_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        """Return detached collection data, materializing only the requested rows.

        Raises:
            ValueError: If limit is not a nonnegative integer or None.
        """
        if limit is not None and (type(limit) is not int or limit < 0):
            message = "Export limit must be a nonnegative integer"
            raise ValueError(message)
        count = self.geometry.num_rows if limit is None else limit
        data = property_buffers.property_data(self.properties.slice(0, count))
        ids = data.pop("ids")
        rows = data.pop("properties")
        data["features"] = [
            {
                "type": "Feature",
                "id": identifier,
                "properties": properties,
                "geometry": geometry,
            }
            for identifier, properties, geometry in zip(
                ids,
                rows,
                geometry_buffers.geometry_rows(self.geometry.slice(0, count)),
                strict=True,
            )
        ]
        return data
