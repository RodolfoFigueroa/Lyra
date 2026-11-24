"""Private variants remain executable dependencies but are not entry points."""

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.dependencies import Dependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric


def test_private_dependency_reuse_and_development_access(tmp_path: Path) -> None:
    calls = []
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": "count"})},
        strict=True,
    )

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"data": schema},
        private=True,
    )
    def source() -> pd.DataFrame:
        calls.append(1)
        return pd.DataFrame({"value": [1]})

    @metric(
        cache="full",
        code_revision="1",
        name="root",
        description="Root",
        outputs={"data": schema},
        inputs={"data": Dependency("data", producer="source")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    plugin = PluginDefinition(metrics=[source, root])
    runner = LocalRunner.from_definitions(
        {"plugin": plugin}, result_store_path=tmp_path / "results.sqlite3"
    )
    for _ in range(2):
        assert runner.run("root", inputs={}).tables["data"].value.tolist() == [1]
    assert calls == [1]
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run("source", inputs={})
    assert runner.run_development("source", inputs={}).tables[
        "data"
    ].value.tolist() == [1]
    assert set(plugin.metric_names) == {"root", "source"}


@pytest.mark.parametrize("visibility", [(False, True), (True, True)])
def test_metric_visibility(visibility: tuple[bool, bool]) -> None:
    def variant(name: str, *, private: bool) -> Callable[[], pd.DataFrame]:
        @metric(
            code_revision="1",
            name=name,
            description="Variant",
            outputs={"data": pa.DataFrameSchema({}, strict=True)},
            private=private,
        )
        def handler() -> pd.DataFrame:
            return pd.DataFrame()

        return handler

    plugin = PluginDefinition(
        metrics=[
            variant("one", private=visibility[0]),
            variant("two", private=visibility[1]),
        ]
    )
    assert [node.name for node in plugin.node_manifests if node.is_public_metric] == (
        [] if visibility[0] else ["one"]
    )


def test_each_live_metric_owns_its_schema_and_computed_inputs() -> None:
    class Count(MetricParameters):
        count: int = Field(2, ge=1, description="Count")

    class Label(MetricParameters):
        label: str = Field(description="Label")

    numbers = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": None})},
        strict=True,
    )
    labels = pa.DataFrameSchema(
        {"label": pa.Column(str, description="Label", metadata={"unit": None})},
        strict=True,
    )
    calls = []

    @metric(
        code_revision="1",
        name="numbers",
        description="Numbers",
        outputs={"numbers": numbers},
    )
    def number_metric(*, parameters: Count) -> pd.DataFrame:
        calls.append("numbers")
        return pd.DataFrame({"value": [parameters.count]})

    @metric(
        code_revision="1",
        name="labels",
        description="Labels",
        outputs={"labels": labels},
    )
    def label_metric(*, parameters: Label) -> pd.DataFrame:
        calls.append("labels")
        return pd.DataFrame({"label": [parameters.label]})

    @metric(
        code_revision="1",
        name="combined",
        description="Combined",
        outputs={"result": numbers},
        inputs={"values": Dependency("numbers", producer="numbers")},
    )
    def combined(values: pd.DataFrame) -> pd.DataFrame:
        return values

    runner = LocalRunner.from_definitions(
        {"test": PluginDefinition(metrics=[number_metric, label_metric, combined])}
    )
    view = Discovery(runner.catalog)
    numeric = view.inspect("numbers")
    textual = view.inspect("labels")
    properties = numeric.parameters["properties"]
    assert isinstance(properties, dict)
    count = properties["count"]
    assert isinstance(count, dict)
    assert count["minimum"] == 1
    assert textual.parameters["required"] == ["label"]
    assert set(numeric.outputs) == {"numbers"}
    assert set(textual.outputs) == {"labels"}
    detail = view.inspect("combined")
    assert detail.metric.accepted_selections == {}
    assert detail.computed_inputs["values"]["producer"] == "numbers"
    detail.computed_inputs.clear()
    assert view.inspect("combined").computed_inputs
    assert prepare_execution(runner.catalog, {"metric": "numbers"}).parameters == {
        "count": 2
    }
    for request in (
        {"metric": "labels"},
        {"metric": "numbers", "parameters": {"label": "wrong"}},
        {"metric": "combined", "inputs": {"values": {}}},
    ):
        with pytest.raises(PreparationError):
            prepare_execution(runner.catalog, request)
    assert calls == []
    assert runner.run("numbers", inputs={}).tables["numbers"].value.tolist() == [2]
    assert runner.run("labels", inputs={}, parameters={"label": "ok"}).tables[
        "labels"
    ].label.tolist() == ["ok"]
