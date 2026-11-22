"""Edition registration preserves concrete graph identity."""

from collections.abc import Callable

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.location_validation import validate_location_families
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    PluginInfo,
)
from lyra.runtime.invocation import BoundPlugin
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_schema import dataset
from test_support.plugin_graph import node
from tests.graph_execution_helpers import executor


def test_factory_graphs_execute_separately() -> None:
    calls: list[str] = []
    schema = pa.DataFrameSchema(
        {
            "value": pa.Column(
                int, description="Population count", metadata={"unit": None}
            )
        },
        strict=True,
    )

    def factory(
        edition: str, value: int
    ) -> tuple[Callable[..., object], Callable[..., object]]:
        @metric(
            code_revision="1",
            name=f"source_{edition}",
            description="Source",
            outputs={"result": schema},
        )
        def source() -> pd.DataFrame:
            calls.append(edition)
            return pd.DataFrame({"value": [value]})

        @metric(
            code_revision="1",
            name=f"population_{edition}",
            description="Population count",
            outputs={"result": schema},
            inputs={"table": Dependency("result", producer=f"source_{edition}")},
        )
        def population(table: pd.DataFrame) -> pd.DataFrame:
            return table

        return population, source

    first, source_first = factory("first", 1)
    second, source_second = factory("second", 2)
    plugin = PluginDefinition(metrics=[first, source_first, second, source_second])
    binding = BoundPlugin(plugin)
    runner, _ = executor(first, source_first, second, source_second)
    for edition, expected in [("first", 1), ("second", 2)]:
        name = f"population_{edition}"
        result = runner.execute(binding.prepare_resolved_invocation(name, {})).outputs
        assert result["result"]["value"].tolist() == [expected]
    assert calls == ["first", "second"]
    restored = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    restored_metric = next(n for n in restored.nodes if n.name == "population_first")
    assert restored_metric.kind == "metric"
    assert restored_metric.name == "population_first"


def test_location_structure_conflicts_still_rejected() -> None:
    first, second = node("first"), node("second")
    first.location_inputs = {"places": LocationInputDeclaration(contract=dataset())}
    second.location_inputs = {"places": LocationInputDeclaration(contract=dataset())}
    second.location_inputs["places"].contract.family.structure.output.columns[
        -1
    ].nullable = True
    with pytest.raises(ValueError, match="incompatible structure"):
        validate_location_families([first, second])
