"""Private variants remain executable dependencies but are not entry points."""

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Dependency,
    LocalRunner,
    PluginDefinition,
    metric,
)
from lyra.sdk.preparation import PreparationError


def test_private_dependency_reuse_and_development_access(tmp_path: Path) -> None:
    calls = []
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description="Value", metadata={"unit": "count"})}
    )

    @metric(name="source", description="Source", outputs={"data": schema}, private=True)
    def source() -> pd.DataFrame:
        calls.append(1)
        return pd.DataFrame({"value": [1]})

    @metric(
        name="root",
        description="Root",
        outputs={"data": schema},
        inputs={"data": Dependency("data", producer="source")},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        return data

    plugin = PluginDefinition(metrics=[source, root])
    runner = LocalRunner.from_definitions({"plugin": plugin}, artifact_dir=tmp_path)
    for _ in range(2):
        assert runner.run("root", {})["data"].value.tolist() == [1]
    assert calls == [1]
    with pytest.raises(PreparationError, match="Unknown public metric"):
        runner.run("source", {})
    assert runner.run_development("source", {})["data"].value.tolist() == [1]
    assert set(plugin.metric_names) == {"root", "source"}


@pytest.mark.parametrize("visibility", [(False, True), (True, True)])
def test_edition_visibility(visibility: tuple[bool, bool]) -> None:
    def variant(name: str, *, private: bool) -> Callable[[], pd.DataFrame]:
        @metric(
            name=name,
            family="family",
            edition=name,
            description="Variant",
            outputs={"data": pa.DataFrameSchema({})},
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
