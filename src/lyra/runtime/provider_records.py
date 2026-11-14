"""Immutable provider records in the shared relational value store."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa
from shapely.errors import GEOSException
from sqlalchemy import exc

from lyra.contracts.provider_scope import RecordIdentity
from lyra.data.relational import frame_from_rows, rows_from_validated
from lyra.runtime.sqlite_schema import Registration
from lyra.runtime.sqlite_store import DamagedDataError, Header

if TYPE_CHECKING:
    from collections.abc import Sequence

    import geopandas as gpd

    from lyra.runtime.planning.provider_registry import RegisteredProvider
    from lyra.runtime.sqlite_store import SQLiteStore


def provider_registration(provider: RegisteredProvider) -> Registration:
    """Return the typed table description shared by both acquisition paths."""
    return Registration.from_description(
        provider.scope.provider_id, "records", provider.schema.storage, kind="provider"
    )


class ProviderRecords:
    """Borrow an initialized store; callers own validated frames and reuse policy."""

    def __init__(self, store: SQLiteStore) -> None:
        """Bind the shared store without database access."""
        self.store = store

    def publish(self, provider: RegisteredProvider, frame: gpd.GeoDataFrame) -> None:
        """Atomically retain a validated batch, keeping previously published values."""
        if frame.empty:
            return
        registration = provider_registration(provider)
        if registration.key not in self.store.layout.descriptions:
            self.store.initialize((registration,))
        else:
            self.store.layout.register(registration)
        data = self.store.layout.tables[registration.key]
        rows = rows_from_validated(frame, provider.schema.storage)
        records = [
            dict(zip(provider.schema.storage.columns, row, strict=True)) for row in rows
        ]
        keys = {
            RecordIdentity(
                scope=provider.scope, location_id=str(row["location_id"])
            ).key: row
            for row in records
        }
        with self.store.transaction() as connection:
            existing = self._read(connection, provider, tuple(keys))
            pending = {key: row for key, row in keys.items() if key not in existing}
            if not pending:
                return
            connection.execute(
                self.store.layout.results.insert(),
                [self._header(provider, key).record() for key in pending],
            )
            connection.execute(
                self.store.layout.partitions.insert(),
                [
                    {"value_key": key, "location_id": row["location_id"]}
                    for key, row in pending.items()
                ],
            )
            connection.execute(
                data.table.insert(),
                [{data.value_column: key, **row} for key, row in pending.items()],
            )

    @staticmethod
    def _header(provider: RegisteredProvider, key: str) -> Header:
        return Header(
            value_key=key,
            node_id=provider.scope.provider_id,
            output_scope="location",
            owner_scope=provider.scope,
        )

    def lookup(
        self,
        provider: RegisteredProvider,
        ids: Sequence[str],
        *,
        required: bool = False,
    ) -> gpd.GeoDataFrame:
        """Return retained records in one grouped read, with absent IDs omitted.

        Raises:
            DamagedDataError: If required records or completed record data are damaged.
        """
        keys = tuple(
            dict.fromkeys(
                RecordIdentity(scope=provider.scope, location_id=location).key
                for location in ids
            )
        )
        if not keys:
            return provider.schema.empty_frame()
        self.store.load_registrations()
        with self.store.engine.connect() as connection:
            records = self._read(connection, provider, keys)
        if required and len(records) != len(keys):
            msg = "Missing required provider records"
            raise DamagedDataError(msg)
        return self._frame(provider, list(records.values()))

    @staticmethod
    def _frame(provider: RegisteredProvider, rows: list[dict]) -> gpd.GeoDataFrame:
        return cast(
            "gpd.GeoDataFrame",
            frame_from_rows(
                [
                    tuple(row[name] for name in provider.schema.storage.columns)
                    for row in rows
                ],
                provider.schema.storage,
            ),
        )

    def _read(
        self,
        connection: sa.Connection,
        provider: RegisteredProvider,
        keys: tuple[str, ...],
    ) -> dict[str, dict]:
        try:
            return self._read_records(connection, provider, keys)
        except (
            exc.DatabaseError,
            ValueError,
            KeyError,
            TypeError,
            GEOSException,
        ) as error:
            msg = "Damaged provider record data"
            raise DamagedDataError(msg) from error

    def _read_records(
        self,
        connection: sa.Connection,
        provider: RegisteredProvider,
        keys: tuple[str, ...],
    ) -> dict[str, dict]:
        layout = self.store.layout
        headers = list(
            connection.execute(
                sa.select(layout.results).where(layout.results.c.value_key.in_(keys))
            ).mappings()
        )
        if not headers:
            return {}
        registration = provider_registration(provider)
        data = layout.tables[registration.key]
        if layout.descriptions[registration.key] != registration:
            msg = "Provider schema differs from retained registration"
            raise DamagedDataError(msg)
        found = {row["value_key"] for row in headers}
        for row in headers:
            if dict(row) != self._header(provider, row["value_key"]).record():
                msg = "Provider header scope differs"
                raise DamagedDataError(msg)
        partitions = list(
            connection.execute(
                sa.select(layout.partitions).where(
                    layout.partitions.c.value_key.in_(found)
                )
            ).mappings()
        )
        rows = list(
            connection.execute(
                sa.select(data.table).where(data.table.c[data.value_column].in_(found))
            ).mappings()
        )
        if Counter(row["value_key"] for row in partitions) != Counter(
            dict.fromkeys(found, 1)
        ) or Counter(row[data.value_column] for row in rows) != Counter(
            dict.fromkeys(found, 1)
        ):
            msg = "Provider records require one partition and one row"
            raise DamagedDataError(msg)
        owners = {row["value_key"]: row["location_id"] for row in partitions}
        result = {}
        for row in rows:
            key = row[data.value_column]
            if (
                row["location_id"] != owners[key]
                or RecordIdentity(
                    scope=provider.scope, location_id=row["location_id"]
                ).key
                != key
                or any(
                    row[column.name] is None
                    for column in registration.columns
                    if not column.nullable
                )
            ):
                msg = "Invalid provider record structure"
                raise DamagedDataError(msg)
            result[key] = dict(row)
        # Decode existing data too; corruption is not a cache miss.
        self._frame(provider, list(result.values()))
        return result
