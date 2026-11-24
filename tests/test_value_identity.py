"""Declared value keys preserve effective inputs without inspecting contents."""

from dataclasses import replace

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope, RecordIdentity
from lyra.contracts.value_identity import ComputationIdentity, value_key
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.plugin import PluginDefinition, metric
from tests.live_graph_helpers import plugins
from tests.node_declaration_helpers import provider


def test_record_scope_and_kind() -> None:
    scope = ProviderScope(provider_id="places", origins=frozenset({Origin(id="data")}))
    record = RecordIdentity(scope=scope, location_id="a")
    assert record.key == value_key(record.identity_payload)
    assert record.key != RecordIdentity(scope=scope, location_id="b").key
    assert (
        record.key
        != RecordIdentity(
            scope=scope.model_copy(update={"code_revision": "2"}), location_id="a"
        ).key
    )
    assert (
        record.key
        == RecordIdentity(
            scope=ProviderScope(
                provider_id="places",
                origins=frozenset({Origin(id="data", description="Edited")}),
            ),
            location_id="a",
        ).key
    )
    assert record.key != value_key(record.identity_payload | {"kind": "metric"})


@pytest.mark.parametrize("ids", [[], ["a", "b"]])
@pytest.mark.parametrize("change", ["code", "origin"])
def test_target_scope_changes_operation_before_selection(
    ids: list[str], change: str
) -> None:
    keys = []
    for changed in (False, True):
        definitions = plugins()
        definitions["providers"] = PluginDefinition(
            resources={
                "tracts": provider(id="tracts"),
                "counties": provider(
                    id="counties",
                    code_revision="2" if changed and change == "code" else None,
                    origins=(
                        Origin(
                            id="data",
                            revision="2" if changed and change == "origin" else "1",
                        ),
                    ),
                ),
            }
        )
        catalog = compile_live_catalog(definitions)
        prepared = prepare_execution(
            catalog,
            {
                "metric": "consumer",
                "inputs": {"seed": {"provider": "tracts", "ids": ids}},
            },
        )
        root = bind_execution(catalog, prepared)
        operation = root.dependencies["first"].locations["places"].upstream
        assert operation is not None
        keys.append((root.identity, operation.identity))
    assert keys[0][0] != keys[1][0]
    assert keys[0][1] != keys[1][1]


def test_effective_parameter_keys_and_output_contract_independence() -> None:
    class Parameters(MetricParameters):
        value: str | int | bool | float | None = Field(
            default=None, description="Value"
        )
        order: list[str] = Field(default=["x", "y"], description="Ordered values")

    keys = []
    for policy in ("off", "full"):

        @metric(
            name="measure",
            description="Measure",
            cache=policy,
            outputs={"value": pa.DataFrameSchema(strict=True)},
        )
        def measure(parameters: Parameters) -> pd.DataFrame:
            del parameters
            pytest.fail("Identity construction invoked a handler")

        catalog = compile_live_catalog({"test": PluginDefinition(metrics=[measure])})

        def key(parameters: dict, catalog: CompiledCatalog = catalog) -> str:
            return bind_execution(
                catalog,
                prepare_execution(
                    catalog, {"metric": "measure", "parameters": parameters}
                ),
            ).identity

        assert key({}) == key({"value": None, "order": ["x", "y"]})
        assert key({}) != key({"order": ["y", "x"]})
        assert len({key({"value": value}) for value in ("1", 1, True, 1.0, None)}) == 5
        keys.append(key({}))
        node = catalog.graph.nodes["measure"].node
        assert node.kind == "metric"
        node.outputs["value"].meaning = "New schema metadata"
        assert key({}) == keys[-1]
        node.code_revision = "2"
        assert key({}) != keys[-1]
    assert keys[0] == keys[1]


def test_descriptor_roles_revisions_and_compact_upstream_keys() -> None:
    descriptor = ComputationIdentity(
        "metric", "node", None, "unpartitioned", {}, {"left": "a", "right": "b"}
    )
    assert descriptor.key != replace(descriptor, inputs={"left": "b", "right": "a"}).key
    assert descriptor.key == replace(descriptor, inputs={"right": "b", "left": "a"}).key
    assert descriptor.key != replace(descriptor, code_revision="2").key
    operation = ComputationIdentity(
        "operation",
        "lookup",
        None,
        "location",
        {},
        {"source": "a"},
        primary_input="source",
        target=ProviderScope(provider_id="places"),
    )
    assert operation.key != replace(operation, code_revision="selector-change").key
    assert "origins" not in operation.payload


@pytest.mark.parametrize(
    "value",
    [
        float("inf"),
        float("-inf"),
        float("nan"),
        object(),
        {1: "a"},
        (1, 2),
        b"bytes",
        {"nested": [float("nan")]},
    ],
)
def test_unsupported_identity_values_fail(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        value_key({"parameters": value})


def test_unsupported_identity_does_not_use_string_fallback() -> None:
    class Unsupported:
        def __repr__(self) -> str:
            message = "Identity must not inspect repr"
            raise AssertionError(message)

        def __str__(self) -> str:
            message = "Identity must not inspect str"
            raise AssertionError(message)

    with pytest.raises(ValueError, match="finite JSON value"):
        value_key({"parameters": [Unsupported()]})
