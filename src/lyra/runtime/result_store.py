"""Read-only facade over the shared retained-value store."""

from contextlib import closing, contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Self

import pyarrow as pa
from shapely.errors import GEOSException
from sqlalchemy.exc import SQLAlchemyError

from lyra.contracts.errors import (
    ReportIntegrityError,
    ReportNotFoundError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.references import validate_execution_id, validate_result_reference
from lyra.contracts.reports import ExecutionReport
from lyra.contracts.results import ResultDescription
from lyra.data.arrow_tables import table_batch, table_schema
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import RunMetadata, RunResult
from lyra.runtime.sqlite_store import DamagedDataError, MissingValueError, SQLiteStore

if TYPE_CHECKING:
    from collections.abc import Generator

    import pandas as pd

    from lyra.runtime.pagination import CandidatePage


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

    @contextmanager
    def _lookup(
        self, result_ref: str, output_name: str | None
    ) -> "Generator[ResultDescriptions]":
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        validate_result_reference(result_ref, output_name)
        try:
            yield ResultDescriptions(self._store)
        except MissingValueError as error:
            raise ResultNotFoundError(result_ref, output_name) from error
        except (
            DamagedDataError,
            SQLAlchemyError,
            ValueError,
            KeyError,
            TypeError,
            GEOSException,
        ) as error:
            raise ResultIntegrityError(result_ref, output_name) from error

    def describe(self, result_ref: str) -> ResultDescription:
        """Return complete public metadata without reading tables or report bodies."""
        with self._lookup(result_ref, None) as lookup:
            return lookup.describe(result_ref)

    def read_result(self, result_ref: str) -> RunResult:
        """Return native outputs, detached metadata, and an eager complete report.

        The attached metadata retains the local store path for reopening.
        """
        with self._lookup(result_ref, None) as lookup:
            description, report, tables = lookup.read_complete(result_ref)
            metadata = lookup.metadata(description, result_ref, report.execution_id)
            return RunResult(
                tables=tables,
                metadata=RunMetadata(
                    **metadata.model_dump(), store_path=self._store.path
                ),
                report=report,
            )

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

    def read_table(self, result_ref: str, output_name: str) -> "pd.DataFrame":
        """Return one independent native output, including typed empty tables."""
        with self._lookup(result_ref, output_name) as lookup:
            return lookup.read(result_ref, output_name)[1][output_name]

    def validate_table(self, result_ref: str, output_name: str) -> None:
        """Validate a retained output without materializing its rows or report."""
        with (
            self._lookup(result_ref, output_name) as lookup,
            lookup.select(result_ref, output_name),
        ):
            pass

    @contextmanager
    def page_candidates(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> "Generator[CandidatePage]":
        """Yield internal SQL page candidates with shared lookup error handling."""
        with (
            self._lookup(result_ref, output_name) as lookup,
            lookup.page_candidates(result_ref, output_name, limit, cursor) as page,
        ):
            yield page

    def stream_table(self, result_ref: str, output_name: str) -> "Generator[bytes]":
        """Yield a complete Arrow IPC stream, owning resources until closed.

        The first chunk validates the lookup and contains the declared schema.
        Subsequent chunks read bounded SQL batches. Consumers must close the
        generator on early exits; normal exhaustion releases all resources.
        """
        with (
            self._lookup(result_ref, output_name) as lookup,
            lookup.select(result_ref, output_name) as selected,
            closing(selected.batches()) as batches,
            BytesIO() as sink,
        ):
            description = selected.registration.storage_description()
            schema = table_schema(description)
            with pa.ipc.new_stream(sink, schema) as writer:
                # An empty batch emits the schema before any value reads, including
                # for a known empty output. IPC stream framing is filename agnostic.
                writer.write_batch(
                    pa.RecordBatch.from_arrays(
                        [pa.array([], type=field.type) for field in schema],
                        schema=schema,
                    )
                )
                yield sink.getvalue()
                sink.seek(0)
                sink.truncate()
                for batch in batches:
                    writer.write_batch(table_batch(batch.rows, description, schema))
                    yield sink.getvalue()
                    sink.seek(0)
                    sink.truncate()
            yield sink.getvalue()
