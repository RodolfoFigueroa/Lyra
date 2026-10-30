"""Public standalone location inspection without producer discovery."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lyra.sdk.location_artifacts import decode
from lyra.sdk.location_snapshot import LocationSnapshot
from lyra.sdk.models.location import Locations, locations_from_snapshot


@dataclass(frozen=True, slots=True)
class LocationArtifact:
    """Validated artifact contents; no producer schema validation is performed."""

    _snapshot: LocationSnapshot

    @property
    def metadata(self) -> dict[str, Any]:
        """Independent scope, CRS, property keys, count and content identity."""
        data = json.loads(self._snapshot.properties.schema.metadata[b"location"])
        data.update(
            feature_count=self._snapshot.properties.num_rows,
            fingerprint=self._snapshot.fingerprint,
            geometry_fingerprint=self._snapshot.geometry_fingerprint,
            non_geometry_fingerprint=self._snapshot.non_geometry_fingerprint,
            producer_schema_validated=False,
        )
        return data

    def preview(self, limit: int = 5) -> list[dict[str, Any]]:
        """Materialize at most limit independent features, including properties.

        Returns:
            Ordered feature dictionaries preserving absent and null properties.

        Raises:
            ValueError: If limit is not a nonnegative integer.
        """
        if type(limit) is not int or limit < 0:
            message = "Preview limit must be a nonnegative integer"
            raise ValueError(message)
        return self._snapshot.to_dict(limit=limit)["features"]

    def to_locations(self) -> Locations:
        """Return public locations supporting independent GeoDataFrame conversion."""
        return locations_from_snapshot(self._snapshot)


def read_location_artifact(path: str | Path) -> LocationArtifact:
    """Read and validate a standalone IPC file without a manifest or plugins.

    Returns:
        Inspection access to validated location contents, not a producer schema.

    File access errors propagate as OSError; invalid content raises
    ArtifactDecodeError with the decoder failure reason.
    """
    return LocationArtifact(decode(Path(path).read_bytes()))
