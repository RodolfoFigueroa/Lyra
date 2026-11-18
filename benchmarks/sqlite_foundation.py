"""Production SQLite publication on disposable local files, without services."""

from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import sqlalchemy as sa
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.sqlite_schema import Registration, StoredColumn
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.storage_schema import prepare_native


class SQLiteFoundationSuite:
    """Measure one transaction containing a header and 1,000 typed rows."""

    version = "1"
    number = 1
    warmup_time = 0

    def setup(self) -> None:
        """Initialize a disposable store outside the measured publication."""
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "results.sqlite"
        self.store = SQLiteStore(self.path)
        self.registration = Registration(
            node_id="benchmark",
            output_name="value",
            kind="metric",
            output_scope="unpartitioned",
            columns=(
                StoredColumn(
                    name="enabled", dtype="boolean", nullable=False, unit=None
                ),
            ),
        )
        self.store.initialize((self.registration,))
        self.data = self.store.layout.tables[self.registration.key]
        self.sequence = 0

    def teardown(self) -> None:
        """Close connections and remove only this benchmark's temporary file."""
        self.store.close()
        self.directory.cleanup()

    def time_publish(self) -> None:
        """Commit one new value with bulk scalar rows."""
        self.sequence += 1
        header = Header(
            value_key=str(self.sequence),
            node_id="benchmark",
            output_scope="unpartitioned",
        )
        with self.store.publication(header) as connection:
            if connection is not None:
                connection.execute(
                    self.data.table.insert(),
                    [
                        {
                            self.data.value_column: header.value_key,
                            self.data.row_column: index,
                            "enabled": True,
                        }
                        for index in range(1000)
                    ],
                )

    def peakmem_publish(self) -> None:
        """Measure process memory for the same transaction boundary."""
        self.time_publish()

    def track_retained_rows(self) -> int:
        """Return duplicate-preserving row count after repeat publication and reopen.

        Raises:
            RuntimeError: If a repeat publication fails to recognize completion.
        """
        self.time_publish()
        header = Header(
            value_key=str(self.sequence),
            node_id="benchmark",
            output_scope="unpartitioned",
        )
        with self.store.publication(header) as connection:
            if connection is not None:
                msg = "Repeated publication must retain the existing value"
                raise RuntimeError(msg)
        with SQLiteStore(self.path, read_only=True) as reader:
            reader.load_registrations()
            data = reader.layout.tables[self.registration.key]
            with reader.engine.connect() as connection:
                return connection.execute(
                    sa.select(sa.func.count()).select_from(data.table)
                ).scalar_one()


class ScopedMetricSuite:
    """Measure native location-group publication and indexed retrieval."""

    version = "2"
    params = ((100, 10000), (1, 5), ("scalar", "polygon"))
    param_names = ("rows", "columns", "geometry")
    number = 1
    warmup_time = 0

    def setup(self, rows: int, columns: int, geometry: str) -> None:
        """Prepare a disposable production store and validated scalar output."""
        self.directory = TemporaryDirectory()
        self.store = SQLiteStore(Path(self.directory.name) / "metrics.sqlite")
        self.tables = MetricTables(self.store)
        schema = pa.DataFrameSchema(
            {
                "location_id": pa.Column(
                    "string", description="Identity", metadata={"unit": None}
                ),
                "row_key": pa.Column(
                    "string", description="Identity", metadata={"unit": None}
                ),
                "value": pa.Column(
                    "Int64", description="Value", metadata={"unit": None}
                ),
            },
            strict=True,
            unique=["location_id", "row_key"],
        )
        for index in range(1, columns):
            schema = schema.add_columns({f"value_{index}": schema.columns["value"]})
        if geometry == "polygon":
            schema = schema.add_columns(
                {
                    "geometry": pa.Column(
                        Geometry(crs="EPSG:4326"),
                        description="Polygon",
                        metadata={"unit": None},
                        checks=pa.Check(
                            lambda values: values.is_valid.all(), name="valid"
                        ),
                    )
                }
            )
        frame = pd.DataFrame(
            {
                "location_id": pd.array(
                    [str(i % 100) for i in range(rows)], dtype="string"
                ),
                "row_key": pd.array([str(i) for i in range(rows)], dtype="string"),
                "value": pd.array(range(rows), dtype="Int64"),
            }
        )
        for index in range(1, columns):
            frame[f"value_{index}"] = frame["value"]
        if geometry == "polygon":
            frame = gpd.GeoDataFrame(
                frame,
                geometry=[Point(i % 100, 0).buffer(0.1) for i in range(rows)],
                crs=4326,
            )
        self.frame, self.description = prepare_native(frame, schema)
        self.groups = tuple(str(i) for i in range(100))
        self.sequence = 0
        self.header = Header(
            value_key="seed",
            node_id="scoped",
            output_scope="location",
            owner_scope=ProviderScope(provider_id="places"),
        )
        self.tables.publish(
            ((self.header, self.groups),),
            {"value": self.frame},
            {"value": self.description},
            location_inputs={self.header.value_key: InputBindings()},
        )

    def teardown(self, _rows: int, _columns: int, _geometry: str) -> None:
        """Close and delete the disposable benchmark store."""
        self.store.close()
        self.directory.cleanup()

    def time_publish(self, _rows: int, _columns: int, _geometry: str) -> None:
        """Publish a complete computation with one hundred partitions."""
        self.sequence += 1
        self.tables.publish(
            (
                (
                    self.header.model_copy(update={"value_key": str(self.sequence)}),
                    self.groups,
                ),
            ),
            {"value": self.frame},
            {"value": self.description},
            location_inputs={
                self.header.model_copy(
                    update={"value_key": str(self.sequence)}
                ).value_key: InputBindings()
            },
        )

    def time_read(self, _rows: int, _columns: int, _geometry: str) -> None:
        """Read a complete computation."""
        self.tables.lookup(self.header, required=True)

    def time_group(self, _rows: int, _columns: int, _geometry: str) -> None:
        """Read a single indexed partition."""
        self.tables.lookup(self.header, partitions=("0",), required=True)

    def peakmem_publish(self, rows: int, columns: int, geometry: str) -> None:
        """Measure memory for the publication workload."""
        self.time_publish(rows, columns, geometry)

    def time_groups(self, _rows: int, _columns: int, _geometry: str) -> None:
        """Read ten partitions together using the indexed selection."""
        self.tables.lookup(self.header, partitions=self.groups[:10], required=True)

    def peakmem_read(self, rows: int, columns: int, geometry: str) -> None:
        """Measure full reconstruction memory with retained fixtures included."""
        self.time_read(rows, columns, geometry)

    def peakmem_groups(self, rows: int, columns: int, geometry: str) -> None:
        """Measure indexed ten-group retrieval including prepared fixtures."""
        self.time_groups(rows, columns, geometry)
