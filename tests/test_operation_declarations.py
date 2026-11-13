"""Computational operation signatures and strict mapping contracts."""

from collections.abc import Callable
from typing import Annotated, cast
from unittest.mock import Mock

import geopandas as gpd
import pandera.pandas as pa
import pytest
from pydantic import BaseModel, Field, ValidationError

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.operations import OPERATION_MAPPING_ADAPTER
from lyra.runtime.planning.native_bindings import bind_native_definitions
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_operations import Places


class Parameters(MetricParameters):
    limit: int = Field(2, description="Maximum targets")


def declare(handler: Callable, *, cache: CachePolicy = "off") -> Callable:
    return location_operation(
        name="select",
        code_revision="1",
        description="Select targets",
        inputs={"sources": LocationDependency(provider=Places.id)},
        target_provider=Places.id,
        cache=cache,
    )(handler)


@pytest.mark.parametrize("cache", ["off", "full", "per_location"])
def test_positional_source_and_discovery(
    cache: CachePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquisition = Mock(side_effect=AssertionError("Unexpected acquisition"))
    monkeypatch.setattr(Places, "setup", acquisition)

    def resolve(self: Places, ids: list[str]) -> gpd.GeoDataFrame:
        acquisition(ids)
        raise AssertionError(self)

    monkeypatch.setattr(Places, "resolve", resolve)

    def blocks(
        records: Annotated[gpd.GeoDataFrame, Field(description="Source blocks")],
        /,
        *,
        parameters: Parameters,
    ) -> dict[str, tuple[str, ...]]:
        pytest.fail(f"Unexpected execution: {records}, {parameters}")

    declare(blocks, cache=cache)
    plugin = PluginDefinition(
        location_operations=[blocks], resources={"places": Places}
    )
    bound = bind_native_definitions({"test": plugin})["test"]
    declaration = bound.declarations[0]
    assert declaration.primary_input == "sources"
    assert declaration.scope == "location"
    assert declaration.cache == cache
    assert declaration.parameters is Parameters
    assert declaration.target == Places.id
    assert declaration.outputs == {}
    manifest = bound.describe("select").manifest
    assert manifest.kind == "location_operation"
    assert manifest.target_provider == Places.id
    assert manifest.primary_input == "sources"
    assert manifest.cache == cache
    assert not manifest.canonical
    assert manifest.location_inputs["sources"].description == "Source blocks"
    binding = LocationDependency("select").bind(sources=Input("main"))
    assert binding.locations == {"sources": Input("main")}
    acquisition.assert_not_called()


def missing() -> None:
    pass


def keyword(*, blocks: gpd.GeoDataFrame) -> None:
    pass


def wrong(blocks: object) -> None:
    pass


def candidates(blocks: gpd.GeoDataFrame, candidates: gpd.GeoDataFrame) -> None:
    pass


def positional(blocks: gpd.GeoDataFrame, parameters: Parameters) -> None:
    pass


def base(blocks: gpd.GeoDataFrame, *, parameters: BaseModel) -> None:
    pass


def nullable(blocks: gpd.GeoDataFrame, *, parameters: Parameters | None) -> None:
    pass


def context(blocks: gpd.GeoDataFrame, *, context: object) -> None:
    pass


def args(blocks: gpd.GeoDataFrame, *args: object) -> None:
    pass


def kwargs(blocks: gpd.GeoDataFrame, **kwargs: object) -> None:
    pass


def default(blocks: gpd.GeoDataFrame | None = None) -> None:
    pass


def parameter_default(
    blocks: gpd.GeoDataFrame, *, parameters: Parameters | None = None
) -> None:
    pass


@pytest.mark.parametrize(
    "handler",
    [
        missing,
        keyword,
        wrong,
        candidates,
        positional,
        base,
        nullable,
        context,
        args,
        kwargs,
        default,
        parameter_default,
    ],
)
def test_invalid_signatures(handler: Callable) -> None:
    with pytest.raises(PluginDefinitionError):
        declare(handler)


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"source": LocationDependency("other")},
        {"source": Dependency("data", producer="metric")},
        {
            "left": LocationDependency(provider=Places.id),
            "right": LocationDependency(provider=Places.id),
        },
    ],
)
def test_invalid_sources(inputs: dict) -> None:
    def blocks(table: gpd.GeoDataFrame) -> None:
        pytest.fail(str(table))

    with pytest.raises(PluginDefinitionError):
        location_operation(
            name="select",
            code_revision="1",
            description="Select",
            inputs=inputs,
            target_provider=Places.id,
        )(blocks)


@pytest.mark.parametrize("option", ["origins", "resources", "outputs", "target"])
def test_extra_capabilities_rejected(option: str) -> None:
    with pytest.raises(PluginDefinitionError):
        cast("Callable", location_operation)(
            name="select",
            code_revision="1",
            description="Select",
            inputs={"source": LocationDependency(provider=Places.id)},
            target_provider=Places.id,
            **{option: {}},
        )(lambda: None)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {1: ("a",)},
        {"a": (1,)},
        {"a": ["b"]},
        {"a": {"b"}},
        {"a": "b"},
        {b"a": ("b",)},
        {"a": (b"b",)},
    ],
)
def test_mapping_rejects_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        OPERATION_MAPPING_ADAPTER.validate_python(value)


def test_mapping_json_restoration_and_ownership() -> None:
    mapping = {"a": ("x", "y"), "b": ("y",), "c": ()}
    validated = OPERATION_MAPPING_ADAPTER.validate_python(mapping)
    assert validated == mapping
    validated["a"] = ()
    assert mapping["a"] == ("x", "y")
    assert (
        OPERATION_MAPPING_ADAPTER.validate_json(
            OPERATION_MAPPING_ADAPTER.dump_json(mapping)
        )
        == mapping
    )
    with pytest.raises(ValidationError):
        OPERATION_MAPPING_ADAPTER.validate_json('{"a": [1]}')


@pytest.mark.parametrize("override_target", [False, True])
def test_source_binding_keeps_fixed_target(*, override_target: bool) -> None:

    def blocks(table: gpd.GeoDataFrame) -> None:
        pytest.fail(str(table))

    declare(blocks)
    source = LocationDependency("select").bind(sources=Input("main"))
    if override_target:
        source = source.bind(target=Input("main"))

    @metric(
        name="consumer",
        code_revision="1",
        description="Consume selected locations",
        inputs={
            "main": LocationDependency(provider=Places.id),
            "selected": source,
        },
        outputs={"data": pa.DataFrameSchema({}, strict=True)},
    )
    def consumer(main: gpd.GeoDataFrame, selected: gpd.GeoDataFrame) -> None:
        pytest.fail(f"Unexpected execution: {main}, {selected}")

    plugin = PluginDefinition(
        metrics=[consumer], location_operations=[blocks], resources={"places": Places}
    )
    if override_target:
        with pytest.raises(ValueError, match="Unknown receiving location binding"):
            bind_native_definitions({"test": plugin})
        return
    bound = bind_native_definitions({"test": plugin})["test"]
    manifest = bound.describe("select").manifest
    assert manifest.kind == "location_operation"
    assert manifest.target_provider == Places.id
    assert manifest.result_schema == OPERATION_MAPPING_ADAPTER.json_schema()
    source.locations["target"] = Input("main")
    # Declaration capture is independent of later changes to the caller's binding.
    bind_native_definitions({"test": plugin})
