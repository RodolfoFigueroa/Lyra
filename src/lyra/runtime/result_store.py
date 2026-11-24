"""Read-only facade over the shared retained-value store."""

from contextlib import closing, contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast

import pyarrow as pa
from shapely.errors import GEOSException
from sqlalchemy.exc import SQLAlchemyError

from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ReportIntegrityError,
    ReportNotFoundError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.references import (
    validate_job_id,
    validate_retrieval_name,
)
from lyra.contracts.reports import ExecutionReport
from lyra.contracts.results import ResultDescription
from lyra.data.arrow_tables import table_batch, table_schema
from lyra.data.geoparquet import export_retained_geoparquet
from lyra.runtime.input_selection import InputRows
from lyra.runtime.location_inputs import _input_traversal
from lyra.runtime.pagination import (
    CandidatePage,
    candidates,
    validate_pagination,
    validate_position,
)
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import RunMetadata, RunResult
from lyra.runtime.sqlite_store import DamagedDataError, MissingValueError, SQLiteStore

if TYPE_CHECKING:
    from collections.abc import Generator

    import geopandas as gpd
    import pandas as pd
    from sqlalchemy import Connection

    from lyra.contracts.provider_scope import DatasetScope
    from lyra.runtime.location_inputs import InputSelection
    from lyra.runtime.output_selection import OutputSelection


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
        self, job_id: str, output_name: str | None, *, input_name: str | None = None
    ) -> "Generator[ResultDescriptions]":
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        validate_job_id(job_id)
        try:
            yield ResultDescriptions(self._store)
        except MissingValueError as error:
            raise ResultNotFoundError(
                job_id, output_name, input_name=input_name
            ) from error
        except (
            DamagedDataError,
            SQLAlchemyError,
            ValueError,
            KeyError,
            TypeError,
            GEOSException,
        ) as error:
            raise ResultIntegrityError(
                job_id, output_name, input_name=input_name
            ) from error

    def describe_result(self, job_id: str) -> ResultDescription:
        """Return complete public metadata without reading tables or report bodies."""
        with self._lookup(job_id, None) as lookup:
            return lookup.describe(job_id)

    def describe(self, job_id: str) -> ResultDescription:
        """Return the complete description through the description-only reader."""
        return self.describe_result(job_id)

    def read_result(self, job_id: str) -> RunResult:
        """Return native outputs, detached metadata, and an eager complete report.

        The attached metadata retains the local store path for reopening.
        """
        with self._lookup(job_id, None) as lookup:
            description, report, tables = lookup.read_complete(job_id)
            metadata = lookup.metadata(description, job_id)
            return RunResult(
                tables=tables,
                metadata=RunMetadata(
                    **metadata.model_dump(), store_path=self._store.path
                ),
                report=report,
            )

    def read_report(self, job_id: str) -> ExecutionReport:
        """Return a detached complete report without loading stored output tables.

        Raises:
            RuntimeError: If this reader is closed.
            ReportNotFoundError: If no report is retained for the ID.
            ReportIntegrityError: If the report or final description is damaged.
        """
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        validate_job_id(job_id)
        try:
            return ResultDescriptions(self._store).read_report(job_id)
        except MissingValueError as error:
            raise ReportNotFoundError(job_id) from error
        except (
            DamagedDataError,
            SQLAlchemyError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            raise ReportIntegrityError(job_id) from error

    def read_table(self, job_id: str, output_name: str) -> "pd.DataFrame":
        """Return one independent native output, including typed empty tables."""
        validate_retrieval_name(output_name)
        with self._lookup(job_id, output_name) as lookup:
            return lookup.read(job_id, output_name)[1][output_name]

    def export_geoparquet(
        self, job_id: str, output_name: str, path: str | Path
    ) -> None:
        """Export one retained spatial output and its description to a local file.

        Requires an existing parent directory and replaces the destination only
        after a successful write. No handlers, providers, or reports are loaded.
        """
        frame = self.read_table(job_id, output_name)
        description = self.describe(job_id)
        export_retained_geoparquet(
            frame, description, "metric_output", output_name, path
        )

    def export_location_input_geoparquet(
        self, job_id: str, input_name: str, path: str | Path
    ) -> None:
        """Export a retained input and its complete description to a local file.

        Requires an existing parent directory. Retrieval performs no acquisition;
        the destination is replaced only after the shared writer succeeds.
        """
        frame = self.read_location_input(job_id, input_name)
        description = self.describe(job_id)
        export_retained_geoparquet(
            frame, description, "location_input", input_name, path
        )

    def read_location_input(self, job_id: str, input_name: str) -> "gpd.GeoDataFrame":
        """Return a detached geographic input using only retained values."""
        with self._select_location_input(job_id, input_name) as selected:
            return cast("gpd.GeoDataFrame", selected.frame())

    @contextmanager
    def _select_location_input(
        self, job_id: str, input_name: str
    ) -> "Generator[InputRows]":
        with self._location_input_metadata(job_id, input_name) as (
            connection,
            selections,
        ):
            yield InputRows.prepare(self._store, connection, selections)

    @contextmanager
    def _location_input_metadata(
        self, job_id: str, input_name: str
    ) -> "Generator[tuple[Connection, tuple[InputSelection, ...]]]":
        validate_retrieval_name(input_name)
        with (
            self._lookup(job_id, None, input_name=input_name) as lookup,
            self._store.engine.connect() as connection,
            _input_traversal(self._store, connection) as inputs,
        ):
            bindings = lookup.input_bindings_connection(connection, job_id, inputs)
            if input_name not in bindings.names:
                raise MissingValueError(input_name)
            selections = inputs.borrow_selections(
                connection, bindings.selections[input_name]
            )
            if selections[0].description.availability == "not_materialized":
                raise LocationInputUnavailableError(job_id, input_name)
            yield connection, selections

    def validate_location_input(self, job_id: str, input_name: str) -> None:
        """Check the saved metadata to confirm that the location input is available."""
        with self._location_input_metadata(job_id, input_name):
            pass

    @contextmanager
    def location_input_page_candidates(
        self,
        job_id: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> "Generator[CandidatePage]":
        """Yield bounded candidates with an input-specific opaque continuation.

        The context owns the connection and iterator, including on early exit.
        """
        validate_retrieval_name(input_name)
        validate_job_id(job_id)
        after = validate_pagination(job_id, None, limit, cursor, input_name=input_name)
        with self._select_location_input(job_id, input_name) as selected:
            validate_position(selected, job_id, None, after, input_name=input_name)
            with closing(
                candidates(
                    selected,
                    job_id,
                    input_name,
                    limit,
                    after,
                )
            ) as rows:
                yield CandidatePage(selected.registration, None, rows)

    def stream_location_input(self, job_id: str, input_name: str) -> "Generator[bytes]":
        """Generate Arrow with lookup/availability preflight before the first chunk.

        Close the generator on early exit. The selection, batches, writer and
        connection remain owned until exhaustion or close, as for output streams.

        Yields:
            Arrow IPC chunks with retained schema and GeoArrow geometry metadata.
        """
        with self._select_location_input(job_id, input_name) as selected:
            description = selected.selections[0].description
            yield from self._stream_selection(
                selected,
                provider_scope=description.provider_scope,
                source_scope=description.source_scope,
            )

    def validate_table(self, job_id: str, output_name: str) -> None:
        """Check the saved metadata to confirm that the named output exists."""
        validate_retrieval_name(output_name)
        with self._lookup(job_id, output_name) as lookup:
            lookup.validate_output_metadata(job_id, output_name)

    @contextmanager
    def page_candidates(
        self,
        job_id: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> "Generator[CandidatePage]":
        """Yield internal SQL page candidates with shared lookup error handling."""
        validate_retrieval_name(output_name)
        with (
            self._lookup(job_id, output_name) as lookup,
            lookup.page_candidates(job_id, output_name, limit, cursor) as page,
        ):
            yield page

    def stream_table(self, job_id: str, output_name: str) -> "Generator[bytes]":
        """Yield a complete Arrow IPC stream, owning resources until closed.

        The first chunk validates the lookup and contains the declared schema.
        Subsequent chunks read bounded SQL batches. Consumers must close the
        generator on early exits; normal exhaustion releases all resources.
        """
        validate_retrieval_name(output_name)
        with (
            self._lookup(job_id, output_name) as lookup,
            lookup.select(job_id, output_name) as selected,
        ):
            yield from self._stream_selection(
                selected, owner_scope=selected.owner_scope
            )

    @staticmethod
    def _stream_selection(
        selected: "OutputSelection | InputRows",
        **scopes: "DatasetScope | None",
    ) -> "Generator[bytes]":
        with (
            closing(selected.batches()) as batches,
            BytesIO() as sink,
        ):
            description = selected.registration.storage_description()
            schema = table_schema(selected.registration.arrow_schema, **scopes)
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
