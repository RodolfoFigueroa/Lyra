"""Concrete output owners and direct origins survive reuse and empty demand."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pandera
import pytest
from pandera.engines.geopandas_engine import Geometry
from shapely.geometry import Point

from lyra.contracts.origins import Origin
from lyra.runtime import provider_acquisition
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore, RunResult
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import LocationDependency, PluginDefinition, metric
from test_support.location_operations import Places
from test_support.location_schema import column
from tests.cache_helpers import SCHEMA
from tests.geoparquet_integration_helpers import assert_exports, assert_remote_outputs
from tests.metric_execution_helpers import schema
from tests.retrieval_client_helpers import NativeAPI, native_api
from tests.sqlite_helpers import database

__all__ = ["database", "native_api"]
pytestmark = pytest.mark.usefixtures("database")


def test_concrete_metrics_and_shared_origin_revisions(
    tmp_path: Path, native_api: NativeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "results.sqlite"
    retained, calls = [], []
    for code_revision, origin_revision in [
        ("1", "1"),
        ("2", "1"),
        ("2", "2"),
        ("3", None),
    ]:
        origin = Origin(
            id="census", revision=origin_revision, description="Census records"
        )

        class Blocks(Places):
            id = "blocks"
            origins = (origin,)

        class Tracts(Places):
            id = "tracts"
            origins = (origin,)

        Blocks.code_revision = Tracts.code_revision = code_revision
        metrics = [
            measurement(provider, origin, calls) for provider in (Blocks, Tracts)
        ]
        metrics.append(shared_origin_summary(origin))
        runner = LocalRunner.from_definitions(
            {
                "test": PluginDefinition(
                    metrics=metrics, resources={"blocks": Blocks, "tracts": Tracts}
                )
            },
            result_store_path=path,
        )
        for provider in (Blocks, Tracts):
            for ids, expected in [
                ([], "skipped"),
                (["a"], "executed"),
                (["a", "b"], "mixed"),
                (["a", "b"], "reused"),
            ]:
                calls.clear()
                result = runner.run(
                    f"{provider.id}_measure",
                    inputs={"places": {"provider": provider.id, "ids": ids}},
                )
                report = result.report
                root = next(n for n in report.nodes if n.use_id == report.root_use_id)
                assert root.outcome == expected
                assert root.origins == [origin]
                assert root.code_revision == "measure-1"
                assert root.primary_input == "places"
                assert report.build_revision is None
                assert report.request.inputs["places"].ids == ids
                scope = root.inputs["places"].provider_scope
                assert scope is not None
                assert (scope.provider_id, scope.code_revision, scope.origins) == (
                    provider.id,
                    code_revision,
                    frozenset({origin}),
                )
                for name in ("area", "count"):
                    assert root.outputs[name].owner_scope == scope
                    assert root.outputs[name].scope == "location"
                    assert result.metadata.outputs[name] == root.outputs[name]
                    assert result.tables[name].location_id.tolist() == ids
                provider_node = next(n for n in report.nodes if n.kind == "provider")
                assert provider_node.origins == [origin]
                assert provider_node.code_revision == code_revision
                if expected in {"skipped", "reused"}:
                    assert calls == []
                    assert provider_node.outcome == "skipped"
                else:
                    assert calls == [("a",) if expected == "executed" else ("b",)]
                retained.append(result)
        for expected in ("executed", "reused"):
            result = runner.run(
                "summary",
                inputs={
                    "blocks": {"provider": "blocks", "ids": ["a"]},
                    "tracts": {"provider": "tracts", "ids": ["a"]},
                },
            )
            root = next(
                n for n in result.report.nodes if n.use_id == result.report.root_use_id
            )
            assert root.outcome == expected
            assert root.origins == [origin]
            scopes = [binding.provider_scope for binding in root.inputs.values()]
            assert all(
                scope is not None and scope.origins == frozenset({origin})
                for scope in scopes
            )
            assert {scope.provider_id for scope in scopes if scope is not None} == {
                "blocks",
                "tracts",
            }
            assert len({binding.source_use_id for binding in root.inputs.values()}) == 2
            assert root.outputs["total"].owner_scope is None
            assert result.tables["total"].value.tolist() == [2]
            assert_summary_exports(path, result)
    reports = [result.report for result in retained]
    assert_no_execution_exports(path, retained, monkeypatch)
    # Later declarations do not rewrite earlier reports' captured descriptions.
    assert next(
        n for n in reports[0].nodes if n.use_id == reports[0].root_use_id
    ).origins == [Origin(id="census", revision="1", description="Census records")]
    assert_remote_outputs(path, retained, native_api, monkeypatch)


def assert_retained_outputs(path: Path, retained: list) -> None:
    assert len({result.report.job_id for result in retained}) == len(retained)
    with ResultStore(SQLiteStore(path, read_only=True)) as reader:
        for result in retained:
            metadata, tables = result.metadata, result.tables
            assert reader.describe(metadata.job_id).outputs == metadata.outputs
            assert_exports(reader, metadata.job_id, path.parent)
            for name, frame in tables.items():
                pd.testing.assert_frame_equal(
                    reader.read_table(metadata.job_id, name),
                    frame.astype(
                        {"location_id": "string", "row_key": "string", "value": "Int64"}
                    ),
                )


def measurement(provider: type[Places], origin: Origin, calls: list) -> Callable:
    @metric(
        name=f"{provider.id}_measure",
        description="Concrete measurement",
        code_revision="measure-1",
        cache="per_location",
        origins=[origin, origin],
        inputs={"places": LocationDependency(provider=provider.id, primary=True)},
        outputs={"area": schema(), "count": schema(), "points": schema(spatial=True)},
    )
    def measure(places: gpd.GeoDataFrame) -> dict[str, pd.DataFrame]:
        calls.append(tuple(places.location_id))
        result = pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )
        points = gpd.GeoDataFrame(
            result.copy(), geometry=places.geometry.copy(), crs=places.crs
        )
        places.attrs["provider_scope"] = {"provider_id": "forged"}
        places.loc[:, "location_id"] = "forged"
        return {"area": result, "count": result.copy(), "points": points}

    return measure


def shared_origin_summary(origin: Origin) -> Callable:
    @metric(
        name="summary",
        description="Shared external origin",
        cache="full",
        origins=[origin],
        inputs={
            "blocks": LocationDependency(provider="blocks"),
            "tracts": LocationDependency(provider="tracts"),
        },
        outputs={
            "total": SCHEMA,
            "map": pandera.DataFrameSchema({"geometry": column(Geometry)}, strict=True),
        },
    )
    def summary(
        blocks: gpd.GeoDataFrame, tracts: gpd.GeoDataFrame
    ) -> dict[str, pd.DataFrame]:
        return {
            "total": pd.DataFrame({"value": [len(blocks) + len(tracts)]}),
            "map": gpd.GeoDataFrame(geometry=[Point(1, 2)], crs=4326),
        }

    return summary


def assert_summary_exports(path: Path, result: RunResult) -> None:
    assert result.metadata.outputs["map"].owner_scope is None
    with ResultStore(SQLiteStore(path, read_only=True)) as reader:
        assert_exports(reader, result.metadata.job_id, path.parent)


def assert_no_execution_exports(
    path: Path, retained: list[RunResult], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("acquire_provider_input", "acquire_provider_candidates"):
        monkeypatch.setattr(
            provider_acquisition,
            name,
            Mock(side_effect=AssertionError("Export must only read retained data")),
        )
    assert_retained_outputs(path, retained)
