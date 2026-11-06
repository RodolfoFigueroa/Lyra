"""Immutable native invocation artifacts shared by caching and retained jobs."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from lyra.sdk import ipc_codec, location_artifacts
from lyra.sdk.inspection import MAX_INSPECTION_BYTES, summarize_outputs
from lyra.sdk.models.inspection import StoredOutputInspection
from lyra.sdk.models.location import (
    Locations,
    location_snapshot,
    locations_from_snapshot,
)
from lyra.sdk.typed_storage import Snapshot
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandera.pandas as pa
    from lyra.sdk.results import NativeOutputs


_INSPECTION = TypeAdapter(dict[str, StoredOutputInspection])


class ArtifactStore:
    """Store complete invocation bundles in one local filesystem directory."""

    def __init__(self, directory: Path) -> None:
        """Resolve the caller-owned artifact namespace."""
        self.directory = directory.resolve() / "bundles"

    def read(self, key: str) -> NativeOutputs:
        """Read a complete bundle.

        Returns:
            The complete named native tables.
        """
        directory, manifest = self._bundle(key, "metric")
        self._inspection(directory, manifest)
        return {
            name: ipc_codec.decode(
                (directory / f"{index}.arrow").read_bytes()
            ).to_frame()
            for index, name in enumerate(manifest["outputs"])
        }

    def inspect(self, key: str) -> dict:
        """Return persisted metric summaries without reading native table data."""
        directory, manifest = self._bundle(key, "metric")
        return self._inspection(directory, manifest)

    @staticmethod
    def _inspection(directory: Path, manifest: dict) -> dict:
        with (directory / "inspection.json").open("rb") as source:
            data = source.read(MAX_INSPECTION_BYTES + 1)
        if len(data) > MAX_INSPECTION_BYTES:
            message = "Stored inspection exceeds its size limit"
            raise ValueError(message)
        saved = _INSPECTION.validate_json(data)
        if saved.keys() != manifest["outputs"].keys():
            message = "Inspection output names differ from artifact outputs"
            raise ValueError(message)
        return {name: item.model_dump(mode="json") for name, item in saved.items()}

    def write(
        self,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> str:
        """Publish all outputs atomically.

        Returns:
            The immutable artifact ID.
        """
        return self._publish(outputs, schemas)

    def read_locations(self, key: str) -> Locations:
        """Decode a complete internal location artifact.

        Shared invariants are checked here; the executor applies the current
        operation schema before accepting the value.

        Returns:
            Validated native locations.
        """
        directory, _ = self._bundle(key, "location_operation")
        snapshot = location_artifacts.decode(
            (directory / "locations.arrow").read_bytes()
        )
        return locations_from_snapshot(snapshot)

    def write_locations(self, locations: Locations) -> str:
        """Publish normalized locations.

        Returns:
            The immutable artifact ID.
        """
        return self._publish_locations(locations)

    def _publish_locations(self, locations: Locations) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        snapshot = location_snapshot(locations)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            (directory / "locations.arrow").write_bytes(
                location_artifacts.encode(snapshot)
            )
            self._manifest(directory, "location_operation", {})
            return self._publish_directory(directory)

    def _publish_directory(self, directory: Path) -> str:
        identifier = uuid4().hex
        directory.rename(self.directory / identifier)
        return identifier

    @staticmethod
    def _manifest(directory: Path, kind: str, outputs: dict) -> None:
        members = sorted(path.name for path in directory.iterdir())
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 2 if kind == "metric" else 1,
                    "kind": kind,
                    "members": members,
                    "outputs": outputs,
                }
            )
        )

    def _bundle(self, key: str, kind: str) -> tuple[Path, dict]:
        # Resolve the immutable generation before validating its members.
        if len(key) != 32 or any(
            character not in "0123456789abcdef" for character in key
        ):
            message = "Invalid artifact identifier"
            raise ValueError(message)
        directory = (self.directory / key).resolve(strict=True)
        if directory.parent != self.directory:
            message = "Bundle outside cache directory"
            raise ValueError(message)
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            message = "Invalid bundle manifest reference"
            raise ValueError(message)
        manifest = json.loads(manifest_path.read_text())
        if (
            set(manifest) != {"version", "kind", "members", "outputs"}
            or type(manifest["version"]) is not int
            or manifest["version"] != (2 if kind == "metric" else 1)
            or manifest["kind"] != kind
            or not isinstance(manifest["outputs"], dict)
        ):
            message = "Unsupported bundle manifest"
            raise ValueError(message)
        expected = (
            {"locations.arrow"}
            if kind == "location_operation"
            else {f"{index}.arrow" for index in range(len(manifest["outputs"]))}
            | {"inspection.json"}
        )
        if kind == "metric" and (
            not manifest["outputs"]
            or any(
                not name or metadata != {}
                for name, metadata in manifest["outputs"].items()
            )
        ):
            message = "Invalid metric outputs"
            raise ValueError(message)

        invalid_location = kind == "location_operation" and bool(manifest["outputs"])
        if invalid_location:
            message = "Unexpected location outputs"
            raise ValueError(message)
        for name in expected:
            if not (directory / name).exists():
                raise FileNotFoundError(directory / name)
        if (
            not isinstance(manifest["members"], list)
            or len(manifest["members"]) != len(expected)
            or set(manifest["members"]) != expected
            or {p.name for p in directory.iterdir()} != expected | {"manifest.json"}
            or any(
                (directory / name).is_symlink() or not (directory / name).is_file()
                for name in expected | {"manifest.json"}
            )
        ):
            message = "Invalid bundle members"
            raise ValueError(message)
        return directory, manifest

    def _publish(
        self,
        outputs: NativeOutputs,
        schemas: Mapping[str, pa.DataFrameSchema],
    ) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        if outputs.keys() != schemas.keys():
            message = "Output names differ from registered schemas"
            raise ValueError(message)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            manifest = {}
            for index, (name, table) in enumerate(outputs.items()):
                snapshot = Snapshot.from_frame(table, schemas[name])
                (directory / f"{index}.arrow").write_bytes(ipc_codec.encode(snapshot))
                manifest[name] = {}
            (directory / "inspection.json").write_text(
                json.dumps(
                    summarize_outputs(outputs),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            self._manifest(directory, "metric", manifest)
            return self._publish_directory(directory)
