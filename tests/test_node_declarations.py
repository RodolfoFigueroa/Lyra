from typing import Annotated, cast
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from geopandas import GeoDataFrame
from pydantic import ValidationError

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.planning.node_declarations import (
    NodeDeclaration,
    compile_node_declarations,
)
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from tests.node_declaration_helpers import (
    CENSUS,
    ELEVATION,
    Tracts,
    declarations,
    mixed,
    plugin,
    provider,
    source,
)


def test_combined_live_catalog_preserves_records_bindings_and_direct_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = Tracts
    forbidden = Mock(side_effect=AssertionError("acquisition"))
    monkeypatch.setattr(declared, "__init__", forbidden)
    monkeypatch.setattr(
        declared, "resolve", create_autospec(declared.resolve, side_effect=forbidden)
    )
    raw = declarations()
    compiled = compile_node_declarations(
        compile_provider_registry({"providers": plugin(alias=declared)}), raw
    )
    consumer = compiled["consumer"]
    assert consumer.locations["tracts"].resource == "alias"
    assert isinstance(declared.schema, pa.DataFrameSchema)
    assert set(consumer.locations["tracts"].schema.schema.columns) == set(
        declared.schema.columns
    )
    assert consumer.origins == frozenset({CENSUS, ELEVATION})
    assert compiled["select"].origins == frozenset()
    assert consumer.locations["tracts"].scope.origins == frozenset({CENSUS})
    dependency = consumer.declaration.inputs["table"]
    assert isinstance(dependency, Dependency)
    nested = dependency.locations["tracts"]
    assert isinstance(nested, LocationDependency)
    assert nested.locations == {"region": Input("tracts")}
    assert nested.parameters == {"year": Input("parameters.year")}
    tracts = consumer.declaration.inputs["tracts"]
    assert isinstance(tracts, LocationDependency)
    assert tracts.primary
    raw_dependency = raw[-1].inputs["table"]
    raw_dependency.locations.clear()
    assert dependency.locations
    forbidden.assert_not_called()


@pytest.mark.parametrize("provider_id", ["tracts", Tracts.id])
def test_provider_and_operation_forms_derive_same_complete_schema(
    provider_id: str,
) -> None:
    raw = declarations()[:2]

    # Use a separate source name to preserve caller scope for operation invocation.
    def consume(seed: gpd.GeoDataFrame, result: gpd.GeoDataFrame) -> None:
        pass

    derived = NodeDeclaration(
        "derived",
        consume,
        inputs={
            "seed": LocationDependency(provider=provider_id),
            "result": LocationDependency(
                operation="select", parameters={"year": 2025}
            ).bind(
                region=LocationDependency(
                    operation="select", parameters={"year": 2024}
                ).bind(region=Input("seed"))
            ),
        },
    )
    compiled = compile_node_declarations(
        compile_provider_registry({"provider": plugin(binding=provider())}),
        [*raw, derived],
    )
    assert compiled["derived"].locations["result"].scope.provider_id == provider_id
    assert (
        compiled["derived"].locations["result"].schema
        is compiled["derived"].locations["seed"].schema
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"provider": "tracts", "operation": "select"},
        {"provider": ""},
        {"provider": 1},
        {"provider": "tracts", "parameters": {"x": 1}},
    ],
)
def test_invalid_sources(kwargs: dict) -> None:
    with pytest.raises((ValueError, TypeError)):
        LocationDependency(**kwargs)


def test_provider_binding_rejected() -> None:
    with pytest.raises(ValueError, match="bindings"):
        LocationDependency(provider="tracts").bind(region=Input("seed"))


@pytest.mark.parametrize("target", [True, False])
def test_unknown_provider_or_target(*, target: bool) -> None:
    raw = NodeDeclaration(
        "node",
        source,
        kind="location_operation" if target else "metric",
        target="missing" if target else None,
        inputs={
            "tracts": LocationDependency(provider="tracts" if target else "missing")
        },
    )
    with pytest.raises(ValueError, match="Unknown provider"):
        compile_node_declarations(
            compile_provider_registry({"provider": plugin(binding=provider())}), [raw]
        )


def resource_operation(
    tracts: gpd.GeoDataFrame, db: Annotated[BaseIO, Resource("db")]
) -> None:
    pass


def extra_operation(tracts: gpd.GeoDataFrame, extra: GeoDataFrame) -> None:
    pass


@pytest.mark.parametrize(
    "capability", ["table", "extra", "resource", "origins", "zero", "undeclared"]
)
def test_operation_restrictions(capability: str) -> None:
    inputs: dict[str, Dependency | LocationDependency] = {
        "tracts": LocationDependency(provider="tracts")
    }
    handler = source
    if capability == "table":
        inputs["table"] = Dependency("value", producer="upstream")
        handler = mixed
    if capability == "extra":
        inputs["table"] = LocationDependency(provider="tracts")
        handler = mixed
    if capability == "resource":
        handler = resource_operation
    if capability == "undeclared":
        handler = extra_operation
    if capability == "zero":
        inputs.clear()
    raw = NodeDeclaration(
        "invalid",
        handler,
        kind="location_operation",
        target="tracts",
        inputs=inputs,
        origins=(CENSUS,) if capability == "origins" else (),
    )
    with pytest.raises((ValueError, TypeError)):
        compile_node_declarations(
            compile_provider_registry({"provider": plugin(binding=provider())}), [raw]
        )


