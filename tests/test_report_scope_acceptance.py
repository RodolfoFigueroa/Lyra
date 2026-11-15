"""Concrete output owners and direct origins survive reuse and empty demand."""

from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.origins import Origin
from lyra.runtime.local import LocalRunner
from lyra.sdk import LocationDependency, PluginDefinition, metric
from test_support.location_operations import Places
from tests.cache_helpers import SCHEMA
from tests.metric_execution_helpers import schema
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def test_concrete_metrics_and_shared_origin_revisions(tmp_path: Path) -> None:
    path = tmp_path / "results.sqlite"
    reports = []
    calls = []
    for code_revision, origin_revision in [("1", "1"), ("2", "1"), ("2", "2")]:
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
                reports.append(report)
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
    assert len({r.execution_id for r in reports}) == len(reports)
    # Later declarations do not rewrite earlier reports' captured descriptions.
    assert next(
        n for n in reports[0].nodes if n.use_id == reports[0].root_use_id
    ).origins == [Origin(id="census", revision="1", description="Census records")]


def measurement(provider: type[Places], origin: Origin, calls: list) -> Callable:
    @metric(
        name=f"{provider.id}_measure",
        description="Concrete measurement",
        code_revision="measure-1",
        cache="per_location",
        origins=[origin, origin],
        inputs={"places": LocationDependency(provider=provider.id, primary=True)},
        outputs={"area": schema(), "count": schema()},
    )
    def measure(places: gpd.GeoDataFrame) -> dict[str, pd.DataFrame]:
        calls.append(tuple(places.location_id))
        result = pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )
        places.attrs["provider_scope"] = {"provider_id": "forged"}
        places.loc[:, "location_id"] = "forged"
        return {"area": result, "count": result.copy()}

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
        outputs={"total": SCHEMA},
    )
    def summary(blocks: gpd.GeoDataFrame, tracts: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame({"value": [len(blocks) + len(tracts)]})

    return summary
