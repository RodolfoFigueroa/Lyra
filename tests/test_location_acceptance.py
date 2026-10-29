"""Geometry generality across public local execution and the queue boundary."""

from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import LocalRunner, LocationInput, PluginDefinition, metric
from lyra.sdk.arguments import serialize_resolved_input
from lyra.sdk.job_inputs import prepare_resolved_job_input
from lyra.sdk.models.job import JobEnvelope
from lyra.sdk.models.location import ResolvedLocation
from typing_extensions import override

from tests.test_location_models import GEOMETRIES, _collection
from tests.test_location_resolution import Provider, selection


@pytest.mark.parametrize("kinds", [[kind] for kind in GEOMETRIES] + [list(GEOMETRIES)])
def test_geometry_survives_local_execution_and_queue(
    tmp_path: Path, kinds: list[str]
) -> None:
    expected = LocationInput.model_validate(_collection(kinds))
    ids = [feature.id for feature in expected.features]
    events: list[object] = []

    class FakeProvider(Provider):
        @override
        def setup(self) -> None:
            events.append("setup")

        @override
        def resolve(
            self, collection: str, unit_type: str, ids: list[str]
        ) -> LocationInput:
            events.append((collection, unit_type, ids.copy()))
            return expected.model_copy(
                update={"features": list(reversed(expected.features))}
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
    def inspect(location: LocationInput) -> pd.DataFrame:
        assert events[-1] == "close"
        assert isinstance(location, LocationInput)
        assert location == expected
        return pd.DataFrame({"id": [feature.id for feature in location.features]})

    plugin = PluginDefinition(
        nodes=[inspect],
        resources={"locations": Provider},
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

    invocation = plugin.prepare_resolved_invocation(
        "inspect",
        {
            "location": ResolvedLocation(
                collection="test:roads", unit_type="road", location=expected
            )
        },
    )
    envelope = JobEnvelope(
        job_id="geometry", metric="inspect", input=serialize_resolved_input(invocation)
    )
    restored = JobEnvelope.model_validate_json(envelope.model_dump_json())
    queued = prepare_resolved_job_input(plugin, "inspect", restored.input, caller)
    assert queued.identity == invocation.identity
    assert queued.locations == invocation.locations
    assert queued.arguments()["location"] == expected
    assert events == ["setup", ("test:roads", "road", ids), "close"]
