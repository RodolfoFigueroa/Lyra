"""Location presentation access over the shared owned typed snapshot."""

from typing import Any

import pyarrow as pa
from lyra.sdk import geometry_buffers
from lyra.sdk.arrow_ownership import own
from lyra.sdk.models.geometry import Feature
from lyra.sdk.scalars import normalize_property
from lyra.sdk.typed_storage import Scope, Snapshot


class LocationSnapshot(Snapshot):
    """Typed location columns with bounded detached presentation exports."""

    __slots__ = ()
    scope: Scope
    geometry: pa.Table

    def with_validated_table(self, table: pa.Table) -> "LocationSnapshot":
        """Own validated replacement properties while sharing immutable geometry.

        Returns:
            A snapshot with fresh identity and the existing geometry buffers.
        """
        return self._derive(own(table), self.geometry)

    @property
    def metadata(self) -> dict[str, Any]:
        """Independent structural metadata."""
        scope = self.scope
        return {
            "collection": scope.collection,
            "unit_type": scope.unit_type,
            "crs": {"type": "name", "properties": {"name": scope.crs}},
            "keys": [
                name for name in self.table.column_names if name != scope.id_column
            ],
        }

    @property
    def geometry_fingerprint(self) -> str:
        """Geometry identity."""
        return self.fingerprints[0]

    @property
    def non_geometry_fingerprint(self) -> str:
        """Scoped column identity."""
        return self.fingerprints[1]

    @property
    def fingerprint(self) -> str:
        """Complete content identity."""
        return self.fingerprints[2]

    def property_data(self) -> dict[str, Any]:
        """Return detached identities and native property scalars."""
        scope = self.scope
        rows = self.table.to_pylist()
        ids = [row.pop(scope.id_column) for row in rows]
        rows = [
            {name: normalize_property(value, name) for name, value in row.items()}
            for row in rows
        ]
        data = self.metadata
        data.pop("keys")
        return data | {"ids": ids, "properties": rows}

    def to_features(self) -> list[Feature]:
        """Return fresh models without revalidating owned contents.

        Returns:
            A detached list of mutable feature models.
        """
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

        Returns:
            Detached scoped contents.

        Raises:
            ValueError: If limit is not a nonnegative integer or None.
        """
        if limit is not None and (type(limit) is not int or limit < 0):
            message = "Export limit must be a nonnegative integer"
            raise ValueError(message)
        count = (
            self.geometry.num_rows
            if limit is None
            else min(limit, self.geometry.num_rows)
        )
        data = self.slice(0, count).property_data()
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
