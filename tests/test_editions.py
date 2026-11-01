"""Edition registration preserves concrete graph identity."""

from collections.abc import Callable

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import Dependency, PluginDefinition, metric
from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import build_graph
from lyra.sdk.models.plugin import NodeManifest, PluginInfo, PluginManifest

from tests.test_graph_execution import executor
from tests.test_plugin_graph import manifest, node


def variant(name: str, edition: str | None) -> NodeManifest:
    return node(name).model_copy(update={"family": "population", "edition": edition})


def test_selection_and_round_trip() -> None:
    for editions in [(None,), ("first",), ("first", "second")]:
        nodes = [variant(f"metric_{i}", edition) for i, edition in enumerate(editions)]
        original = manifest("editions", *nodes)
        restored = PluginManifest.model_validate_json(original.model_dump_json())
        assert restored == original
        registry = build_graph({"plugin": restored.nodes}).editions
        for i, edition in enumerate(editions):
            assert registry.resolve("population", edition=edition) == f"metric_{i}"
        if editions[0] is not None:
            with pytest.raises(ValueError, match="explicit edition"):
                registry.resolve("population")
        with pytest.raises(ValueError, match="Unknown edition"):
            registry.resolve("population", edition="missing")
        with pytest.raises(ValueError, match="Unknown metric family"):
            registry.resolve("missing")


def test_duplicate_and_mixed_registration() -> None:
    for editions in [
        ("first", "first"),
        (None, None),
        (None, "first"),
        ("first", None),
    ]:
        with pytest.raises(ValueError, match=r"Duplicate|mixes"):
            EditionRegistry([variant(f"metric_{i}", e) for i, e in enumerate(editions)])


def test_equal_schemas_do_not_create_family() -> None:
    registry = EditionRegistry([node("a"), node("b")])
    assert registry.resolve("a") == "a"
    assert registry.resolve("b") == "b"


def test_factory_graphs_execute_separately() -> None:
    calls: list[str] = []
    schema = pa.DataFrameSchema(
        {
            "value": pa.Column(
                int, description="Population count", metadata={"unit": None}
            )
        }
    )

    def factory(
        edition: str, value: int
    ) -> tuple[Callable[..., object], Callable[..., object]]:
        @metric(
            name=f"source_{edition}", description="Source", outputs={"result": schema}
        )
        def source() -> pd.DataFrame:
            calls.append(edition)
            return pd.DataFrame({"value": [value]})

        @metric(
            name=f"population_{edition}",
            family="population",
            edition=edition,
            edition_metadata={"release": edition, "method": {"factor": value}},
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
    runner, _ = executor(first, source_first, second, source_second)
    for edition, expected in [("first", 1), ("second", 2)]:
        name = plugin.editions.resolve("population", edition=edition)
        result = runner.execute(plugin.prepare_invocation(name, {}))
        assert result["result"]["value"].tolist() == [expected]
    assert calls == ["first", "second"]
    restored = PluginManifest.model_validate_json(
        plugin.manifest(
            plugin=PluginInfo(name="test", version="1"),
            factory="test:create",
        ).model_dump_json()
    )
    assert next(
        n for n in restored.nodes if n.name == "population_first"
    ).edition_metadata == {"release": "first", "method": {"factor": 1}}
