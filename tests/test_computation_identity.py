"""Computation reuse follows semantic dependencies and captured versions."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.bound_plan import deserialize_bound_plan, serialize_bound_plan
from lyra.contracts.plugins import (
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    PluginInfo,
    PluginManifest,
)
from lyra.contracts.providers import LocationCollection, LocationProviderManifest
from lyra.contracts.units import Unit
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning import computation
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.location_helpers import node_contract


def definitions(
    calls: list[str], description: str = "Original"
) -> dict[str, PluginDefinition]:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description=description, metadata={"unit": None})}
    )

    @metric(name="source", description=description, outputs={"value": schema})
    def source() -> pd.DataFrame:
        calls.append("source")
        return pd.DataFrame({"value": [7]})

    @metric(
        name="root",
        description=description,
        inputs={"data": Dependency("value", producer="source")},
        outputs={"value": schema},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return data

    @metric(name="other", description=description, outputs={"value": schema})
    def other() -> pd.DataFrame:
        calls.append("other")
        return pd.DataFrame({"value": [9]})

    return {
        "root": PluginDefinition(metrics=[root]),
        "source": PluginDefinition(metrics=[source]),
        "other": PluginDefinition(metrics=[other]),
    }


def test_editorial_changes_reuse_cache_and_accepted_execution(tmp_path: Path) -> None:
    calls: list[str] = []
    old = LocalRunner.from_definitions(definitions(calls), artifact_dir=tmp_path)
    plan = prepare_execution(old.catalog, {"metric": "root"})
    old.run("root", {})
    assert calls == ["source", "root"]
    calls.clear()
    plugins = definitions(calls, "Revised prose")
    current = LocalRunner.from_definitions(plugins, artifact_dir=tmp_path)
    assert Discovery(current.catalog).fingerprint != Discovery(old.catalog).fingerprint
    assert (
        prepare_execution(current.catalog, {"metric": "root"}).computation_identity
        == plan.computation_identity
    )
    restored = deserialize_bound_plan(serialize_bound_plan(plan))
    executor = GraphExecutor(
        current.catalog,
        {name: BoundPlugin(plugin) for name, plugin in plugins.items()},
        Mock(),
        cache=TableCache(tmp_path),
    )
    assert executor.execute(restored).outputs["value"].value.tolist() == [7]
    assert calls == []
    assert restored.contracts.nodes["root"].description == "Original"


@pytest.mark.parametrize("distribution", ["source", "root", "other"])
def test_version_change_is_scoped_and_captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, distribution: str
) -> None:
    versions: dict[str, str] = {}
    read = Mock(side_effect=versions.get)
    monkeypatch.setattr(computation, "installed_version", read)
    calls: list[str] = []
    plugins = definitions(calls)
    old = LocalRunner.from_definitions(plugins, artifact_dir=tmp_path)
    plan = prepare_execution(old.catalog, {"metric": "root"})
    old.run("root", {})
    calls.clear()
    assert read.call_count == 3
    versions[distribution] = "2"
    old.run("root", {})
    assert calls == []
    assert read.call_count == 3
    current = LocalRunner.from_definitions(plugins, artifact_dir=tmp_path)
    executor = GraphExecutor(
        current.catalog,
        {name: BoundPlugin(plugin) for name, plugin in plugins.items()},
        Mock(),
        cache=TableCache(tmp_path),
    )
    if distribution == "other":
        executor.execute(plan)
        assert calls == []
    else:
        with pytest.raises(GraphExecutionError, match="identity differs"):
            executor.execute(plan)
        assert calls == []
        current.run("root", {})
        assert calls == (["source", "root"] if distribution == "source" else ["root"])
    assert read.call_count == 6


@pytest.mark.parametrize(
    "change",
    [
        "description",
        "column_description",
        "schema_prose",
        "edition_metadata",
        "visibility",
        "literal_default",
        "constraint",
        "meaning",
        "granularity",
        "dtype",
        "unit",
        "dependency",
    ],
)
def test_contract_projection_distinguishes_prose_from_semantics(change: str) -> None:
    catalog = LocalRunner.from_definitions(definitions([])).catalog
    manifests = deepcopy(dict(catalog.manifests))
    node = manifests["root"].nodes[0]
    assert isinstance(node, MetricManifest)
    node.parameters_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "options": {
                "type": "object",
                "default": {"description": "literal", "title": "literal"},
            }
        },
    }
    before = compile_catalog(manifests)
    if change == "description":
        node.description = "Edited"
    elif change == "column_description":
        node.outputs["value"].columns[0].description = "Edited"
    elif change == "schema_prose":
        node.parameters_schema.update(
            title="Edited", description="Edited", examples=[{}]
        )
        node.parameters_schema["properties"]["options"].update(
            title="Edited", examples=[{"a": 1}], **{"$comment": "Edited"}
        )
    elif change == "edition_metadata":
        node.edition_metadata = {"description": "Edited"}
    elif change == "visibility":
        node.private = True
    elif change == "literal_default":
        node.parameters_schema["properties"]["options"]["default"]["description"] = (
            "changed data"
        )
    elif change == "constraint":
        node.parameters_schema["properties"]["options"]["maxProperties"] = 2
    elif change in {"meaning", "granularity"}:
        setattr(node.outputs["value"], change, "Different interpretation")
    elif change in {"dtype", "unit"}:
        setattr(
            node.outputs["value"].columns[0],
            change,
            "float64" if change == "dtype" else Unit.METRE,
        )
    else:
        node.inputs["data"].producer = "other"
    after = compile_catalog(manifests)
    editorial = change in {
        "description",
        "column_description",
        "schema_prose",
        "edition_metadata",
        "visibility",
    }
    assert (
        before.computation.nodes["root"] == after.computation.nodes["root"]
    ) == editorial
    assert before.computation.nodes["source"] == after.computation.nodes["source"]


def test_unrelated_addition_removal_and_owned_identity() -> None:
    catalog = LocalRunner.from_definitions(definitions([])).catalog
    manifests = deepcopy(dict(catalog.manifests))
    del manifests["other"]
    smaller = compile_catalog(manifests)
    assert smaller.computation.nodes["root"] == catalog.computation.nodes["root"]
    assert smaller.computation.plan("root", ()) == catalog.computation.plan("root", ())
    with pytest.raises(TypeError):
        cast("Any", catalog.computation.nodes)["root"] = "changed"
    source = manifests["source"].nodes[0]
    assert source.kind == "metric"
    source.outputs["value"].meaning = "changed"
    assert smaller.computation.nodes["root"] == catalog.computation.nodes["root"]
    assert (
        compile_catalog(manifests).computation.nodes["root"]
        != smaller.computation.nodes["root"]
    )


@pytest.mark.parametrize(
    "field",
    ["meaning", "observation_period", "release", "field_definitions", "data_revision"],
)
def test_location_semantics_and_revision_boundaries(field: str) -> None:
    manifests = deepcopy(
        dict(LocalRunner.from_definitions(definitions([])).catalog.manifests)
    )
    source = manifests["source"].nodes[0]
    contract = node_contract()
    source.location_inputs = {"places": LocationInputDeclaration(contract=contract)}
    manifests["root"].nodes[0].location_inputs = source.location_inputs.copy()
    before = compile_catalog(manifests)
    setattr(
        contract,
        field,
        {"id": "Changed interpretation"} if field == "field_definitions" else "changed",
    )
    after = compile_catalog(manifests)
    assert (before.computation.nodes["root"] == after.computation.nodes["root"]) == (
        field == "data_revision"
    )
    assert (
        before.computation.cache_nodes["root"] != after.computation.cache_nodes["root"]
    )
    assert (
        before.computation.cache_nodes["other"]
        == after.computation.cache_nodes["other"]
    )


def test_provider_scope_and_dynamic_operation_identity() -> None:
    first = node_contract()
    second = node_contract("test:other", "unit")
    provider = LocationProviderManifest(
        resource="locations",
        collections=tuple(
            LocationCollection(
                name=c.family.collection,
                unit_types=(c.family.unit_type,),
                crs="EPSG:4326",
            )
            for c in (first, second)
        ),
        contracts=(first, second),
    )
    manifests = deepcopy(
        dict(LocalRunner.from_definitions(definitions([])).catalog.manifests)
    )
    manifests["provider"] = PluginManifest(
        plugin=PluginInfo(name="provider", version="1"),
        factory="test:create",
        resources={"locations": {}},
        location_provider=provider,
    )
    # A plain metric pins only its explicit source scope.
    root = manifests["root"].nodes[0]
    root.inputs.clear()
    root.location_inputs = {"places": LocationInputDeclaration(contract=first)}
    operation = LocationOperationManifest(
        name="lookup",
        description="Lookup",
        location_inputs={"places": LocationInputDeclaration(contract=first)},
        output_contract=first,
    )
    manifests["operations"] = PluginManifest(
        plugin=PluginInfo(name="operations", version="1"),
        factory="test:create",
        nodes=[operation],
    )
    before = compile_catalog(manifests)
    plan = prepare_execution(
        before,
        {
            "metric": "root",
            "inputs": {
                "places": {
                    "collection": first.family.collection,
                    "unit_type": first.family.unit_type,
                    "ids": ["a"],
                }
            },
        },
    )
    second.meaning = "Changed unrelated scope"
    after = compile_catalog(manifests)
    assert after.computation.plan(plan.node, plan.inputs) == plan.computation_identity
    assert after.computation.nodes["root"] == before.computation.nodes["root"]
    assert after.computation.nodes["lookup"] != before.computation.nodes["lookup"]
    first.meaning = "Changed selected scope"
    changed = compile_catalog(manifests)
    assert changed.computation.plan(plan.node, plan.inputs) != plan.computation_identity
