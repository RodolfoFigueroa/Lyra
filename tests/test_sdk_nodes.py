"""Local graph declarations are independent of source access and graph execution."""

from __future__ import annotations

import asyncio
import builtins
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pandas as pd
import pytest
from lyra.sdk import (
    DatasetContext,
    Dependency,
    Input,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    RunContext,
    TableColumn,
    TableOutput,
    dataset,
    metric,
)
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from pydantic import Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

OUTPUT = TableOutput(columns=[])


class Parameters(MetricParameters):
    year: int = Field(default=2020, description="Reference year.")


def manifest(plugin: PluginDefinition) -> PluginManifest:
    return plugin.manifest(
        plugin=PluginInfo(name="local", version="1"), factory="local:create"
    )


def test_registration_is_local_and_direct_calls_only_use_supplied_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = Mock()

    @dataset(
        name="producer", description="Acquire records.", outputs={"records": OUTPUT}
    )
    def producer() -> pd.DataFrame:
        calls()
        return pd.DataFrame()

    @metric(
        name="consumer",
        description="Consume records.",
        inputs={"table": Dependency("foreign_records")},
        outputs={"summary": OUTPUT},
    )
    def consumer(table: pd.DataFrame, *, context: RunContext) -> pd.DataFrame:
        assert context is supplied_context
        return table

    supplied_context = Mock(spec=RunContext)
    table = pd.DataFrame({"value": [1]})
    imports = Mock(side_effect=AssertionError("registration must not import producers"))
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", imports)
        plugin = PluginDefinition(nodes=[producer, consumer])
        local = manifest(plugin)
    imports.assert_not_called()
    calls.assert_not_called()
    assert consumer(table, context=supplied_context) is table
    calls.assert_not_called()
    assert plugin.metric_names == ("consumer",)
    assert plugin.node_names == ("consumer", "producer")
    assert local.plugin.name == "local"
    assert local.nodes[0].request_schema["properties"] == {}
    assert set(local.nodes[0].outputs) == {"summary"}
    assert local.nodes[0].inputs["table"].output == "foreign_records"
    assert PluginManifest.model_validate_json(local.model_dump_json()) == local


@pytest.mark.parametrize("context_type", [RunContext, DatasetContext])
def test_dataset_only_registration(context_type: type) -> None:
    def handler(context: object) -> object:
        return context

    handler.__annotations__["context"] = context_type
    dataset(name="source", description="Source.", outputs={"data": OUTPUT})(handler)
    plugin = PluginDefinition(nodes=[handler])
    assert plugin.metric_names == ()
    assert manifest(plugin).nodes[0].role == "dataset"
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(nodes=[handler, handler])


def test_metric_cannot_declare_database_context() -> None:
    def handler(context: DatasetContext) -> None:
        del context

    with pytest.raises(PluginDefinitionError, match="context"):
        metric(name="test", description="Test.", outputs={"data": OUTPUT})(handler)


@pytest.mark.parametrize("decorate", [metric, dataset])
def test_outputs_required_and_spatial_inputs_optional(decorate: Callable) -> None:
    def handler() -> pd.DataFrame:
        return pd.DataFrame()

    with pytest.raises(PluginDefinitionError):
        decorate(name="test", description="Test.", outputs={})(handler)
    decorate(name="test", description="Test.", outputs={"different": OUTPUT})(handler)
    assert manifest(PluginDefinition(nodes=[handler])).nodes[0].spatial_inputs == {}


@pytest.mark.parametrize(
    "key", ["parameters", "context", "location", "bounds", "missing"]
)
@pytest.mark.parametrize("decorate", [metric, dataset])
def test_invalid_dependency_keys(key: str, decorate: Callable) -> None:
    def handler(table: pd.DataFrame) -> pd.DataFrame:
        return table

    with pytest.raises(PluginDefinitionError):
        decorate(
            name="test",
            description="Test.",
            outputs={"result": OUTPUT},
            inputs={key: Dependency("foreign")},
        )(handler)


