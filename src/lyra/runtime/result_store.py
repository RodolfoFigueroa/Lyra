"""Read-only facade over the shared retained-value store."""

import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Self

from sqlalchemy.exc import SQLAlchemyError

from lyra.contracts.errors import (
    InvalidResultReferenceError,
    ResultIntegrityError,
    ResultNotFoundError,
)
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
    ) -> tuple["ResultDescription", "NativeOutputs"]:
        if self._closed:
            msg = "Result store is closed"
            raise RuntimeError(msg)
        if (
            not isinstance(result_ref, str)
            or re.fullmatch(r"[0-9a-f]{32}", result_ref) is None
        ):
            raise InvalidResultReferenceError(result_ref, output_name)
        try:
            return ResultDescriptions(self._store).read(result_ref, output_name)
        except MissingValueError as error:
            raise ResultNotFoundError(result_ref, output_name) from error
        except (DamagedDataError, SQLAlchemyError) as error:
            raise ResultIntegrityError(result_ref, output_name) from error

    def read_result(self, result_ref: str) -> RunResult:
        """Return all outputs and detached metadata for a retained execution.

        Raises:
            ResultIntegrityError: If required schema metadata is missing or damaged.
        """
        description, tables = self._read(result_ref, None)
        try:
            metadata = self._metadata(description, result_ref)
        except (DamagedDataError, ValueError, KeyError) as error:
            raise ResultIntegrityError(result_ref) from error
        return RunResult(tables=tables, metadata=metadata)

    def _metadata(
        self, description: "ResultDescription", result_ref: str
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
        )

    def read_table(self, result_ref: str, output_name: str) -> "pd.DataFrame":
        """Return one independent native output, including typed empty tables."""
        return self._read(result_ref, output_name)[1][output_name]
