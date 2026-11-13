"""Immutable native invocation artifacts shared by caching and retained jobs."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry
from pydantic import TypeAdapter

from lyra.contracts.inspection import MAX_INSPECTION_BYTES, StoredOutputInspection
from lyra.contracts.stored_operation import StoredOperation
from lyra.data import ipc_codec
from lyra.data.inspection import summarize_outputs
from lyra.data.typed_storage import Snapshot
from lyra.runtime.results import RunOutputMetadata
from lyra.sdk import storage_schema
from lyra.sdk.output_schema import compile_output
from lyra.sdk.results import validate_registered_result

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd
    import pandera.pandas as pandera

    from lyra.contracts.outputs import OutputMetadata
    from lyra.data.results import NativeOutputs


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
        outputs = {
            name: ipc_codec.decode(
                (directory / f"{index}.arrow").read_bytes()
            ).to_frame()
            for index, name in enumerate(manifest["outputs"])
        }

        descriptions = self.descriptions(key)
        for name, frame in outputs.items():
            _validate_restored(
                frame, descriptions[name], manifest["outputs"][name]["partitions"]
            )
        return outputs

    def descriptions(self, key: str) -> dict[str, RunOutputMetadata]:
        """Read captured output descriptions independently of the live catalog.

        Returns:
            Captured descriptions keyed by output name.
        """
        _, manifest = self._bundle(key, "metric")
        return {
            name: RunOutputMetadata.model_validate(item["description"])
            for name, item in manifest["outputs"].items()
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
        schemas: Mapping[str, pandera.DataFrameSchema],
    ) -> str:
        """Publish all outputs atomically.

        Returns:
            The immutable artifact ID.

        Raises:
            ValueError: If output names differ from the declared schema names.
        """
        if outputs.keys() != schemas.keys():
            message = "Output names differ from registered schemas"
            raise ValueError(message)
        value = next(iter(outputs.values())) if len(outputs) == 1 else outputs
        validated = validate_registered_result("artifact", schemas, value)
        descriptions = {
            name: RunOutputMetadata(
                scope="unpartitioned",
                table_schema=_describe_schema(schema),
                owner_scope=None,
            )
            for name, schema in schemas.items()
        }
        return self.write_validated(
            validated,
            {name: storage_schema.describe(schema) for name, schema in schemas.items()},
            descriptions,
            (),
        )

    def read_operation(self, key: str) -> StoredOperation:
        """Restore strict completed mapping groups and their captured scopes.

        Returns:
            Independent retained artifact contents or their identifier.

        Raises:
            ValueError: If stored contracts are invalid.
        """
        directory, _ = self._bundle(key, "operation")
        value = StoredOperation.model_validate_json(
            (directory / "operation.json").read_bytes()
        )
        if any(len(ids) != len(set(ids)) for ids in value.groups.values()):
            message = "Duplicate operation targets within a source group"
            raise ValueError(message)
        return value

    def write_operation(self, value: StoredOperation) -> str:
        """Atomically publish completed relationships without provider records.

        Returns:
            Independent retained artifact contents or their identifier.

        Raises:
            ValueError: If stored contracts are invalid.
        """
        # Public input is independently checked before publication.
        checked = StoredOperation.model_validate_json(value.model_dump_json())
        if any(len(ids) != len(set(ids)) for ids in checked.groups.values()):
            message = "Duplicate operation targets within a source group"
            raise ValueError(message)
        return self.write_validated_operation(checked)

    def write_validated_operation(self, value: StoredOperation) -> str:
        """Publish privately owned, normalized operation groups.

        Returns:
            The immutable completed mapping identifier.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            (directory / "operation.json").write_text(value.model_dump_json())
            self._manifest(directory, "operation", {})
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
            {"operation.json"}
            if kind == "operation"
            else {f"{index}.arrow" for index in range(len(manifest["outputs"]))}
            | {"inspection.json"}
        )
        if kind == "metric" and (
            not manifest["outputs"]
            or any(
                not name or set(metadata) != {"description", "partitions"}
                for name, metadata in manifest["outputs"].items()
            )
        ):
            message = "Invalid metric outputs"
            raise ValueError(message)

        invalid_location = kind == "operation" and bool(manifest["outputs"])
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

    def write_validated(
        self,
        outputs: NativeOutputs,
        layouts: Mapping[str, storage_schema.StorageDescription],
        descriptions: Mapping[str, RunOutputMetadata],
        partitions: tuple[str, ...],
    ) -> str:
        """Publish privately owned validated tables and compiled descriptions.

        Returns:
            The immutable artifact identifier.

        Raises:
            ValueError: If names disagree with the descriptions.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        if outputs.keys() != layouts.keys() or outputs.keys() != descriptions.keys():
            message = "Output names differ from registered schemas"
            raise ValueError(message)
        with TemporaryDirectory(dir=self.directory, prefix=".writing-") as scratch:
            directory = Path(scratch) / "bundle"
            directory.mkdir()
            manifest = {}
            for index, (name, table) in enumerate(outputs.items()):
                layout = layouts[name]
                snapshot = Snapshot.from_frame(
                    table, layout.properties, columns=layout.columns
                )
                (directory / f"{index}.arrow").write_bytes(ipc_codec.encode(snapshot))
                manifest[name] = {
                    "description": descriptions[name].model_dump(mode="json"),
                    "partitions": list(partitions),
                }
            (directory / "inspection.json").write_text(
                json.dumps(
                    summarize_outputs(outputs),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            self._manifest(directory, "metric", manifest)
            return self._publish_directory(directory)


def _validate_restored(
    frame: pd.DataFrame, description: RunOutputMetadata, partitions: list[str]
) -> None:
    """Check stored structural contracts without executing producer callbacks.

    Raises:
        ValueError: If stored ownership or completion is inconsistent.
    """
    metadata = description.table_schema
    columns = {
        column.name: pa.Column(
            Geometry(**column.parameters)
            if column.dtype == "geometry"
            else column.dtype,
            nullable=column.nullable,
            unique=column.unique,
        )
        for column in metadata.columns
    }
    schema = pa.DataFrameSchema(columns, strict=True, unique=metadata.unique)
    validate_registered_result("artifact", {"output": schema}, frame)
    if (
        not isinstance(partitions, list)
        or any(type(item) is not str for item in partitions)
        or len(partitions) != len(set(partitions))
    ):
        message = "Invalid completed partitions"
        raise ValueError(message)
    if description.scope == "location":
        if (
            description.owner_scope is None
            or not frame["location_id"].isin(partitions).all()
        ):
            message = "Invalid stored output ownership"
            raise ValueError(message)
    elif description.owner_scope is not None or partitions:
        message = "Unexpected unpartitioned ownership"
        raise ValueError(message)


def _describe_schema(schema: pandera.DataFrameSchema) -> OutputMetadata:
    """Describe a public storage declaration with default editorial labels.

    Returns:
        Captured table structure and column descriptions.
    """
    declaration = schema.update_columns(
        {
            name: {
                "description": column.description or name,
                "metadata": {"unit": None, **(column.metadata or {})},
            }
            for name, column in schema.columns.items()
        }
    )
    return compile_output(declaration)[1]
