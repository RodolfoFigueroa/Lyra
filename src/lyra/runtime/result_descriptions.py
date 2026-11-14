"""Immutable execution descriptions selecting shared metric computations."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, field_validator
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.types import JsonObject
from lyra.data.relational import frame_from_rows
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.sqlite_store import DamagedDataError, MissingValueError

if TYPE_CHECKING:
    from lyra.data.results import NativeOutputs
    from lyra.runtime.sqlite_store import SQLiteStore


class ComputationSelection(BaseModel):
    """A complete computation or selected groups from that computation."""

    model_config = ConfigDict(frozen=True)
    value_key: str
    groups: tuple[str, ...] | None = None


class ResultOutput(BaseModel):
    """Registered named output and concrete ownership, including empty assembly."""

    model_config = ConfigDict(frozen=True)
    registration_key: str
    owner_scope: ProviderScope | None
    computations: tuple[ComputationSelection, ...]


class ResultDescription(BaseModel):
    """Effective root metadata and references without copied schemas or rows."""

    model_config = ConfigDict(frozen=True)
    metric_name: str
    code_revision: str | None = None
    parameters: JsonObject
    output_names: tuple[str, ...]
    outputs: dict[str, ResultOutput]

    @field_validator("output_names")
    @classmethod
    def validate_output_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        """Require a complete, unique inventory of declared output names.

        Returns:
            The retained inventory.

        Raises:
            ValueError: If the inventory is empty or contains invalid names.
        """
        if (
            not names
            or len(set(names)) != len(names)
            or any(not name.strip() for name in names)
        ):
            msg = "Invalid declared output inventory"
            raise ValueError(msg)
        return names


class ResultDescriptions:
    """Publish and reopen final selections using the shared value store."""

    def __init__(self, store: "SQLiteStore") -> None:
        """Borrow a store without acquiring resources."""
        self.store = store

    def publish(self, description: ResultDescription) -> str:
        """Validate required relationships and atomically publish a fresh reference.

        Returns:
            A distinct opaque reference after a successful commit.
        """
        self.store.load_registrations()
        reference = uuid4().hex
        with self.store.transaction() as connection:
            self._assemble(connection, description)
            connection.execute(
                self.store.layout.final_results.insert(),
                {"result_ref": reference, "description": description.model_dump_json()},
            )
        return reference

    def read(
        self, reference: str, output_name: str | None = None
    ) -> tuple[ResultDescription, "NativeOutputs"]:
        """Reopen detached metadata and native tables without producer code.

        Returns:
            The execution description and its selected named tables.

        Raises:
            MissingValueError: If the description is absent.
            DamagedDataError: If required metadata or values are damaged.
        """
        table = self.store.layout.final_results
        with self.store.engine.connect() as connection:
            try:
                row = (
                    connection.execute(
                        sa.select(table).where(table.c.result_ref == reference)
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise MissingValueError(reference)
                description = ResultDescription.model_validate_json(row["description"])
                return description, self._read_outputs(
                    connection, description, output_name
                )
            except (
                ValueError,
                KeyError,
                TypeError,
                exc.DatabaseError,
                GEOSException,
            ) as error:
                msg = "Damaged final-result description"
                raise DamagedDataError(msg) from error

    def _read_outputs(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        output_name: str | None,
    ) -> "NativeOutputs":
        if output_name is not None and output_name not in description.output_names:
            raise MissingValueError(output_name)
        if output_name is None:
            if set(description.outputs) != set(description.output_names):
                msg = "Incomplete final output selections"
                raise DamagedDataError(msg)
        elif output_name not in description.outputs:
            msg = "Missing declared output selection"
            raise DamagedDataError(msg)
        self.store.load_registrations(
            tuple(
                output.registration_key
                for name, output in description.outputs.items()
                if output_name is None or name == output_name
            )
        )
        return self._assemble(connection, description, output_name, check_names=False)

    def _assemble(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        output_name: str | None = None,
        *,
        check_names: bool = True,
    ) -> "NativeOutputs":
        outputs = {}
        tables = MetricTables(self.store)
        if not description.outputs or (
            check_names and set(description.outputs) != set(description.output_names)
        ):
            msg = "Final result requires every registered named output"
            raise DamagedDataError(msg)
        for name, output in description.outputs.items():
            if output_name is not None and name != output_name:
                continue
            registration = self.store.layout.descriptions.get(output.registration_key)
            if (
                registration is None
                or registration.kind != "metric"
                or registration.node_id != description.metric_name
                or registration.output_name != name
                or (registration.output_scope == "location")
                != (output.owner_scope is not None)
            ):
                msg = "Invalid final output registration or scope"
                raise DamagedDataError(msg)
            if (
                registration.output_scope == "unpartitioned"
                and len(output.computations) != 1
            ):
                msg = "Whole-table results require one computation"
                raise DamagedDataError(msg)
            frames = []
            for selection in output.computations:
                header = self.store.header(
                    connection, selection.value_key, required=True
                )
                if (
                    header.node_id != description.metric_name
                    or header.output_scope != registration.output_scope
                    or header.owner_scope != output.owner_scope
                    or (
                        header.output_scope == "unpartitioned"
                        and selection.groups is not None
                    )
                ):
                    msg = "Final output computation scope differs"
                    raise DamagedDataError(msg)
                frames.append(
                    tables.read_connection(connection, header, name, selection.groups)[
                        name
                    ]
                )
            outputs[name] = (
                pd.concat(frames, ignore_index=True)
                if frames
                else frame_from_rows([], registration.storage_description())
            )
        return outputs