@pytest.mark.parametrize(
    "path",
    [
        "context.job_id",
        "location",
        "parameters.year.value",
        "parameters.year + 1",
        "table.value",
        "parameters.",
    ],
)
def test_invalid_reference_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        Input(path)


@pytest.mark.parametrize(
    "arguments",
    [
        {"location": {}},
        {"bounds": {}},
        {"context": {}},
        {"parameters": {"nested.field": 1}},
        {"parameters": {"year": float("inf")}},
        {"parameters": {"year": [Input("parameters.year")]}},
    ],
)
def test_invalid_override_syntax(arguments: dict) -> None:
    with pytest.raises((ValidationError, ValueError, TypeError)):
        Dependency("foreign", arguments=arguments)


def test_override_metadata_roundtrips_without_losing_literal_objects() -> None:
    literals = {"kind": "input", "path": "parameters.missing"}
    dependency = Dependency(
        "foreign",
        arguments={
            "parameters": {
                "baseline": Input("parameters.year"),
                "year": 2021,
                "selection": literals,
                "nullable": None,
            }
        },
    )

    @metric(
        name="test",
        description="Test.",
        outputs={"result": OUTPUT},
        inputs={"table": dependency},
    )
    def handler(parameters: Parameters, table: pd.DataFrame) -> pd.DataFrame:
        del parameters
        return table

    local = manifest(PluginDefinition(nodes=[handler]))
    restored = PluginManifest.model_validate_json(local.model_dump_json())
    assert restored == local
    values = restored.nodes[0].inputs["table"].arguments["parameters"]
    assert values["baseline"] == Input("parameters.year")
    selection = values["selection"]
    assert not isinstance(selection, Input)
    assert selection.value == literals
    properties = local.nodes[0].request_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"parameters"}
    assert PluginManifest.model_validate(local.model_dump()) == local
    description = PluginDefinition(nodes=[handler]).describe("test")
    assert description.inputs == local.nodes[0].inputs
    assert description.inputs["table"].arguments["parameters"]["baseline"] == Input(
        "parameters.year"
    )


def test_reference_requires_a_direct_declared_field() -> None:
    def handler(parameters: Parameters, table: pd.DataFrame) -> None:
        del parameters, table

    with pytest.raises(PluginDefinitionError, match="undeclared input reference"):
        metric(
            name="test",
            description="Test.",
            outputs={"data": OUTPUT},
            inputs={
                "table": Dependency(
                    "foreign",
                    arguments={"parameters": {"year": Input("parameters.missing")}},
                )
            },
        )(handler)


@pytest.mark.parametrize("decorate", [metric, dataset])
def test_signature_categories(decorate: Callable) -> None:
    async def coroutine() -> None:
        pass

    def generator() -> Iterator[int]:
        yield 1

    async def async_generator() -> AsyncIterator[int]:
        await asyncio.sleep(0)
        yield 1

    def positional(table: object, /) -> None:
        del table

    def variadic(*args: object) -> None:
        del args

    def keywords(**kwargs: object) -> None:
        del kwargs

    def default(table: object = None) -> None:
        del table

    def undeclared(table: object) -> None:
        del table

    for handler in [
        coroutine,
        generator,
        async_generator,
        positional,
        variadic,
        keywords,
        default,
        undeclared,
    ]:
        with pytest.raises(PluginDefinitionError):
            decorate(name="test", description="Test.", outputs={"data": OUTPUT})(
                handler
            )


def test_spatial_date_and_nullable_output_metadata() -> None:
    output = TableOutput(
        columns=[
            TableColumn(name="day", type="date", unit=None, description="Day."),
            TableColumn(
                name="time",
                type="datetime",
                unit=None,
                description="Timestamp.",
                nullable=True,
            ),
        ],
        geometry="shape",
    )
    assert TableOutput.model_validate_json(output.model_dump_json()) == output
    assert output.columns[1].nullable
    assert output.geometry == "shape"
    with pytest.raises(ValidationError):
        TableOutput(columns=output.columns, geometry="day")
