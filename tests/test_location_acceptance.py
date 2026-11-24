"""Geometry generality across public local execution and the queue boundary."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from typing_extensions import override

from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import LocationDependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import (
    fixture_locations,
    output_schema,
    scoped_payload,
)
from test_support.location_resolution import Provider, selection
from test_support.native_providers import native_schema
from tests.geometry_fixtures import GEOMETRIES, station_collection
from tests.queue_helpers import accepted_envelope


@pytest.mark.parametrize("kinds", [[kind] for kind in GEOMETRIES] + [list(GEOMETRIES)])
def test_geometry_survives_local_execution_and_queue(
    tmp_path: Path, kinds: list[str]
) -> None:
    payload = station_collection(kinds)
    for index, feature in enumerate(payload["features"]):
        feature["properties"] = {"label": feature["geometry"]["type"], "reading": index}
    expected = fixture_locations(
        scoped_payload(payload) | {"collection": "test:roads", "unit_type": "road"},
        schema=output_schema(label=str, reading=int),
    )
    ids = expected["id"].tolist()
    events: list[object] = []

    expected_frame = expected.copy(deep=True).rename(columns={"id": "location_id"})

    class FakeProvider(Provider):
        schema = native_schema(output_schema(label=str, reading=int), "EPSG:27700")

        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
            events.append((self.id, ids.copy()))
            return expected_frame.iloc[::-1].copy()

        @override
        def close(self) -> None:
            events.append("close")

    @metric(
        code_revision="1",
        inputs={"location": LocationDependency(provider=Provider.id)},
        name="inspect",
        description="Inspect geometry",
        outputs={
            "result": pa.DataFrameSchema(
                {
                    "id": pa.Column(
                        str, description="Selected ID", metadata={"unit": None}
                    )
                },
                strict=True,
            )
        },
    )
    def inspect(location: gpd.GeoDataFrame) -> pd.DataFrame:
        assert events[-1] == (Provider.id, sorted(ids))
        pd.testing.assert_frame_equal(location, expected_frame.reset_index(drop=True))
        return pd.DataFrame({"id": location.location_id.tolist()})

    plugin = PluginDefinition(
        metrics=[inspect],
        resources={"locations": FakeProvider},
    )
    caller = {"location": selection(*ids, ids[0]).model_dump(mode="json")}
    runner = LocalRunner.from_definitions(
        {"plugin": plugin},
        resource_overrides={"locations": FakeProvider},
        result_store_path=tmp_path / "results.sqlite3",
    )
    assert runner.run("inspect", inputs=caller).tables["result"]["id"].tolist() == ids
    assert events == ["setup", (Provider.id, sorted(ids)), "close"]

    envelope = accepted_envelope(
        job_id="geometry", metric="inspect", inputs=caller, catalog=runner.catalog
    )
    restored = JobEnvelope.model_validate_json(envelope.model_dump_json())
    accepted = restored.accepted.plan
    queued = prepare_execution(
        runner.catalog,
        accepted.request.submission_payload(),
        normalized_uses=accepted.parameter_uses,
    )

    executor = GraphExecutor(
        runner.catalog,
        Mock(),
    )
    assert executor.execute(queued).outputs["result"]["id"].tolist() == ids
    assert events == ["setup", (Provider.id, sorted(ids)), "close"] * 2
