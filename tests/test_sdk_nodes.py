"""Local graph declarations are independent of source access and graph execution."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, Annotated
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas import GeoDataFrame
from pydantic import Field, ValidationError

from lyra.contracts.dependencies import Input
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.sdk.context import RunContext
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import (
    NodeImplementation,
    PluginDefinition,
    location_operation,
    metric,
)
from lyra.sdk.resources import BaseIO, Resource
from test_support.table_fixtures import value_frame, value_schema
from tests.provider_helpers import FakeProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

OUTPUT = value_schema()


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

    @metric(
        code_revision="1",
        name="producer",
        description="Acquire records.",
        outputs={"records": OUTPUT},
    )
    def producer() -> pd.DataFrame:
        calls()
        return value_frame()

    @metric(
        code_revision="1",
        name="consumer",
        description="Consume records.",
        inputs={"table": Dependency("foreign_records", producer="foreign_records")},
        outputs={"summary": OUTPUT},
    )
    def consumer(table: pd.DataFrame, *, context: RunContext) -> pd.DataFrame:
        assert context is supplied_context
        return table

    supplied_context = Mock(spec=RunContext)
    table = pd.DataFrame({"value": [1]})
    finder = Mock()
    finder.find_spec.side_effect = AssertionError("registration loaded a new module")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "meta_path", [finder, *sys.meta_path])
        plugin = PluginDefinition(metrics=[producer, consumer])
        local = manifest(plugin)
    finder.find_spec.assert_not_called()
    calls.assert_not_called()
    assert consumer(table, context=supplied_context) is table
    calls.assert_not_called()
    assert plugin.metric_names == ("consumer", "producer")
    assert plugin.node_names == ("consumer", "producer")
    assert local.plugin.name == "local"
    assert local.nodes[0].request_schema["properties"] == {}
    assert local.nodes[0].kind == "metric"
    assert set(local.nodes[0].outputs) == {"summary"}
    assert local.nodes[0].inputs["table"].output == "foreign_records"


@pytest.mark.parametrize("context_type", [RunContext])
def test_source_registration(context_type: type) -> None:
    def handler(context: object) -> object:
        return context

    handler.__annotations__["context"] = context_type
    metric(
        code_revision="1",
        name="source",
        description="Source.",
        outputs={"data": OUTPUT},
    )(handler)
    plugin = PluginDefinition(metrics=[handler])
    assert plugin.metric_names == ("source",)
    assert "role" not in manifest(plugin).nodes[0].model_dump()
    with pytest.raises(PluginDefinitionError, match="duplicate"):
        PluginDefinition(metrics=[handler, handler])


def test_metric_can_declare_database_context() -> None:
    def handler(context: RunContext) -> None:
        del context

    metric(
        code_revision="1", name="test", description="Test.", outputs={"data": OUTPUT}
    )(handler)


@pytest.mark.parametrize("decorate", [metric])
def test_outputs_required_and_spatial_inputs_optional(decorate: Callable) -> None:
    def handler() -> pd.DataFrame:
        return value_frame()

    with pytest.raises(PluginDefinitionError):
        decorate(code_revision="1", name="test", description="Test.", outputs={})(
            handler
        )
    decorate(
        code_revision="1",
        name="test",
        description="Test.",
        outputs={"different": OUTPUT},
    )(handler)
    assert manifest(PluginDefinition(metrics=[handler])).nodes[0].location_inputs == {}


@pytest.mark.parametrize(
    "key", ["parameters", "context", "location", "bounds", "missing"]
)
@pytest.mark.parametrize("decorate", [metric])
def test_invalid_dependency_keys(key: str, decorate: Callable) -> None:
    def handler(table: pd.DataFrame) -> pd.DataFrame:
        return table

    with pytest.raises(PluginDefinitionError):
        decorate(
            code_revision="1",
            name="test",
            description="Test.",
            outputs={"result": OUTPUT},
            inputs={key: Dependency("foreign", producer="foreign")},
        )(handler)


@pytest.mark.parametrize(
    "path",
    [
        "context.job_id",
        "location.child",
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
        Dependency("foreign", **arguments, producer="foreign")


def test_override_metadata_roundtrips_without_losing_literal_objects() -> None:
    literals = {"kind": "input", "path": "parameters.missing"}
    dependency = Dependency(
        "foreign",
        parameters={
            "baseline": Input("parameters.year"),
            "year": 2021,
            "selection": literals,
            "nullable": None,
        },
        producer="foreign",
    )

    @metric(
        code_revision="1",
        name="test",
        description="Test.",
        outputs={"result": OUTPUT},
        inputs={"table": dependency},
    )
    def handler(parameters: Parameters, table: pd.DataFrame) -> pd.DataFrame:
        del parameters
        return table

    local = manifest(PluginDefinition(metrics=[handler]))
    values = local.nodes[0].inputs["table"].parameters
    assert values["baseline"] == Input("parameters.year")
    selection = values["selection"]
    assert not isinstance(selection, Input)
    assert selection == literals
    properties = local.nodes[0].request_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"parameters"}
    description = PluginDefinition(metrics=[handler]).describe("test")
    assert description.manifest.inputs == local.nodes[0].inputs
    assert description.manifest.inputs["table"].parameters["baseline"] == Input(
        "parameters.year"
    )


def test_reference_requires_a_direct_declared_field() -> None:
    def handler(parameters: Parameters, table: pd.DataFrame) -> None:
        del parameters, table

    with pytest.raises(PluginDefinitionError, match="undeclared input reference"):
        metric(
            code_revision="1",
            name="test",
            description="Test.",
            outputs={"data": OUTPUT},
            inputs={
                "table": Dependency(
                    "foreign",
                    parameters={"year": Input("parameters.missing")},
                    producer="foreign",
                )
            },
        )(handler)


@pytest.mark.parametrize("decorate", [metric])
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
            decorate(
                code_revision="1",
                name="test",
                description="Test.",
                outputs={"data": OUTPUT},
            )(handler)


def test_spatial_date_and_nullable_output_metadata() -> None:
    output = OutputMetadata(
        columns=[
            ColumnMetadata(name="day", dtype="date", unit=None, description="Day."),
            ColumnMetadata(
                name="time",
                dtype="datetime64[ns]",
                unit=None,
                description="Timestamp.",
                nullable=True,
            ),
            ColumnMetadata(
                name="shape", dtype="geometry", description="Geometry", unit=None
            ),
        ],
        geometry="shape",
    )
    assert OutputMetadata.model_validate_json(output.model_dump_json()) == output
    assert output.columns[1].nullable
    assert output.geometry == "shape"
    with pytest.raises(ValidationError):
        OutputMetadata(columns=output.columns, geometry="day")


@pytest.mark.parametrize(
    "path", ["lots", "missing", "parameters.year", "data", "context"]
)
def test_decorated_location_bindings_validate_without_handlers(path: str) -> None:
    @metric(
        code_revision="1",
        inputs={"location": LocationDependency(provider=FakeProvider.id)},
        name="source",
        description="Source.",
        outputs={"source": OUTPUT},
    )
    def source(location: GeoDataFrame) -> pd.DataFrame:
        pytest.fail(f"Executed source: {location}")

    def consumer(
        lots: GeoDataFrame, parameters: Parameters, data: pd.DataFrame
    ) -> pd.DataFrame:
        pytest.fail(f"Executed consumer: {lots}, {parameters}, {data}")

    if path == "parameters.year":
        with pytest.raises(ValueError, match=r"locations.location.*location reference"):
            Dependency("source", producer="source").bind(location=Input(path))
        return
    decorate = metric(
        code_revision="1",
        name="consumer",
        description="Forward lots.",
        outputs={"summary": OUTPUT},
        inputs={
            "lots": LocationDependency(provider=FakeProvider.id),
            "data": Dependency("source", producer="source").bind(location=Input(path)),
        },
    )
    if path != "lots":
        with pytest.raises(PluginDefinitionError, match="undeclared input reference"):
            decorate(consumer)
        return
    plugin = PluginDefinition(
        metrics=[source, decorate(consumer)], resources={"places": FakeProvider}
    )
    edge = compile_live_catalog({"local": plugin}).graph.dependencies["consumer"][
        "data"
    ]
    assert edge.dependency.locations["location"] == Input("lots")


def test_decorated_parameter_binding_rejects_location_reference() -> None:
    def consumer(
        lots: GeoDataFrame, parameters: Parameters, data: pd.DataFrame
    ) -> pd.DataFrame:
        pytest.fail(f"Executed consumer: {lots}, {parameters}, {data}")

    with pytest.raises(ValueError, match=r"parameters.year.*parameter reference"):
        metric(
            code_revision="1",
            name="consumer",
            description="Invalid category.",
            outputs={"summary": OUTPUT},
            inputs={
                "lots": LocationDependency(provider=FakeProvider.id),
                "data": Dependency(
                    "source", parameters={"year": Input("lots")}, producer="source"
                ),
            },
        )(consumer)


def test_metric_implementation_owns_its_declarations() -> None:
    table = pa.DataFrameSchema(
        {
            "value": pa.Column(
                int, description="Measured value", metadata={"unit": None}
            )
        },
        strict=True,
    )

    @metric(
        code_revision="1",
        name="measurement",
        description="Measure",
        outputs={"table": table},
    )
    def measurement(
        parameters: Parameters,
        context: RunContext,
        source: Annotated[BaseIO, Resource("reader")],
    ) -> object:
        pytest.fail(f"Executed during inspection: {parameters}, {context}, {source}")

    table.columns.clear()
    plugin = PluginDefinition(metrics=[measurement], resources={"reader": BaseIO})
    binding = BoundPlugin(plugin)
    impl = plugin.implementation("measurement")
    assert isinstance(impl, NodeImplementation)
    assert impl.function is measurement
    assert impl.parameters is Parameters
    assert impl.accepts_context
    assert impl.location_schema is None
    assert impl.manifest.resources == {"source": "reader"}
    assert impl.manifest == plugin.describe("measurement").manifest
    captured = impl.schemas["table"]
    assert set(captured.columns) == {"value"}
    with pytest.raises(FrozenInstanceError):
        impl.accepts_context = False  # ty: ignore[invalid-assignment]

    impl.schemas.clear()
    impl.manifest.resources.clear()
    assert impl.manifest.parameters_schema is not None
    impl.manifest.parameters_schema.clear()
    fresh = plugin.implementation("measurement")
    assert fresh.schemas["table"] is captured
    assert fresh.manifest.resources == {"source": "reader"}
    assert binding.prepare_parameters("measurement", {}) == Parameters()
    with pytest.raises(
        PluginDefinitionError, match=r"missing.*available nodes: measurement"
    ):
        plugin.implementation("missing")


def test_operation_implementation_owns_location_metadata() -> None:
    @location_operation(
        code_revision="1",
        name="select",
        description="Select",
        inputs={"source": LocationDependency(provider=FakeProvider.id)},
        target_provider=FakeProvider.id,
    )
    def select(source: gpd.GeoDataFrame) -> object:
        pytest.fail(f"Executed during inspection: {source}")

    plugin = PluginDefinition(
        location_operations=[select], resources={"places": FakeProvider}
    )
    impl = plugin.implementation("select")
    assert impl.function is select
    assert impl.parameters is None
    assert not impl.accepts_context
    assert impl.schemas == {}
    assert impl.manifest.kind == "location_operation"
    contract = impl.manifest.output_contract.model_copy(deep=True)
    assert impl.location_schema is not None
    assert impl.location_schema.metadata == contract.structure
    impl.location_schema.metadata.output.columns.clear()
    impl.location_schema.geometry_column = "changed"
    impl.manifest.output_contract.structure.output.columns.clear()
    fresh = plugin.implementation("select")
    assert fresh.location_schema is not None
    assert fresh.location_schema.id_column == "location_id"
    assert fresh.location_schema.geometry_column == "geometry"
    assert fresh.location_schema.metadata == contract.structure
    assert fresh.manifest.kind == "location_operation"
    assert fresh.manifest.output_contract == contract


class OperationParameters(MetricParameters):
    radius: float = Field(10.0, description="Search radius")


def test_operations_infer_independent_parameter_models() -> None:
    def measure(*, parameters: Parameters) -> None:
        pytest.fail(f"Executed metric during registration: {parameters}")

    def select(source: gpd.GeoDataFrame, *, parameters: OperationParameters) -> None:
        pytest.fail(f"Executed operation during registration: {source}, {parameters}")

    metric(
        code_revision="1",
        name="measure",
        description="Measure",
        outputs={"table": OUTPUT},
    )(measure)
    location_operation(
        code_revision="1",
        name="select",
        description="Select",
        inputs={"source": LocationDependency(provider=FakeProvider.id)},
        target_provider=FakeProvider.id,
    )(select)
    plugin = PluginDefinition(
        metrics=[measure],
        location_operations=[select],
        resources={"places": FakeProvider},
    )
    assert plugin.implementation("measure").parameters is Parameters
    assert plugin.implementation("select").parameters is OperationParameters
    schema = plugin.describe("select").manifest.parameters_schema
    assert schema is not None
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert "radius" in properties


@pytest.mark.parametrize("annotation", [str, Parameters | None, None])
def test_operations_reject_invalid_parameter_annotations(annotation: object) -> None:
    def select(source: gpd.GeoDataFrame, *, parameters: object) -> None:
        pytest.fail(f"Executed operation: {source}, {parameters}")

    select.__annotations__["source"] = gpd.GeoDataFrame
    select.__annotations__["parameters"] = annotation
    with pytest.raises(PluginDefinitionError, match="annotation"):
        location_operation(
            code_revision="1",
            name="select",
            description="Select",
            inputs={"source": LocationDependency(provider=FakeProvider.id)},
            target_provider=FakeProvider.id,
        )(select)


def test_operation_parameters_must_be_keyword_only() -> None:
    def select(source: gpd.GeoDataFrame, parameters: Parameters) -> None:
        pytest.fail(f"Executed operation: {source}, {parameters}")

    with pytest.raises(PluginDefinitionError, match="keyword-only"):
        location_operation(
            code_revision="1",
            name="select",
            description="Select",
            inputs={"source": LocationDependency(provider=FakeProvider.id)},
            target_provider=FakeProvider.id,
        )(select)
