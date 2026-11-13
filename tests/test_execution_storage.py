"""Current native values retain execution contracts across storage boundaries."""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from shapely.geometry import Point

from lyra.contracts.metric_scope import CachePolicy
from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.local import LocalRunner
from lyra.sdk import (
    Input,
    LocationDependency,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.storage_schema import empty_frame
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import RESULT, provider_execution_definitions


@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_owned_tables_and_captured_completion(
    tmp_path: Path, *, spatial: bool, empty: bool
) -> None:
    declaration = schema(spatial=spatial)
    calls = []

    @metric(
        name="findings",
        description="Findings",
        code_revision="1",
        cache="full",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"a": declaration, "b": declaration},
    )
    def findings(places: gpd.GeoDataFrame) -> dict:
        assert set(places.location_id) == {"a", "b"}
        calls.append(True)
        frame = (
            empty_frame(declaration)
            if empty
            else pd.DataFrame(
                {
                    "location_id": ["a"],
                    "row_key": ["one"],
                    "value": pd.array([None], dtype="Int64"),
                }
            )
        )
        if spatial and not empty:
            frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0)], crs=4326)
        return {"a": frame, "b": frame}

    definitions = provider_execution_definitions([])
    definitions["findings"] = PluginDefinition(metrics=[findings])
    runner = LocalRunner.from_definitions(definitions, artifact_dir=tmp_path)
    inputs = {"places": {"provider": "target", "ids": ["a", "b"]}}
    result = runner.run("findings", inputs=inputs)
    again = runner.run("findings", inputs=inputs)
    assert calls == [True]
    store = ArtifactStore(tmp_path)
    bundle = next(store.directory.iterdir()).name
    assert store.descriptions(bundle) == result.metadata.outputs
    manifest = json.loads((store.directory / bundle / "manifest.json").read_text())
    assert manifest["outputs"]["a"]["partitions"] == ["a", "b"]
    result.metadata.outputs["a"].table_schema.columns.clear()
    result.tables["a"]["value"] = 99
    restored = store.read(bundle)
    pd.testing.assert_frame_equal(restored["a"], again.tables["a"])
    restored["a"]["value"] = 13
    pd.testing.assert_frame_equal(store.read(bundle)["a"], again.tables["a"])
    node = runner.catalog.graph.nodes["findings"].node
    assert node.kind == "metric"
    node.outputs["a"].columns[0].description = "Changed catalog"
    assert store.descriptions(bundle) == again.metadata.outputs


@pytest.mark.parametrize("matches", [False, True])
def test_operation_mapping_reuse_resolves_targets(
    tmp_path: Path, *, matches: bool
) -> None:
    events = []
    definitions = provider_execution_definitions(events)
    del definitions["calculations"]

    @location_operation(
        name="match",
        description="Match",
        code_revision="1",
        cache="full",
        inputs={"places": LocationDependency(provider="source")},
        target_provider="target",
    )
    def match(places: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        assert set(places.location_id) == {"a", "b", "c"}
        events.append("match")
        return {"a": ("shared",), "b": ("shared",)} if matches else {}

    @metric(
        name="count",
        description="Count",
        code_revision="1",
        outputs={"count": RESULT},
        inputs={
            "source": LocationDependency(provider="source"),
            "places": LocationDependency(operation="match").bind(
                places=Input("source")
            ),
        },
    )
    def count(source: gpd.GeoDataFrame, places: gpd.GeoDataFrame) -> pd.DataFrame:
        assert set(source.location_id) == {"a", "b", "c"}
        assert places.location_id.tolist() == (["shared", "shared"] if matches else [])
        return pd.DataFrame({"total": [len(places)]})

    definitions["matches"] = PluginDefinition(
        metrics=[count], location_operations=[match]
    )
    runner = LocalRunner.from_definitions(definitions, artifact_dir=tmp_path)
    for _ in range(2):
        runner.run(
            "count", inputs={"source": {"provider": "source", "ids": ["a", "b", "c"]}}
        )
    assert events.count("match") == 1
    assert events.count("resolve:target") == (2 if matches else 0)
    assert events.count("setup:target") == (2 if matches else 0)
    store = ArtifactStore(tmp_path)
    saved = store.read_operation(next(store.directory.iterdir()).name)
    assert saved.groups == {
        "a": ("shared",) if matches else (),
        "b": ("shared",) if matches else (),
        "c": (),
    }


@pytest.mark.parametrize("cache_policy", ["off", "full"])
def test_cache_policy_and_trusted_write_validation(
    tmp_path: Path, cache_policy: CachePolicy
) -> None:
    checks = []
    calls = []

    def check_values(values: pd.Series) -> bool:
        checks.append(True)
        return bool((values >= 0).all())

    declaration = RESULT.update_column(
        "total", checks=pa.Check(check_values, name="nonnegative")
    )

    @metric(
        name="total",
        description="Total",
        code_revision="1",
        outputs={"total": declaration},
        cache=cache_policy,
    )
    def total() -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame({"total": [1]})

    runner = LocalRunner.from_definitions(
        {"p": PluginDefinition(metrics=[total])}, artifact_dir=tmp_path
    )
    runner.run("total", inputs={})
    assert checks == [True]  # A trusted write must not rerun producer checks.
    runner.run("total", inputs={})
    assert len(calls) == (1 if cache_policy == "full" else 2)
    assert len(checks) == 2  # Restored values have an independent validation boundary.


def test_corrupt_table_contract_fails_independent_read(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    reference = store.write({"total": pd.DataFrame({"total": [1]})}, {"total": RESULT})
    manifest_path = store.directory / reference / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["outputs"]["total"]["description"]["table_schema"]["columns"][0][
        "dtype"
    ] = "str"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="output"):
        store.read(reference)
