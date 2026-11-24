"""Effective source records and staged targets across a managed operation chain."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest
from shapely.geometry import Point
from typing_extensions import override

from lyra.data.interventions import Intervention
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import (
    Input,
    LocationDependency,
    LocationProvider,
    PluginDefinition,
    PostgresIO,
    ResourceConfig,
    location_operation,
    metric,
)
from lyra.sdk.query_schema import compile_query_schema
from tests.cache_helpers import SCHEMA
from tests.frame_assertions import assert_logical_equal
from tests.parquet_helpers import Places as ParquetPlaces
from tests.parquet_helpers import backend_fixture, source_fixture
from tests.postgis_provider_helpers import Places as PostGISPlaces
from tests.postgis_provider_helpers import database_config, native_postgres
from tests.provider_schema_helpers import declaration, frame
from tests.sqlite_helpers import database

__all__ = ["backend_fixture", "database", "source_fixture"]
pytestmark = pytest.mark.usefixtures("database")


class Seeds(LocationProvider):
    id = "seeds"
    schema = declaration()

    @override
    def existing_ids(self, ids: list[str]) -> set[str]:
        return {"a", "b"}.intersection(ids)

    @override
    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        return frame().loc[lambda rows: rows.location_id.isin(ids)]


class FilePlaces(ParquetPlaces):
    schema = declaration()


def additions() -> gpd.GeoDataFrame:
    return frame().assign(
        location_id=["middle", "last"],
        count=pd.array([5, 8], dtype="Int64"),
        geometry=gpd.GeoSeries([Point(7, 8)] * 2, crs=4326),
    )


def chain_definition(
    resources: dict, *, outside: bool
) -> tuple[PluginDefinition, list]:
    observed = []

    def all_places(seeds: gpd.GeoDataFrame, target: ir.Table) -> ir.Table:
        added = seeds.set_index("location_id").loc["seed"]
        assert added.geometry == Point(7, 8)
        assert added["count"] == 5
        return target

    @location_operation(
        name="start",
        description="Map nonempty seeds to a common intermediate place",
        cache="per_location",
        inputs={"seeds": LocationDependency(provider="seeds")},
        target_provider="places",
        target_selector=all_places,
    )
    def start(seeds: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> dict:
        assert set(candidates.location_id) == {"a", "middle", "last"}
        candidates["count"] = 99
        return {key: ("middle",) for key in seeds.location_id if key != "empty"}

    def larger_at_point(source: gpd.GeoDataFrame, target: ir.Table) -> ir.Table:
        observed.append(
            set(zip(source.source_location_id, source.location_id, strict=True))
        )
        geometry = source.geometry.union_all()
        assert geometry == Point(7, 8)
        count = int(source["count"].min())
        literal = ibis.literal(geometry, type=dt.GeoSpatial(srid=4326))
        source["count"] = 99
        return target.filter(
            target.geometry.intersects(literal), target["count"] > count
        )

    @location_operation(
        name="larger",
        description="Choose larger places at the source point",
        cache="per_location",
        inputs={"source": LocationDependency(provider="places")},
        target_provider="places",
        target_selector=larger_at_point,
    )
    def larger(source: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> dict:
        counts = set(source["count"])
        assert counts in ({5}, {8})
        assert candidates.location_id.tolist() == (["last"] if counts == {5} else [])
        if outside:
            # This ID was acquired by the unfiltered selector in the same run.
            return dict.fromkeys(source.location_id, ("middle",))
        groups = {
            key: tuple(
                candidates.loc[
                    candidates.geometry.intersects(point)
                    & (candidates["count"] > count),
                    "location_id",
                ]
            )
            for key, point, count in zip(
                source.location_id, source.geometry, source["count"], strict=True
            )
        }
        candidates["count"] = 99
        return groups

    @metric(
        name="chain",
        description="Inspect immediate correspondences at each hop",
        cache="full",
        inputs={
            "seeds": LocationDependency(provider="seeds"),
            "middle": LocationDependency("start").bind(seeds=Input("seeds")),
            "last": LocationDependency("larger").bind(source=Input("middle")),
            "empty": LocationDependency("larger").bind(source=Input("last")),
        },
        outputs={"data": SCHEMA},
    )
    def consume(
        seeds: gpd.GeoDataFrame,
        middle: gpd.GeoDataFrame,
        last: gpd.GeoDataFrame,
        empty: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        assert set(seeds.location_id) == {"a", "seed", "empty"}
        assert set(middle.source_location_id) == {"a", "seed"}
        assert middle["count"].tolist() == [5, 5]
        assert last.source_location_id.tolist() == ["middle"]
        assert last.location_id.tolist() == ["last"]
        assert empty.empty
        assert empty.crs == last.crs
        return pd.DataFrame({"value": last["count"].astype("int64")})

    return PluginDefinition(
        location_operations=[start, larger],
        metrics=[consume],
        resources={"seeds": Seeds, **resources},
    ), observed


def run_chain(
    tmp_path: Path, resources: dict, config: dict, *, outside: bool = False
) -> None:
    definition, observed = chain_definition(resources, outside=outside)
    catalog = LocalRunner.from_definitions({"test": definition}).catalog
    changes = [
        Intervention(
            id="seed-plan",
            revision="1",
            catalog="seeds",
            add=additions().assign(location_id=["seed", "empty"]),
            remove=["b"],
        ),
        Intervention(
            id="place-plan",
            revision="1",
            catalog="places",
            add=additions(),
            remove=["b"],
        ),
    ]
    request = {
        "metric": "chain",
        "inputs": {"seeds": {"provider": "seeds", "ids": ["a", "seed", "empty"]}},
    }
    with SQLiteStore(tmp_path / "chain") as store:
        executor = GraphExecutor(catalog, Mock(), config, store=store)
        plan = prepare_execution(catalog, request, native_interventions=changes)
        if outside:
            with pytest.raises(GraphExecutionError, match="original candidates"):
                executor.execute(plan)
            return
        result = executor.execute(plan)
        assert result.outputs["data"].value.tolist() == [8]
        assert observed == [{("a", "middle"), ("seed", "middle")}, {("middle", "last")}]
        observed.clear()
        reordered = [
            replace(change, add=change.add.iloc[::-1])
            for change in reversed(changes)
            if change.add is not None
        ]
        again = executor.execute(
            prepare_execution(catalog, request, native_interventions=reordered)
        )
        assert again.completions == result.completions
        assert_logical_equal(again.outputs["data"], result.outputs["data"])
        assert not observed


def candidate_batches() -> list[pa.Table]:
    """Script membership, all candidates, a filtered match, then an empty match."""
    added = additions()
    effective = pd.concat([frame().iloc[:1], added], ignore_index=True)
    schema = compile_query_schema(declaration()).interchange
    return [
        pa.table({"location_id": ["b"]}),
        *(
            pa.Table.from_pandas(data.to_wkb(), schema=schema, preserve_index=False)
            for data in (effective, added.iloc[1:], added.iloc[:0])
        ),
    ]


@pytest.mark.parametrize("outside", [False, True])
def test_parquet_selector_chain(
    tmp_path: Path, source: Path, backend: Mock, *, outside: bool
) -> None:
    backend.to_pyarrow_batches.side_effect = [
        batch.to_reader() for batch in candidate_batches()
    ]
    run_chain(
        tmp_path,
        {"places": FilePlaces},
        {"places": ResourceConfig(config={"path": str(source)})},
        outside=outside,
    )
    assert backend.con.register.call_count == backend.con.unregister.call_count == 2


def test_postgis_selector_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cursor = native_postgres(monkeypatch)
    batches = [
        [tuple(row.values()) for row in batch.to_pylist()]
        for batch in candidate_batches()
    ]
    cursor.execute.return_value.fetchmany.side_effect = [
        chunk for rows in batches for chunk in ([rows, []] if rows else [[]])
    ]
    run_chain(
        tmp_path, {"places": PostGISPlaces, "database": PostgresIO}, database_config()
    )
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum(sql.startswith("CREATE TEMPORARY TABLE") for sql in statements) == 2
    assert sum(sql.startswith("DROP") for sql in statements) == 2
