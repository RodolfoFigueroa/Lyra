"""Declared provider reuse through isolated execution and acquisition boundaries."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from typing_extensions import override

from lyra.contracts.errors import LocationProviderContractError, LocationSelectionError
from lyra.contracts.locations import LocationSelection
from lyra.contracts.references import new_job_id
from lyra.runtime.local import LocalRunner
from lyra.runtime.locations import LocationResolver
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import LocationDependency, LocationProvider, PluginDefinition, metric
from lyra.server import worker
from tests.acquisition_helpers import acquisition_fixture
from tests.provider_execution_helpers import RESULT
from tests.provider_schema_helpers import declaration, frame
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.sqlite_helpers import database

__all__ = ["database"]
from tests.worker_graph_integration_helpers import install_definitions, native_job


@pytest.mark.usefixtures("worker_state")
@pytest.mark.parametrize("entry", ["local", "development", "worker"])
@pytest.mark.parametrize("cache", [False, True])
def test_declared_reuse_across_executions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, *, cache: bool
) -> None:
    events: list[str] = []
    acquired: set[str] = set()
    received: list[set[str]] = []

    class Places(LocationProvider):
        id = "places"
        schema = declaration()

        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def close(self) -> None:
            events.append("close")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            acquired.update(ids)
            rows = frame().iloc[[0] * len(ids)].reset_index(drop=True)
            rows["location_id"] = ids
            return rows

    Places.cache = cache

    @metric(
        name="root",
        description="Inspect every supplied provider record",
        inputs={"places": LocationDependency(provider="places")},
        outputs={"result": RESULT},
    )
    def root(places: gpd.GeoDataFrame) -> pd.DataFrame:
        received.append(set(places.location_id))
        return pd.DataFrame({"total": [len(places)]})

    definitions = {
        "reuse": PluginDefinition(metrics=[root], resources={"places": Places})
    }
    runner = LocalRunner.from_definitions(definitions)
    if entry == "worker":
        install_definitions(tmp_path, monkeypatch, definitions)
        native = native_job(monkeypatch)

    for ids, missing in [
        (["a", "b"], {"a", "b"}),
        (["b", "c"], {"c"} if cache else {"b", "c"}),
        (["a", "b", "c"], set() if cache else {"a", "b", "c"}),
        ([], set()),
    ]:
        acquired.clear()
        events.clear()
        inputs = {"places": {"provider": "places", "ids": ids}}
        if entry == "worker":
            native.id = new_job_id()
            envelope = accepted_envelope(job_id=native.id, metric="root", inputs=inputs)
            result = run_and_read(
                worker.run_metric_task, envelope.model_dump(mode="json")
            )
            assert result["outputs"] == {"result": [{"total": len(ids)}]}
        else:
            run = runner.run if entry == "local" else runner.run_development
            assert run("root", inputs=inputs).tables["result"].total.tolist() == [
                len(ids)
            ]
        assert received[-1] == set(ids)
        assert acquired == missing
        assert events == (["setup", "close"] if missing else [])


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("cache", [False, True])
def test_reduced_demand_preserves_binding_and_local_records(
    tmp_path: Path, *, cache: bool
) -> None:
    handler = Mock(
        side_effect=lambda _self, ids: frame()[frame().location_id.isin(ids)]
    )
    selection, resources = acquisition_fixture(handler)
    provider = replace(selection.provider, cache=cache)
    bound = {"places": LocationSelection(provider="tracts", ids=["a", "b"])}
    resolver = LocationResolver(
        resources,
        providers={"tracts": provider},
        provider_records=ProviderRecords(SQLiteStore(tmp_path / "records.sqlite")),
    )
    with resources.activate():
        empty = resolver.resolve(bound, needed_ids={"places": []})["places"]
        assert empty.frame.empty
        assert empty.contract == provider.contract.reference
        handler.assert_not_called()
        result = resolver.resolve(bound, needed_ids={"places": ["b"]})["places"]
        assert set(result.ids) == {"b"}
        assert set(handler.call_args.args[-1]) == {"b"}
        handler.reset_mock()
        resolver.resolve(bound, needed_ids={"places": ["b"]})
        handler.assert_not_called()
        with pytest.raises(LocationSelectionError, match="bound selection"):
            resolver.resolve(bound, needed_ids={"places": ["unknown"]})
        handler.assert_not_called()
    assert bound["places"].ids == ["a", "b"]


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("invalid", ["missing", "extra", "duplicate"])
def test_partial_hit_validates_exact_missing_membership(
    tmp_path: Path, invalid: str
) -> None:
    handler = Mock(return_value=frame())
    selection, resources = acquisition_fixture(handler)
    provider = replace(selection.provider, cache=True)
    records = ProviderRecords(SQLiteStore(tmp_path / "records.sqlite"))
    records.publish(provider, provider.schema.validate(frame().iloc[:1]))
    output = frame()
    if invalid == "missing":
        output = output.iloc[:0]
    elif invalid == "duplicate":
        output = output.iloc[[1, 1]]
    handler.return_value = output
    resolver = LocationResolver(
        resources, providers={"tracts": provider}, provider_records=records
    )
    with resources.activate(), pytest.raises(LocationProviderContractError):
        resolver.resolve(
            {"places": LocationSelection(provider="tracts", ids=["a", "b"])}
        )
    assert set(handler.call_args.args[-1]) == {"b"}
    assert records.lookup(provider, ["b"]).empty
