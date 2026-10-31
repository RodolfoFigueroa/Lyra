"""Geometry generality across public local execution and the queue boundary."""

from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    LocalRunner,
    LocationOutputSchema,
    Locations,
    PluginDefinition,
    metric,
)
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.graph import build_graph
from lyra.sdk.job_inputs import prepare_job_input
from lyra.sdk.models.job import JobEnvelope
from typing_extensions import override

from tests.location_helpers import (
    fixture_locations,
    output_schema,
    producer_result,
    scoped_payload,
)
from tests.test_location_models import GEOMETRIES, _collection
from tests.test_location_resolution import Provider, selection


@pytest.mark.parametrize("kinds", [[kind] for kind in GEOMETRIES] + [list(GEOMETRIES)])
def test_geometry_survives_local_execution_and_queue(
    tmp_path: Path, kinds: list[str]
) -> None:
    payload = _collection(kinds)
    for index, feature in enumerate(payload["features"]):
        feature["properties"] = {"label": feature["geometry"]["type"], "reading": index}
    expected = fixture_locations(
        scoped_payload(payload) | {"collection": "test:roads", "unit_type": "road"},
        schema=output_schema(label=str, reading=int),
    )
    ids = list(expected.ids)
    events: list[object] = []

    class FakeProvider(Provider):
        outputs: ClassVar[dict[tuple[str, str], LocationOutputSchema]] = {
            scope: output_schema(label=str, reading=int) for scope in Provider.outputs
        }

        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> gpd.GeoDataFrame:
            events.append((collection, unit_type, ids.copy()))
            return producer_result(
                fixture_locations(
                    expected.to_dict()
                    | {"features": list(reversed(expected.to_features()))},
                    schema=output_schema(label=str, reading=int),
                ),
                self.outputs[collection, unit_type],
            )

        @override
        def close(self) -> None:
            events.append("close")

    @metric(
        name="inspect",
        description="Inspect geometry",
        outputs={
            "result": pa.DataFrameSchema(
                {
                    "id": pa.Column(
                        str, description="Selected ID", metadata={"unit": None}
                    )
                }
            )
        },
    )
    def inspect(location: Locations) -> pd.DataFrame:
        assert events[-1] == ("test:roads", "road", ids)
        assert isinstance(location, Locations)
        assert location == expected
        return pd.DataFrame({"id": list(location.ids)})

    plugin = PluginDefinition(
        metrics=[inspect],
        resources={"locations": FakeProvider},
        location_provider="locations",
    )
    caller = {
        "location": selection(
            *ids, ids[0], collection="test:roads", unit_type="road"
        ).model_dump(mode="json")
    }
    runner = LocalRunner.from_definitions(
        {"plugin": plugin},
        resource_overrides={"locations": FakeProvider},
        cache_dir=tmp_path,
    )
    assert runner.run("inspect", caller)["result"]["id"].tolist() == ids
    assert events == ["setup", ("test:roads", "road", ids), "close"]

    envelope = JobEnvelope(job_id="geometry", metric="inspect", input=caller)
    restored = JobEnvelope.model_validate_json(envelope.model_dump_json())
    queued = prepare_job_input(plugin, "inspect", restored.input, caller)

    executor = GraphExecutor(
        build_graph({"plugin": plugin.node_manifests}),
        {"plugin": plugin},
        Mock(),
        location_provider=runner.location_provider,
    )
    assert executor.execute(queued)["result"]["id"].tolist() == ids
    assert events == ["setup", ("test:roads", "road", ids), "close"] * 2
