"""Declaration identities and caller scope are available without acquisition."""

from unittest.mock import Mock, patch

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.dependencies import Dependency, Input, LocationDependency
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition, metric
from tests.bound_invocation_helpers import bound
from tests.node_declaration_helpers import CENSUS, Tracts


def test_nested_uses_preserve_caller_scope_and_fixed_targets() -> None:
    _, _, root = bound(["a", "b"])
    first, second = root.dependencies.values()
    assert first.identity != second.identity
    assert root.origins == frozenset({CENSUS})
    assert first.parameter_values() == {"year": 2020}
    assert second.parameter_values() == {"year": 2021}
    target = first.locations["places"]
    assert target.provider.provider_id == "counties"
    assert target.ids is None
    operation = target.upstream
    assert operation is not None
    assert operation.parameter_values() == {"year": 2025}
    assert operation.locations["source"] == root.locations["seed"]
    other = second.locations["places"].upstream
    assert other is not None
    assert operation.identity == other.identity


def test_unordered_selections_and_empty_provider_identity() -> None:
    _, _, first = bound(["a", "b"])
    _, _, permuted = bound(["b", "a"])
    _, _, changed = bound(["a"])
    _, _, empty = bound([])
    assert first.identity == permuted.identity
    assert first.identity != changed.identity
    assert empty.locations["seed"].ids == ()
    assert empty.locations["seed"].provider.provider_id == "tracts"
    assert empty.dependencies["first"].locations["places"].provider == (
        first.dependencies["first"].locations["places"].provider
    )
    assert empty.identity != changed.identity


def test_effective_values_are_owned_and_affect_identity() -> None:
    _, prepared, root = bound(["a"])
    prepared.parameters["year"] = 1
    exposed = root.parameter_values()
    exposed["year"] = 2
    assert root.parameter_values() == {"year": 2025}
    assert root.identity != bound(["a"], year=2026)[2].identity


def test_explicit_and_implicit_source_forwarding_are_equivalent() -> None:
    @metric(
        name="producer",
        code_revision="1",
        description="Producer",
        inputs={"seed": LocationDependency(provider="tracts", primary=True)},
        outputs={
            "value": pa.DataFrameSchema(
                {
                    "location_id": pa.Column(
                        str, description="Location ID", metadata={"unit": None}
                    ),
                    "row_key": pa.Column(
                        str, description="Row key", metadata={"unit": None}
                    ),
                },
                strict=True,
                unique=["location_id", "row_key"],
            )
        },
    )
    def producer(seed: gpd.GeoDataFrame) -> pd.DataFrame:
        del seed
        pytest.fail("handler executed")

    identities = []
    for explicit in (False, True):
        dependency = Dependency("value", producer="producer")
        if explicit:
            dependency = dependency.bind(seed=Input("seed"))

        @metric(
            name="consumer",
            code_revision="1",
            description="Consumer",
            inputs={"seed": LocationDependency(provider="tracts"), "table": dependency},
            outputs={"value": pa.DataFrameSchema(strict=True)},
        )
        def consumer(seed: gpd.GeoDataFrame, table: pd.DataFrame) -> pd.DataFrame:
            del seed, table
            pytest.fail("handler executed")

        catalog = compile_live_catalog(
            {
                "test": PluginDefinition(
                    metrics=[producer, consumer], resources={"tracts": Tracts}
                )
            }
        )
        prepared = prepare_execution(
            catalog,
            {
                "metric": "consumer",
                "inputs": {"seed": {"provider": "tracts", "ids": ["a"]}},
            },
        )
        root = bind_execution(catalog, prepared)
        assert root.dependencies["table"].primary_input == "seed"
        identities.append(root.identity)
    assert identities[0] == identities[1]


def test_executor_preflight_rejects_missing_parameter_use_before_acquisition() -> None:
    catalog, prepared, _ = bound(["a"])
    prepared.parameter_uses.pop(
        next(key for key in prepared.parameter_uses if key != "root")
    )
    context = Mock(side_effect=AssertionError("handler context acquired"))
    executor = GraphExecutor(catalog, context)
    with pytest.raises(ValueError, match="Missing normalized parameter use"):
        executor.validate_plan(prepared)
    context.assert_not_called()


def test_executor_rejects_changed_effective_binding() -> None:
    catalog, prepared, _ = bound(["a"])
    for key, values in prepared.parameter_uses.items():
        if "inputs.first" in key and values == {"year": 2020}:
            values["year"] = 1999
            break
    executor = GraphExecutor(catalog, Mock())
    with pytest.raises(ValueError, match="bindings disagree"):
        executor.validate_plan(prepared)


def test_resolved_targets_retain_upstream_computation_identity() -> None:
    catalog, _, root = bound([])
    producer = root.dependencies["first"]
    targets = producer.locations["places"]
    plugin = catalog.implementations[catalog.graph.nodes[producer.node].distribution]
    invocation = PreparedResolvedInvocation(
        producer.node,
        plugin.prepare_parameters(producer.node, producer.parameter_values()),
        {
            "places": NativeLocationInput(
                gpd.GeoDataFrame(
                    {"location_id": [], "geometry": []},
                    geometry="geometry",
                    crs="EPSG:4326",
                ),
                targets.provider,
            )
        },
        bound_use=producer,
    )
    executor = GraphExecutor(catalog, Mock())
    with patch.object(
        type(plugin),
        "invoke",
        return_value=plugin.normalize_result(producer.node, pd.DataFrame()),
    ) as invoke:
        executor.execute(invocation)
    effective = invoke.call_args.args[0]
    assert effective.identity == producer.identity
    assert effective.bound_use.locations["places"].upstream == targets.upstream
