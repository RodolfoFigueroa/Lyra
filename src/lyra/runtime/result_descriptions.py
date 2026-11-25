"""Immutable execution descriptions selecting shared metric computations."""

from contextlib import contextmanager
from copy import deepcopy
from typing import TYPE_CHECKING

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.provider_scope import DatasetScope
from lyra.contracts.references import validate_job_id
from lyra.contracts.reports import ExecutionReport, ReportOutput
from lyra.contracts.results import ResultDescription as PublicResultDescription
from lyra.contracts.types import JsonObject
from lyra.contracts.value_identity import same_json
from lyra.runtime.location_inputs import (
    InputBindings,
    _input_traversal,
    _InputTraversal,
)
from lyra.runtime.output_selection import ComputationReader, OutputSelection
from lyra.runtime.sqlite_store import (
    DamagedDataError,
    MissingValueError,
    select_from_optional_store,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from lyra.contracts.reports import ReportNode
    from lyra.data.results import NativeOutputs
    from lyra.runtime.sqlite_schema import Registration
    from lyra.runtime.sqlite_store import Header, SQLiteStore


class ComputationSelection(BaseModel):
    """A complete computation or selected groups from that computation."""

    model_config = ConfigDict(frozen=True)
    value_key: str
    groups: tuple[str, ...] | None = None


class ResultOutput(BaseModel):
    """Registered named output and concrete ownership, including empty assembly."""

    model_config = ConfigDict(frozen=True)
    registration_key: str
    owner_scope: DatasetScope | None
    computations: tuple[ComputationSelection, ...]


class ResultDescription(BaseModel):
    """Effective root metadata and references without copied schemas or rows."""

    model_config = ConfigDict(frozen=True)
    metric_name: str
    code_revision: str | None = None
    parameters: JsonObject
    output_names: tuple[str, ...]
    outputs: dict[str, ResultOutput]
    location_inputs: InputBindings = Field(default_factory=InputBindings)
    input_computations: tuple[ComputationSelection, ...] = ()

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
        """Validate and publish the report and final selections in one transaction.

        Returns:
            The job ID after the transaction commits.
        """
        return (
            _ResultPublication(self.store).publish_metadata(description, report).job_id
        )

    @staticmethod
    def _check_pair(
        description: ResultDescription, report: ExecutionReport
    ) -> "ReportNode":
        root = next(node for node in report.nodes if node.use_id == report.root_use_id)
        if description.outputs.keys() != set(description.output_names):
            msg = "Incomplete final output selections"
            raise DamagedDataError(msg)
        if (
            description.outputs.keys() != root.outputs.keys()
            or description.metric_name != root.node_id
            or not same_json(description.parameters, root.parameters)
            or description.code_revision != root.code_revision
            or any(
                output.owner_scope != root.outputs[name].owner_scope
                for name, output in description.outputs.items()
            )
        ):
            msg = "Final description and report disagree"
            raise DamagedDataError(msg)

        return root

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
        if report.job_id != row["job_id"]:
            msg = "Final report identity differs from its record"
            raise DamagedDataError(msg)
        cls._check_pair(description, report)
        return description, report

    def job_outputs(
        self, job_id: str, metric: str
    ) -> tuple["NativeOutputs", dict[str, OutputMetadata]]:
        """Return the accepted metric's declarations and native tables in one traversal.

        Raises:
            DamagedDataError: If the retained identity differs from the queue record.
        """
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id)
            description = ResultDescription.model_validate_json(row["description"])
            if row["job_id"] != job_id or description.metric_name != metric:
                msg = "Retained result differs from the accepted job"
                raise DamagedDataError(msg)
            self._load_outputs(description, None)
            tables = {}
            declarations = {}
            for name in description.outputs:
                selected = self._selection(connection, description, name)
                declarations[name] = OutputMetadata.model_validate(
                    selected.registration.table_schema
                ).model_copy(deep=True)
                tables[name] = selected.frame()
            return tables, declarations

    def read_report(self, job_id: str) -> ExecutionReport:
        """Return the retained report without reading output values."""
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id, include_report=True)
        _, report = self.decode(row)
        return report

    def read(
        self, job_id: str, output_name: str | None = None
    ) -> tuple[ResultDescription, "NativeOutputs"]:
        """Return selected native tables without decoding the complete report."""
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id)
            description = ResultDescription.model_validate_json(row["description"])
            return description, self._read_outputs(connection, description, output_name)

    @contextmanager
    def select(self, job_id: str, output_name: str) -> "Generator[OutputSelection]":
        """Yield a validated named SQL selection with an owned connection.

        Required metadata and aggregate completion checks finish before yielding.
        The connection closes on completion, exceptions and early consumer exit.

        Yields:
            Retained schema, concrete scope, and bounded row traversal.
        """
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id)
            description = ResultDescription.model_validate_json(row["description"])
            self._load_outputs(description, output_name)
            yield self._selection(connection, description, output_name)

    def _selection(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        name: str,
        *,
        check_overlap: bool = False,
    ) -> OutputSelection:
        output = description.outputs[name]
        registration = self._registration(description, name, output)
        claimed: set[str] = set()
        computations = []
        reader = ComputationReader(self.store, connection, registration)
        for selection in output.computations:
            header = self._header(
                connection, description, output, registration, selection
            )
            computations.append(reader.prepare(header, selection.groups))
            if check_overlap and header.output_scope == "location":
                counts = (header.output_rows or {})[name]
                if isinstance(counts, dict):
                    groups = (
                        counts.keys() if selection.groups is None else selection.groups
                    )
                    if not claimed.isdisjoint(groups):
                        msg = "Overlapping final output partitions"
                        raise DamagedDataError(msg)
                    claimed.update(groups)
        return OutputSelection(
            connection, registration, output.owner_scope, tuple(computations)
        )

    def _result_record(
        self, connection: sa.Connection, job_id: str, *, include_report: bool = False
    ) -> sa.RowMapping:
        table = self.store.layout.final_results
        report = (
            table.c.report
            if include_report
            else table.c.report.is_not(None).label("report_present")
        )
        query = sa.select(table.c.job_id, table.c.description, report).where(
            table.c.job_id == job_id
        )
        row = (
            select_from_optional_store(connection, table, query)
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MissingValueError(job_id)
        validate_job_id(row["job_id"])
        # The report belongs to this unique execution/result record. Its body is
        # validated only by report and complete-result reads.
        if not include_report and not row["report_present"]:
            msg = "Missing required execution report"
            raise DamagedDataError(msg)
        return row

    def _inputs(
        self,
        connection: sa.Connection,
        description: ResultDescription,
        inputs: _InputTraversal,
    ) -> InputBindings:
        bindings = [description.location_inputs.selected()]
        for selection in description.input_computations:
            header = self.store.header(connection, selection.value_key, required=True)
            counts = header.output_rows
            groups = selection.groups
            invalid_groups = groups is not None and (
                header.output_scope != "location"
                or counts is None
                or any(
                    not isinstance(count, dict) or not set(groups) <= count.keys()
                    for count in counts.values()
                )
            )
            if header.node_id != description.metric_name or invalid_groups:
                msg = "Input computation belongs to another metric"
                raise DamagedDataError(msg)
            bindings.append(
                inputs.borrow_bindings(connection, selection.value_key).selected(
                    selection.groups
                )
            )
        names = tuple(
            dict.fromkeys(name for binding in bindings for name in binding.names)
        )
        references = {
            name: tuple(
                dict.fromkeys(
                    key
                    for binding in bindings
                    for key in binding.selections.get(name, ())
                )
            )
            for name in names
        }
        result = InputBindings(names=names, selections=references)
        result.validate_inventory()
        return result

    def input_bindings(self, job_id: str) -> InputBindings:
        """Load the declared input inventory without outputs or the report body.

        Returns:
            Complete captured input references.
        """
        with (
            self.store.engine.connect() as connection,
            _input_traversal(self.store, connection) as inputs,
        ):
            return self.input_bindings_connection(connection, job_id, inputs)

    def input_bindings_connection(
        self, connection: sa.Connection, job_id: str, inputs: _InputTraversal
    ) -> InputBindings:
        """Assemble inputs within an existing private metadata traversal.

        Returns:
            An independently owned merged inventory.
        """
        row = self._result_record(connection, job_id)
        description = ResultDescription.model_validate_json(row["description"])
        return self._inputs(connection, description, inputs)

    def validate_output_metadata(self, job_id: str, name: str) -> None:
        """Resolve a named output using only its retained metadata."""
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id)
            description = ResultDescription.model_validate_json(row["description"])
            self._load_outputs(description, name)
            self._check_output_metadata(connection, description, name)

    def _check_output_metadata(
        self, connection: sa.Connection, description: ResultDescription, name: str
    ) -> None:
        output = description.outputs[name]
        registration = self._registration(description, name, output)
        reader = ComputationReader(self.store, connection, registration)
        for selection in output.computations:
            header = self._header(
                connection, description, output, registration, selection
            )
            reader.table(header)
            counts = (header.output_rows or {}).get(name)
            if counts is None or (
                selection.groups is not None
                and (
                    not isinstance(counts, dict)
                    or not set(selection.groups) <= counts.keys()
                )
            ):
                msg = "Missing retained output completion"
                raise DamagedDataError(msg)

    def describe(self, job_id: str) -> PublicResultDescription:
        """Return complete retained metadata without reading reports or value rows."""
        with self.store.engine.connect() as connection:
            row = self._result_record(connection, job_id)
            description = ResultDescription.model_validate_json(row["description"])
            self._load_outputs(description, None)
            for name in description.outputs:
                self._check_output_metadata(connection, description, name)
            with _input_traversal(self.store, connection) as inputs:
                return self._metadata(connection, inputs, description, job_id)

    def metadata(
        self, description: ResultDescription, job_id: str
    ) -> PublicResultDescription:
        """Project loaded registrations into independent public output metadata.

        Returns:
            A complete description without private selections or local paths.
        """
        with (
            self.store.engine.connect() as connection,
            _input_traversal(self.store, connection) as inputs,
        ):
            return self._metadata(connection, inputs, description, job_id)

    def _metadata(
        self,
        connection: sa.Connection,
        retained: _InputTraversal,
        description: ResultDescription,
        job_id: str,
    ) -> PublicResultDescription:
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
        inputs = retained.descriptions(
            connection, self._inputs(connection, description, retained)
        )
        return PublicResultDescription(
            metric_name=description.metric_name,
            parameters=deepcopy(description.parameters),
            outputs=outputs,
            location_inputs=inputs,
            job_id=job_id,
        )

    def read_complete(
        self, job_id: str, output_name: str | None = None
    ) -> tuple[ResultDescription, ExecutionReport, "NativeOutputs"]:
        """Reopen detached metadata and native tables without producer code.

        Returns:
            The execution description and its selected named tables.

        Raises:
            DamagedDataError: If required metadata or values are damaged.
        """
        with self.store.engine.connect() as connection:
            try:
                row = self._result_record(connection, job_id, include_report=True)
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
        return {
            name: self._selection(connection, description, name).frame()
            for name in description.outputs
            if output_name is None or name == output_name
        }

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
            or (header.owner_scope, header.target_scope) != (output.owner_scope, None)
            or (header.output_scope == "unpartitioned" and selection.groups is not None)
        ):
            msg = "Final output computation scope differs"
            raise DamagedDataError(msg)
        return header


