"""Empty demand and explicit retention avoid unnecessary operation work."""

from pathlib import Path
from typing import TYPE_CHECKING

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.errors import LocationInputUnavailableError
from lyra.data.interventions import Intervention
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore
from lyra.sdk import (
    Dependency,
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from test_support.location_operations import CatalogPlaces, Places
from tests.cache_helpers import SCHEMA
from tests.metric_execution_helpers import schema
from tests.operation_reuse_helpers import WorkloadOptions, configured_workload
from tests.sqlite_helpers import Database, database

if TYPE_CHECKING:
    from lyra.contracts.metric_scope import CachePolicy

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("retain", [False, True])
@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("ids", [(), ("a", "b")])
def test_off_mapping_has_no_unneeded_storage(
    tmp_path: Path,
    database: Database,
    ids: tuple[str, ...],
    *,
    retain: bool,
    derived: bool,
) -> None:
    run, events, seen, mapping = configured_workload(
        tmp_path, WorkloadOptions(policy="off", derived=derived, retain_result=retain)
    )
    mapping.update(a=("t",), b=())
    for _ in range(2):
        events.clear()
        result = run(ids)
        assert bool(result.result_ref) == retain
        assert ("mapping", frozenset(ids)) in events if ids else not events
        assert set(seen[-1].location_id) == ({"t"} if ids else set())
    metric_rows = [
        r
        for r in database.rows.get("stored_results", [])
        if r["node_id"] in {"consumer", "derived"}
    ]
    assert {r["node_id"] for r in metric_rows} == (
        {"derived" if derived else "consumer"} if retain else set()
    )
    metric_keys = {r["value_key"] for r in metric_rows}
    assert not [
        r
        for r in database.rows.get("stored_partitions", [])
        if r["value_key"] in metric_keys
    ]


def test_operation_policy_round_trip(tmp_path: Path, database: Database) -> None:
    retained = set()
    complete = set()
    partitions = set()
    sequence: list[tuple[CachePolicy, bool]] = [
        ("off", True),
        ("full", True),
        ("full", False),
        ("off", True),
        ("per_location", True),
        ("full", False),
        ("per_location", False),
    ]
    for policy, executes in sequence:
        run, events, seen, mapping = configured_workload(
            tmp_path, WorkloadOptions(policy=policy)
        )
        mapping.update(a=("t",), b=())
        run()
        assert (("mapping", frozenset({"a", "b"})) in events) == executes
        assert set(
            zip(seen[-1].source_location_id, seen[-1].location_id, strict=True)
        ) == {("a", "t")}
        keys = {r["value_key"] for r in database.rows.get("stored_results", [])}
        assert retained <= keys
        if policy == "full" and not complete:
            complete = keys
            assert len(complete) == 1
        if policy == "per_location":
            partitions = keys - complete
            assert len(partitions) == 2
        retained = keys
    assert retained == complete | partitions


def test_full_reuses_mapping_retained_for_downstream_cache(
    tmp_path: Path,
    database: Database,
) -> None:
    run, _, _, mapping = configured_workload(
        tmp_path, WorkloadOptions(policy="off", metric_cache="full")
    )
    mapping.update(a=("t",), b=())
    run()
    keys = {
        r["value_key"]
        for r in database.rows["stored_results"]
        if r["node_id"] == "mapping"
    }
    assert len(keys) == 1
    run, events, _, mapping = configured_workload(
        tmp_path, WorkloadOptions(policy="full")
    )
    mapping.update(a=("t",), b=())
    run()
    assert not any(
        isinstance(event, tuple) and event[0] == "mapping" for event in events
    )
    assert keys <= {r["value_key"] for r in database.rows["stored_results"]}


@pytest.mark.parametrize("distinct_binding", [False, True])
def test_shared_mapping_retention_upgrades_without_execution(
    tmp_path: Path, database: Database, *, distinct_binding: bool
) -> None:
    events = []

    @location_operation(
        name="forward",
        description="Shared mapping",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def forward(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        events.append(set(source.location_id))
        return {key: (key,) for key in source.location_id}

    def leaf(places: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(places)]})

    cached = metric(
        name="cached",
        description="Cached consumer",
        cache="full",
        inputs={"places": LocationDependency(provider=Places.id)},
        outputs={"data": SCHEMA},
    )(leaf)
    binding = LocationDependency(operation="forward").bind(source=Input("source"))
    inputs = {
        "source": LocationDependency(provider=Places.id),
        "places": binding,
        "data": Dependency("data", producer="cached").bind(
            places=binding if distinct_binding else Input("places")
        ),
    }

    # A derived input on a dependency is visited after root location inputs.
    # In the first case it has a distinct binding; in the second it shares one.
    @metric(
        name="root",
        description="Uncached root",
        inputs=inputs,
        outputs={"data": SCHEMA},
    )
    def root(
        source: gpd.GeoDataFrame, places: gpd.GeoDataFrame, data: pd.DataFrame
    ) -> pd.DataFrame:
        assert set(source.location_id) == set(places.location_id)
        return data

    runner = LocalRunner.from_definitions(
        {
            "p": PluginDefinition(
                metrics=[root, cached],
                location_operations=[forward],
                resources={"places": Places},
            )
        },
        result_store_path=tmp_path / "values.sqlite",
    )
    for _ in range(2):
        events.clear()
        result = runner.run(
            "root", inputs={"source": {"provider": Places.id, "ids": ["a", "b"]}}
        )
        assert result.tables["data"].value.tolist() == [2]
        # A shared logical operation executes once, even when retention is upgraded.
        assert events == [{"a", "b"}]
    assert (
        len([r for r in database.rows["stored_results"] if r["node_id"] == "forward"])
        == 1
    )


@pytest.mark.parametrize(("ids", "policy"), [([], "per_location"), (["a"], "off")])
def test_derived_empty_primary_skips_other_membership(
    tmp_path: Path, ids: list[str], policy: "CachePolicy"
) -> None:
    work = []

    @location_operation(
        name="empty",
        description="Empty primary mapping",
        cache=policy,
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def empty(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        work.append(set(source.location_id))
        return {}

    @location_operation(
        name="unused",
        description="Unused shared mapping",
        inputs={"source": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
    )
    def unused(source: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        del source
        pytest.fail("Shared operation is unnecessary for an empty primary")

    @metric(
        name="root",
        description="Empty derived primary",
        cache="per_location",
        inputs={
            "source": LocationDependency(provider=Places.id),
            "shared": LocationDependency(operation="unused").bind(
                source=Input("source")
            ),
            "primary": LocationDependency(operation="empty", primary=True).bind(
                source=Input("source")
            ),
        },
        outputs={"data": schema()},
    )
    def root(
        source: gpd.GeoDataFrame, shared: gpd.GeoDataFrame, primary: gpd.GeoDataFrame
    ) -> pd.DataFrame:
        del source, shared, primary
        pytest.fail("Empty primary has no metric work")

    runner = LocalRunner.from_definitions(
        {
            "p": PluginDefinition(
                metrics=[root],
                location_operations=[empty, unused],
                resources={"places": CatalogPlaces},
            )
        },
        result_store_path=tmp_path / "values.sqlite",
    )
    result = runner.run(
        "root",
        inputs={"source": {"provider": Places.id, "ids": ids}},
        interventions=[
            Intervention(
                catalog=Places.id, id="changes", revision="1", remove=["outside"]
            )
        ],
    )
    assert result.tables["data"].empty
    assert work == ([{"a"}] if ids else [])
    result.metadata.store_path.touch()
    with ResultStore.open(result.metadata.store_path) as reader:
        ref = result.metadata.result_ref
        descriptions = reader.describe_result(ref).location_inputs
        assert descriptions["primary"].availability == "available"
        assert descriptions["shared"].availability == "not_materialized"
        scope = result.report.intervention_validations[0].scope
        assert (
            descriptions["primary"].source_scope
            == descriptions["primary"].provider_scope
            == scope
        )
        export = tmp_path / "empty.parquet"
        reader.export_location_input_geoparquet(ref, "primary", export)
        exported = gpd.read_parquet(export)
        assert exported.empty
        assert exported.crs == "EPSG:4326"
        assert exported.attrs["lyra"]["description"] == descriptions[
            "primary"
        ].model_dump(mode="json")
        with pytest.raises(LocationInputUnavailableError):
            reader.export_location_input_geoparquet(
                ref, "shared", tmp_path / "unavailable.parquet"
            )
