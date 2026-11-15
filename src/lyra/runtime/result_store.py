"""Read-only facade over the shared retained-value store."""

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Self

from sqlalchemy.exc import SQLAlchemyError

from lyra.contracts.errors import (
    ReportIntegrityError,
    ReportNotFoundError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.references import validate_execution_id, validate_result_reference
from lyra.contracts.reports import ExecutionReport
from lyra.runtime.result_descriptions import ResultDescription, ResultDescriptions
from lyra.runtime.results import RunMetadata, RunOutputMetadata, RunResult
from lyra.runtime.sqlite_store import DamagedDataError, MissingValueError, SQLiteStore

if TYPE_CHECKING:
    import pandas as pd

    from lyra.data.results import NativeOutputs


class ResultStore:
    """Context-managed, plugin-independent access to retained native results."""

    def __init__(self, store: "SQLiteStore") -> None:
        """Own the configured read-only store."""
        self._store = store
        self._closed = False

    @classmethod
    def open(cls, store_path: str | Path) -> "ResultStore":
        """Return read-only access to an existing store without initializing it.

        Raises:
            FileNotFoundError: If the database path does not exist.
        """
        path = Path(store_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return cls(SQLiteStore(path, read_only=True))

    def __enter__(self) -> Self:
        """Return this owned reader."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release connections on normal and exceptional exits."""
        self.close()

    def close(self) -> None:
        """Release connections; previously returned values remain usable."""
        if not self._closed:
            self._store.close()
            self._closed = True

    def _read(
        self, result_ref: str, output_name: str | None
    ) -> tuple["ResultDescription", ExecutionReport, "NativeOutputs"]:
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        validate_result_reference(result_ref, output_name)
        try:
            return ResultDescriptions(self._store).read_complete(
                result_ref, output_name
            )
        except MissingValueError as error:
            raise ResultNotFoundError(result_ref, output_name) from error
        except (DamagedDataError, SQLAlchemyError) as error:
            raise ResultIntegrityError(result_ref, output_name) from error

    def read_result(self, result_ref: str) -> RunResult:
        """Return native outputs, detached metadata, and an eager complete report.

        Raises:
            ResultIntegrityError: If required data, metadata, or report is damaged.
        """
        description, report, tables = self._read(result_ref, None)
        try:
            metadata = self._metadata(description, result_ref, report.execution_id)
        except (DamagedDataError, ValueError, KeyError) as error:
            raise ResultIntegrityError(result_ref) from error
        return RunResult(tables=tables, metadata=metadata, report=report)

    def read_report(self, execution_id: str) -> ExecutionReport:
        """Return a detached complete report without loading stored output tables.

        Raises:
            RuntimeError: If this reader is closed.
            ReportNotFoundError: If no report is retained for the ID.
            ReportIntegrityError: If the report or final description is damaged.
        """
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        validate_execution_id(execution_id)
        try:
            return ResultDescriptions(self._store).read_report(execution_id)
        except MissingValueError as error:
            raise ReportNotFoundError(execution_id) from error
        except (
            DamagedDataError,
            SQLAlchemyError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            raise ReportIntegrityError(execution_id) from error

    def _metadata(
        self, description: "ResultDescription", result_ref: str, execution_id: str
    ) -> RunMetadata:
        outputs = {}
        for name, output in description.outputs.items():
            registration = self._store.layout.descriptions[output.registration_key]
            if registration.table_schema is None:
                msg = "Missing retained output schema"
                raise DamagedDataError(msg)
            outputs[name] = RunOutputMetadata(
                scope=registration.output_scope,
                table_schema=registration.table_schema.model_copy(deep=True),
                owner_scope=output.owner_scope.model_copy(deep=True)
                if output.owner_scope is not None
                else None,
            )
        return RunMetadata(
            metric_name=description.metric_name,
            parameters=deepcopy(description.parameters),
            outputs=outputs,
            store_path=self._store.path,
            result_ref=result_ref,
            execution_id=execution_id,
        )

    def read_table(self, result_ref: str, output_name: str) -> "pd.DataFrame":
        """Return one independent native output, including typed empty tables."""
        return self._read(result_ref, output_name)[2][output_name]
