"""Jobs retain their declared output schemas across cache hits and revisions."""

from pathlib import Path

import geopandas as gpd
import pandera.pandas as pa
import pytest
from pyarrow import ipc

from lyra.contracts.errors import MetricResultError
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore, RunResult
from lyra.sdk import LocationDependency, PluginDefinition, metric
from tests.frame_assertions import assert_logical_equal
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def runner(
    path: Path,
    calls: list[set[str]],
    wording: str,
    revision: str = "1",
    minimum: int = 0,
) -> LocalRunner:
    declaration = schema(spatial=True)
    declaration.columns["value"].description = wording
    declaration.columns["value"].checks = [pa.Check.ge(minimum, description=wording)]

    @metric(
        name="measurement",
        description="One measurement per location",
        code_revision=revision,
        cache="per_location",
        inputs={"places": LocationDependency(provider="source", primary=True)},
        outputs={"rows": declaration},
    )
    def measurement(places: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        calls.append(set(places.location_id))
        return gpd.GeoDataFrame(
            {
                "location_id": places.location_id,
                "row_key": "r",
                "value": places["location_id"].ne("zero").astype("Int64"),
            },
            geometry=places.geometry,
            crs=places.crs,
        )

    definitions = provider_execution_definitions([])
    definitions["sample"] = PluginDefinition(metrics=[measurement])
    return LocalRunner.from_definitions(definitions, result_store_path=path)


def run(local: LocalRunner, *ids: str) -> RunResult:
    return local.run(
        "measurement", inputs={"places": {"provider": "source", "ids": list(ids)}}
    )


def test_editorial_change_preserves_rows_and_job_metadata(tmp_path: Path) -> None:
    path = tmp_path / "values"
    calls = []
    original = run(runner(path, calls, "Original"), "a")
    edited = runner(path, calls, "Edited")
    cached = run(edited, "a")
    partial = run(edited, "a", "b")
    empty = run(runner(path, calls, "Empty selection"))
    assert calls == [{"a"}, {"b"}]
    path.touch()
    with ResultStore.open(path) as store:
        for result, wording, ids in (
            (original, "Original", ["a"]),
            (cached, "Edited", ["a"]),
            (partial, "Edited", ["a", "b"]),
            (empty, "Empty selection", []),
        ):
            job = result.metadata.job_id
            output = result.metadata.outputs["rows"]
            value = next(c for c in output.table_schema.columns if c.name == "value")
            assert value.description == value.checks[0].description == wording
            assert store.describe(job).outputs["rows"] == output
            assert store.read_report(job) == result.report
            root = next(n for n in result.report.nodes if n.node_id == "measurement")
            assert root.outputs["rows"] == output
            assert sorted(store.read_table(job, "rows").location_id) == ids
            with ipc.open_stream(b"".join(store.stream_table(job, "rows"))) as stream:
                table = stream.read_all()
            assert sorted(table["location_id"].to_pylist()) == ids
            assert (
                table.schema.field("value").metadata[b"description"] == wording.encode()
            )
            destination = tmp_path / f"{job}.parquet"
            store.export_geoparquet(job, "rows", destination)
            exported = gpd.read_parquet(destination)
            assert sorted(exported.location_id) == ids
            assert exported.attrs["lyra"]["description"] == output.model_dump(
                mode="json"
            )


def test_revised_check_recomputes_and_keeps_prior_jobs(tmp_path: Path) -> None:
    path = tmp_path / "values"
    calls = []
    original = run(runner(path, calls, "Value"), "a", "zero")
    revised = runner(path, calls, "Value", revision="2", minimum=1)
    with pytest.raises(GraphExecutionError) as caught:
        run(revised, "a", "zero")
    assert isinstance(caught.value.__cause__, MetricResultError)
    fresh = run(revised, "a")
    warm = run(revised, "a")
    assert calls == [{"a", "zero"}, {"a", "zero"}, {"a"}]
    path.touch()
    with ResultStore.open(path) as store:
        for result in (original, fresh, warm):
            restored = store.read_result(result.metadata.job_id)
            assert restored.report == result.report
            assert restored.metadata.outputs == result.metadata.outputs
            assert_logical_equal(restored.tables["rows"], result.tables["rows"])