@pytest.mark.parametrize("origins", [(), (CENSUS,), (ELEVATION, CENSUS, CENSUS)])
def test_metric_decorator_normalizes_own_origins(origins: tuple[Origin, ...]) -> None:
    @metric(
        name="origins_metric",
        code_revision="1",
        description="Direct dependencies.",
        origins=origins,
        outputs={
            "value": pa.DataFrameSchema(
                strict=True,
            )
        },
    )
    def handler() -> pd.DataFrame:
        return pd.DataFrame()

    manifest = PluginDefinition(metrics=[handler]).node_manifests[0]
    assert manifest.kind == "metric"
    assert manifest.origins == frozenset(origins)


def test_invalid_direct_origins() -> None:
    with pytest.raises(ValidationError):
        compile_node_declarations(
            compile_provider_registry({}),
            [
                NodeDeclaration(
                    "invalid",
                    lambda: None,
                    origins=cast("tuple[Origin, ...]", ("bad",)),
                )
            ],
        )


def test_metric_direct_query_remains_distinct_from_provider_origin() -> None:
    raw = NodeDeclaration(
        "query",
        resource_operation,
        inputs={"tracts": LocationDependency(provider=Tracts.id)},
        origins=(CENSUS,),
    )
    compiled = compile_node_declarations(
        compile_provider_registry(
            {"providers": plugin(binding=Tracts), "database": plugin(db=BaseIO)}
        ),
        [raw],
    )["query"]
    assert compiled.origins == compiled.locations["tracts"].scope.origins
    assert compiled.origins == frozenset({CENSUS})
    assert compiled.declaration.function is resource_operation


@pytest.mark.parametrize("operation_name", ["missing", "population"])
def test_unknown_or_nonoperation_dependency(operation_name: str) -> None:
    raw = declarations()[:1]
    raw.append(
        NodeDeclaration(
            "derived",
            source,
            inputs={"tracts": LocationDependency(operation=operation_name)},
        )
    )
    with pytest.raises(
        ValueError, match=r"Unknown dependency|Expected location operation"
    ):
        compile_node_declarations(
            compile_provider_registry({"providers": plugin(binding=Tracts)}), raw
        )


def test_matching_names_supply_metric_location_bindings() -> None:
    def consume(tracts: gpd.GeoDataFrame, table: pd.DataFrame) -> None:
        pass

    raw = declarations()[:1]
    raw.append(
        NodeDeclaration(
            "consumer",
            consume,
            inputs={
                "tracts": LocationDependency(provider=Tracts.id),
                "table": Dependency("population", producer="population"),
            },
        )
    )
    result = compile_node_declarations(
        compile_provider_registry({"providers": plugin(binding=Tracts)}), raw
    )
    assert result["consumer"].locations["tracts"].scope.provider_id == Tracts.id


def test_provider_binding_can_be_routed() -> None:
    raw = declarations()
    raw[-1].inputs["table"].locations["tracts"] = LocationDependency(
        provider="counties"
    )
    compiled = compile_node_declarations(
        compile_provider_registry(
            {"providers": plugin(binding=Tracts, other=provider(id="counties"))}
        ),
        raw,
    )
    binding = compiled["consumer"].declaration.inputs["table"].locations["tracts"]
    assert isinstance(binding, LocationDependency)
    assert binding.provider == "counties"


def test_mutated_provider_invocation_arguments_are_revalidated() -> None:
    dependency = LocationDependency(provider=Tracts.id)
    dependency.parameters["unexpected"] = 1
    with pytest.raises(ValueError, match="invocation bindings"):
        compile_node_declarations(
            compile_provider_registry({"providers": plugin(binding=Tracts)}),
            [NodeDeclaration("source", source, inputs={"tracts": dependency})],
        )


@pytest.mark.parametrize("nested", [False, True])
def test_derived_location_input_cycles_fail_before_acquisition(*, nested: bool) -> None:
    def consume(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> None:
        pass

    right = LocationDependency("select", parameters={"year": 2025}).bind(
        region=Input("left")
    )
    if nested:
        right = LocationDependency("select", parameters={"year": 2025}).bind(
            region=right
        )
    consumer = NodeDeclaration(
        "cycle",
        consume,
        inputs={
            "left": LocationDependency("select", parameters={"year": 2025}).bind(
                region=Input("right")
            ),
            "right": right,
        },
    )
    with pytest.raises(ValueError, match="Location input cycle"):
        compile_node_declarations(
            compile_provider_registry({"providers": plugin(tracts=Tracts)}),
            [*declarations()[:2], consumer],
        )
