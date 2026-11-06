"""Edition registration preserves concrete graph identity."""

from collections.abc import Callable

import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import Dependency, PluginDefinition, metric
from lyra.sdk.editions import EditionRegistry
from lyra.sdk.graph import build_graph
from lyra.sdk.models.output import ColumnMetadata
from lyra.sdk.models.plugin import (
    LocationInputDeclaration,
    MetricManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.sdk.types import JsonObject
from lyra.sdk.units import Unit

from tests.test_graph_execution import executor
from tests.test_location_contracts import dataset
from tests.test_plugin_graph import manifest, node


def variant(name: str, edition: str | None) -> MetricManifest:
    return node(name).model_copy(
        update={
            "family": "population",
            "edition": edition,
            "outputs": node("shared").outputs,
        }
    )


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
        result = runner.execute(plugin.prepare_resolved_invocation(name, {})).outputs
        assert result["result"]["value"].tolist() == [expected]
    assert calls == ["first", "second"]
    restored = PluginManifest.model_validate_json(
        plugin.manifest(
            plugin=PluginInfo(name="test", version="1"),
            factory="test:create",
        ).model_dump_json()
    )
    restored_metric = next(n for n in restored.nodes if n.name == "population_first")
    assert restored_metric.kind == "metric"
    assert restored_metric.edition_metadata == {
        "release": "first",
        "method": {"factor": 1},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dtype", "float64"),
        ("nullable", True),
        ("unique", True),
        ("unit", Unit.COUNT),
        ("name", "other"),
    ],
)
def test_family_rejects_output_changes(field: str, value: object) -> None:
    first, second = variant("first", "first"), variant("second", "second")
    for item in (first, second):
        next(iter(item.outputs.values())).columns = [
            ColumnMetadata(name="value", dtype="int64", unit=None, description="Value")
        ]
    output = next(iter(second.outputs.values()))
    output.columns[0] = output.columns[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match=r"population.*first.*second.*outputs"):
        EditionRegistry([first, second])


def test_family_parameter_constraints_and_reference_names() -> None:
    first, second = variant("first", "first"), variant("second", "second")
    for item, name in [(first, "A"), (second, "B")]:
        item.parameters_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {name: {"type": "integer", "minimum": 0, "default": 1}},
            "properties": {"value": {"$ref": f"#/$defs/{name}"}},
        }
    registry = EditionRegistry([first, second])
    assert "population" in registry.contracts
    assert second.parameters_schema is not None
    second.parameters_schema["$defs"] = {
        "B": {"type": "integer", "minimum": 1, "default": 1}
    }
    with pytest.raises(ValueError, match="ordinary parameters"):
        EditionRegistry([first, second])


@pytest.mark.parametrize("change", ["role", "family", "structure", "output"])
def test_family_structural_boundaries(change: str) -> None:
    first, second = variant("first", "first"), variant("second", "second")
    first.location_inputs = {"places": LocationInputDeclaration(contract=dataset())}
    second.location_inputs = {
        "places": LocationInputDeclaration(
            contract=dataset(source="other", observation_period="2024")
        )
    }
    EditionRegistry([first, second])
    if change == "role":
        second.location_inputs["other"] = second.location_inputs.pop("places")
    elif change == "family":
        second.location_inputs["places"].contract.family.unit_type = "other"
    elif change == "structure":
        second.location_inputs["places"].contract.family.structure.output.columns[
            -1
        ].nullable = True
    else:
        second.outputs["other"] = second.outputs.pop("shared")
    with pytest.raises(ValueError, match="incompatible"):
        EditionRegistry([first, second])


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "number", "default": 1},
        {"type": "integer", "default": 2},
        {"type": ["integer", "null"], "default": 1},
        {"type": "integer", "default": 1, "enum": [1, 2]},
        {"type": "integer", "default": 1, "maximum": 5},
    ],
)
def test_parameter_contract_changes(schema: JsonObject) -> None:
    first, second = variant("first", "first"), variant("second", "second")
    first.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer", "default": 1}},
    }
    second.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": schema},
    }
    with pytest.raises(ValueError, match="ordinary parameters"):
        EditionRegistry([first, second])


def test_reference_annotations_do_not_change_parameter_contract() -> None:
    first, second = variant("first", "first"), variant("second", "second")
    for item in (first, second):
        item.parameters_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {"Value": {"type": "integer"}},
            "properties": {"value": {"$ref": "#/$defs/Value"}},
        }
    assert second.parameters_schema is not None
    second.parameters_schema["properties"] = {
        "value": {"$ref": "#/$defs/Value", "description": "A value"}
    }
    EditionRegistry([first, second])


def test_constraint_payload_keys_are_not_descriptive_metadata() -> None:
    first, second = variant("first", "first"), variant("second", "second")
    for item, value in ((first, "a"), (second, "b")):
        next(iter(item.outputs.values())).columns = [
            ColumnMetadata(
                name="value",
                dtype="category",
                unit=None,
                description="Value",
                parameters={"description": value},
            )
        ]
    with pytest.raises(ValueError, match="outputs"):
        EditionRegistry([first, second])
