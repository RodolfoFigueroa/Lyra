"""Local results retain their execution's tables and descriptive metadata."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import Field
from shapely.geometry import Point

from lyra.contracts.units import Unit
from lyra.runtime import RunResult
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore
from lyra.sdk import (
    LocationDependency,
    MetricParameters,
    Origin,
    PluginDefinition,
    metric,
)
from lyra.sdk.storage_schema import empty_frame
from tests.metric_execution_helpers import schema
from tests.provider_execution_helpers import provider_execution_definitions
from tests.sqlite_helpers import database

__all__ = ["database"]


class Parameters(MetricParameters):
    labels: list[str] = Field(default_factory=lambda: ["default"], description="Labels")
    optional: str | None = Field(default=None, description="Optional label")


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("spatial", [False, True])
@pytest.mark.parametrize("primary", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("multiple", [False, True])
def test_local_result_descriptions(
    tmp_path: Path, *, spatial: bool, primary: bool, empty: bool, multiple: bool
) -> None:
    output_schema = schema(spatial=spatial).update_column(
        "value", metadata={"unit": Unit.COUNT}
    )
    if not primary:
        output_schema = output_schema.rename_columns(
            {"location_id": "record_id", "row_key": "record_key"}
        )
    outputs = dict.fromkeys(["a", "b"] if multiple else ["a"], output_schema)
    retained = []
    fail = False

    @metric(
        name="findings",
        code_revision="1",
        description="Findings",
        inputs={"places": LocationDependency(provider="target", primary=primary)},
        outputs=outputs,
    )
    def findings(places: gpd.GeoDataFrame, parameters: Parameters) -> object:
        assert isinstance(places, gpd.GeoDataFrame)
        assert parameters.labels == ["default"]
        if fail:
            msg = "Deliberate failure"
            raise ValueError(msg)
        frame = (
            empty_frame(output_schema)
            if empty
            else pd.DataFrame(
                {
                    "location_id": ["a"],
                    "row_key": ["one"],
                    "value": pd.array([1], dtype="Int64"),
                }
            )
        )
        if not primary and not empty:
            frame = frame.rename(
                columns={"location_id": "record_id", "row_key": "record_key"}
            )
        if spatial and not empty:
            frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0)], crs=4326)
        frame.attrs = {"nested": [1]}
        retained.append(frame)
        return dict.fromkeys(outputs, frame) if multiple else frame

    definitions = provider_execution_definitions([])
    definitions["findings"] = PluginDefinition(metrics=[findings])
    path = tmp_path / "results.sqlite"
    runner = LocalRunner.from_definitions(definitions, result_store_path=path)
    inputs = {"places": {"provider": "target", "ids": ["a"]}}
    first = runner.run("findings", inputs=inputs)
    second = runner.run_development("findings", inputs=inputs)
    assert isinstance(first, RunResult)
    assert first.tables.keys() == first.metadata.outputs.keys() == outputs.keys()
    assert first.metadata.metric_name == "findings"
    assert first.metadata.parameters == {"labels": ["default"], "optional": None}
    declaration = runner.catalog.graph.nodes["findings"].node
    assert declaration.kind == "metric"
    assert_descriptions(
        first, declaration.outputs, primary=primary, empty=empty, spatial=spatial
    )

    assert_reopened(first, second, primary=primary, empty=empty, spatial=spatial)

    mutate_result(first, second, retained[0], empty=empty)
    assert second.metadata.parameters["labels"] == ["default"]
    assert second.metadata.outputs["a"].table_schema == declaration.outputs["a"]
    assert second.tables["a"].attrs["nested"] == [1]
    assert first.tables is first.tables
    assert first.metadata is first.metadata
    fail = True
    with pytest.raises(GraphExecutionError, match="Deliberate failure"):
        runner.run("findings", inputs=inputs)
    assert second.metadata.outputs["a"].table_schema == declaration.outputs["a"]


def mutate_result(
    first: RunResult, second: RunResult, producer: pd.DataFrame, *, empty: bool
) -> None:
    producer.attrs["nested"].append(2)
    assert first.tables["a"].attrs["nested"] == [1]
    first.tables["a"].attrs["nested"].append(3)
    labels = first.metadata.parameters["labels"]
    assert isinstance(labels, list)
    labels.append("edited")
    first.metadata.outputs["a"].table_schema.columns[0].description = "Edited"
    if not empty:
        producer.loc[0, "value"] = 99
        assert first.tables["a"].loc[0, "value"] == 1
        first.tables["a"].loc[0, "value"] = 42
        assert second.tables["a"].loc[0, "value"] == 1


def assert_descriptions(
    result: RunResult, outputs: dict, *, primary: bool, empty: bool, spatial: bool
) -> None:
    for name, frame in result.tables.items():
        description = result.metadata.outputs[name]
        assert description.table_schema == outputs[name]
        assert description.scope == ("location" if primary else "unpartitioned")
        assert frame.empty is empty
        assert isinstance(frame, gpd.GeoDataFrame) is spatial
        if primary:
            assert description.owner_scope is not None
            assert description.owner_scope.provider_id == "target"
            assert description.owner_scope.code_revision == "1"
            assert description.owner_scope.origins == frozenset(
                {Origin(id="external", revision="2025")}
            )
        else:
            assert description.owner_scope is None


def test_retained_provider_identity_survives_registration_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = provider_execution_definitions([])
    target = definitions["providers"].resources["target_binding"]
    origins = (Origin(id="external", revision="2025"),) * 2
    monkeypatch.setattr(target, "origins", origins)

    @metric(
        name="owned",
        code_revision="1",
        description="Owned values",
        inputs={"places": LocationDependency(provider="target", primary=True)},
        outputs={"data": schema()},
    )
    def owned(places: gpd.GeoDataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {"location_id": places.location_id, "row_key": "one", "value": 1}
        )

    definitions["owned"] = PluginDefinition(metrics=[owned])
    runner = LocalRunner.from_definitions(definitions)
    inputs = {"places": {"provider": "target", "ids": ["a"]}}
    first = runner.run("owned", inputs=inputs)
    monkeypatch.setattr(target, "code_revision", "2")
    current = LocalRunner.from_definitions(definitions)
    second = current.run_development("owned", inputs=inputs)
    previous_scope = first.metadata.outputs["data"].owner_scope
    current_scope = second.metadata.outputs["data"].owner_scope
    assert previous_scope is not None
    assert current_scope is not None
    assert previous_scope.code_revision == "1"
    assert current_scope.code_revision == "2"
    assert len(previous_scope.origins) == 1
    first.metadata.outputs["data"].owner_scope = None
    assert second.metadata.outputs["data"].owner_scope == current_scope
    assert (
        current.run("owned", inputs=inputs).metadata.outputs["data"].owner_scope
        == current_scope
    )


def assert_reopened(
    first: RunResult,
    second: RunResult,
    *,
    primary: bool,
    empty: bool,
    spatial: bool,
) -> None:
    first.metadata.store_path.touch()
    with ResultStore.open(first.metadata.store_path) as store:
        for original in (first, second):
            restored = store.read_result(original.metadata.result_ref)
            assert restored.report == original.report
            assert restored.metadata == original.metadata
            for name in original.tables:
                # Handler attributes are not retained output metadata.
                expected = original.tables[name].convert_dtypes()
                expected.attrs = {}
                pd.testing.assert_frame_equal(restored.tables[name], expected)
            assert_descriptions(
                restored,
                {
                    name: output.table_schema
                    for name, output in second.metadata.outputs.items()
                },
                primary=primary,
                empty=empty,
                spatial=spatial,
            )
    assert restored.report == second.report
    assert_descriptions(
        restored,
        {name: output.table_schema for name, output in second.metadata.outputs.items()},
        primary=primary,
        empty=empty,
        spatial=spatial,
    )
