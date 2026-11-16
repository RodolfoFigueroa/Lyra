"""Immutable execution descriptions selecting shared metric computations."""

from contextlib import closing, contextmanager
from copy import deepcopy
from typing import TYPE_CHECKING

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.references import ExecutionID, ResultReference
from lyra.contracts.reports import ExecutionReport, ReportOutput
from lyra.contracts.results import ResultDescription as PublicResultDescription
from lyra.contracts.types import JsonObject
from lyra.runtime.output_selection import OutputSelection, prepare_computation
from lyra.runtime.pagination import (
    CandidatePage,
    candidates,
    validate_pagination,
    validate_position,
)
from lyra.runtime.sqlite_store import DamagedDataError, MissingValueError

if TYPE_CHECKING:
    from collections.abc import Generator

    from lyra.data.results import NativeOutputs
    from lyra.runtime.sqlite_schema import Registration
    from lyra.runtime.sqlite_store import Header, SQLiteStore

_EXECUTION_ID = TypeAdapter(ExecutionID)
_RESULT_REFERENCE = TypeAdapter(ResultReference)


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

    def publish(self, description: ResultDescription, report: ExecutionReport) -> str:
        """Validate required relationships and atomically publish a fresh reference.

        Returns:
            A distinct opaque reference after a successful commit.
        """
        self.store.load_registrations()
        reference = report.result_ref
        self._check_pair(description, report)
        with self.store.transaction() as connection:
            self._assemble(connection, description)
            connection.execute(
                self.store.layout.final_results.insert(),
                {
                    "result_ref": reference,
                    "execution_id": report.execution_id,
                    "report": report.model_dump_json(),
                    "description": description.model_dump_json(),
                },
            )
        return reference

    @staticmethod
    def _check_pair(description: ResultDescription, report: ExecutionReport) -> None:
        root = next(node for node in report.nodes if node.use_id == report.root_use_id)
        if (
            description.metric_name != root.node_id
            or description.parameters != root.parameters
            or description.code_revision != root.code_revision
            or set(description.output_names) != set(root.outputs)
            or any(
                name not in root.outputs
                or output.owner_scope != root.outputs[name].owner_scope
                for name, output in description.outputs.items()
            )
        ):
            msg = "Final description and report disagree"
            raise DamagedDataError(msg)

    @classmethod
    def decode(cls, row: sa.RowMapping) -> tuple[ResultDescription, ExecutionReport]:
        """Decode a retained pair and check its required execution relationships.

        Returns:
            Independent description and complete report values.

        Raises:
            DamagedDataError: If the pair's identities or descriptions disagree.
        """
        description = ResultDescription.model_validate_json(row["description"])
        report = ExecutionReport.model_validate_json(row["report"])
        if (
            report.execution_id != row["execution_id"]
            or report.result_ref != row["result_ref"]
        ):
            msg = "Final report identity differs from its record"
            raise DamagedDataError(msg)
        cls._check_pair(description, report)
        return description, report

    def read_report(self, execution_id: str) -> ExecutionReport:
        """Decode the complete retained pair without accessing output values.

        Returns:
            An independently decoded report.

        Raises:
            MissingValueError: If the execution has no retained report.
            DamagedDataError: If the final output selections are incomplete.
        """
        table = self.store.layout.final_results
        with self.store.engine.connect() as connection:
            row = (
                connection.execute(
                    sa.select(table).where(table.c.execution_id == execution_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise MissingValueError(execution_id)
        description, report = self.decode(row)
        if set(description.outputs) != set(description.output_names):
            msg = "Incomplete final output selections"
            raise DamagedDataError(msg)
        return report

    def read(
        self, reference: str, output_name: str | None = None
    ) -> tuple[ResultDescription, "NativeOutputs"]:
        """Return selected native tables without decoding the complete report."""
        with self.store.engine.connect() as connection:
            row = self._description_record(connection, reference)
            description = ResultDescription.model_validate_json(row["description"])
            return description, self._read_outputs(connection, description, output_name)

    @contextmanager
    def select(self, reference: str, output_name: str) -> "Generator[OutputSelection]":
        """Yield a validated named SQL selection with an owned connection.

        Required metadata and aggregate completion checks finish before yielding.
        The connection closes on completion, exceptions and early consumer exit.

        Yields:
            Retained schema, concrete scope, and bounded row traversal.
        """
        with self.store.engine.connect() as connection:
            row = self._description_record(connection, reference)
            description = ResultDescription.model_validate_json(row["description"])
            self._load_outputs(description, output_name)
            yield self._selection(connection, description, output_name)

    @contextmanager
    def page_candidates(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> "Generator[CandidatePage]":
        """Yield internal SQL candidates with continuations and retained schema.

        Consumers must keep this context open while iterating. Early exits close
        the iterator and connection, including when a byte budget rejects a row.
        """
        after = validate_pagination(result_ref, output_name, limit, cursor)
        with self.select(result_ref, output_name) as selected:
            validate_position(selected, result_ref, output_name, after)
            with closing(
                candidates(selected, result_ref, output_name, limit, after)
            ) as rows:
                yield CandidatePage(selected.registration, selected.owner_scope, rows)

    def _selection(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        name: str,
    ) -> OutputSelection:
        output = description.outputs[name]
        registration = self._registration(description, name, output)
        return OutputSelection(
            connection,
            registration,
            output.owner_scope,
            tuple(
                prepare_computation(
                    self.store,
                    connection,
                    registration,
                    self._header(
                        connection, description, output, registration, selection
                    ),
                    selection.groups,
                )
                for selection in output.computations
            ),
        )

    def _description_record(
        self, connection: sa.Connection, reference: str
    ) -> sa.RowMapping:
        table = self.store.layout.final_results
        row = (
            connection.execute(
                sa.select(
                    table.c.result_ref,
                    table.c.execution_id,
                    table.c.description,
                    table.c.report.is_not(None).label("report_present"),
                ).where(table.c.result_ref == reference)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MissingValueError(reference)
        _RESULT_REFERENCE.validate_python(row["result_ref"])
        _EXECUTION_ID.validate_python(row["execution_id"])
        # The report belongs to this unique execution/result record. Its body is
        # validated only by report and complete-result reads.
        if not row["report_present"]:
            msg = "Missing required execution report"
            raise DamagedDataError(msg)
        return row

    def describe(self, reference: str) -> PublicResultDescription:
        """Return complete retained metadata without reading reports or value rows."""
        with self.store.engine.connect() as connection:
            row = self._description_record(connection, reference)
            description = ResultDescription.model_validate_json(row["description"])
            self._load_outputs(description, None)
            for name, output in description.outputs.items():
                registration = self._registration(description, name, output)
                for selection in output.computations:
                    self._header(
                        connection, description, output, registration, selection
                    )
            return self.metadata(description, reference, row["execution_id"])

    def metadata(
        self, description: ResultDescription, reference: str, execution_id: str
    ) -> PublicResultDescription:
        """Project loaded registrations into independent public output metadata.

        Returns:
            A complete description without private selections or local paths.
        """
        outputs = {}
        for name, output in description.outputs.items():
            registration = self._registration(description, name, output)
            outputs[name] = ReportOutput(
                scope=registration.output_scope,
                table_schema=OutputMetadata.model_validate(
                    registration.table_schema
                ).model_copy(deep=True),
                owner_scope=deepcopy(output.owner_scope),
            )
        return PublicResultDescription(
            metric_name=description.metric_name,
            parameters=deepcopy(description.parameters),
            outputs=outputs,
            result_ref=reference,
            execution_id=execution_id,
        )

    def read_complete(
        self, reference: str, output_name: str | None = None
    ) -> tuple[ResultDescription, ExecutionReport, "NativeOutputs"]:
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
                description, report = self.decode(row)
                return (
                    description,
                    report,
                    self._read_outputs(connection, description, output_name),
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
        self._load_outputs(description, output_name)
        return self._assemble(connection, description, output_name, check_names=False)

    def _load_outputs(
        self, description: ResultDescription, output_name: str | None
    ) -> None:
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

    def _registration(
        self, description: ResultDescription, name: str, output: ResultOutput
    ) -> "Registration":
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
        if registration.table_schema is None:
            msg = "Missing retained output schema"
            raise DamagedDataError(msg)
        if (
            registration.output_scope == "unpartitioned"
            and len(output.computations) != 1
        ):
            msg = "Whole-table results require one computation"
            raise DamagedDataError(msg)
        return registration

    def _header(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        output: ResultOutput,
        registration: "Registration",
        selection: ComputationSelection,
    ) -> "Header":
        header = self.store.header(connection, selection.value_key, required=True)
        if (
            header.node_id != description.metric_name
            or header.output_scope != registration.output_scope
            or header.owner_scope != output.owner_scope
            or (header.output_scope == "unpartitioned" and selection.groups is not None)
        ):
            msg = "Final output computation scope differs"
            raise DamagedDataError(msg)
        return header

    def _assemble(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        output_name: str | None = None,
        *,
        check_names: bool = True,
    ) -> "NativeOutputs":
        outputs = {}
        if not description.outputs or (
            check_names and set(description.outputs) != set(description.output_names)
        ):
            msg = "Final result requires every registered named output"
            raise DamagedDataError(msg)
        for name in description.outputs:
            if output_name is not None and name != output_name:
                continue
            outputs[name] = self._selection(connection, description, name).frame()
        return outputs