class _ResultPublication(ResultDescriptions):
    """Private final-result transaction returning owned publication metadata."""

    def publish_metadata(
        self,
        description: ResultDescription,
        report: ExecutionReport,
        registrations: tuple["Registration", ...] = (),
        on_publish: "Callable[[sa.Connection], None] | None" = None,
    ) -> PublicResultDescription:
        """Publish the final selections and report under their job ID.

        Returns:
            The independently owned public description after commit.

        Raises:
            DamagedDataError: If registered and reported output schemas differ.
        """
        job_id = report.job_id
        root = self._check_pair(description, report)
        missing = tuple(
            r for r in registrations if r.key not in self.store.layout.descriptions
        )
        transaction = (
            self.store.schema_transaction() if missing else self.store.transaction()
        )
        with transaction as connection:
            if missing:
                self.store.register(connection, missing)
            for name, output in description.outputs.items():
                self.store.read_registration(connection, output.registration_key)
                self._selection(connection, description, name, check_overlap=True)
            with _input_traversal(self.store, connection) as inputs:
                projection = self._metadata(connection, inputs, description, job_id)
            if any(
                output.scope != root.outputs[name].scope
                or not same_json(
                    output.table_schema.model_dump(mode="json"),
                    root.outputs[name].table_schema.model_dump(mode="json"),
                )
                for name, output in projection.outputs.items()
            ):
                msg = "Final output schema and report disagree"
                raise DamagedDataError(msg)
            if {
                name: binding.provider_scope
                for name, binding in root.inputs.items()
                if binding.provider_scope is not None
            } != {
                name: retained.provider_scope
                for name, retained in projection.location_inputs.items()
            }:
                msg = "Final location inputs and report disagree"
                raise DamagedDataError(msg)
            connection.execute(
                self.store.layout.final_results.insert(),
                {
                    "job_id": job_id,
                    "report": report.model_dump_json(),
                    "description": description.model_dump_json(),
                },
            )
            if on_publish is not None:
                on_publish(connection)
        return projection
