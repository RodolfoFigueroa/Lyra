"""Computation reuse follows semantic dependencies and captured versions."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import ValidationError

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.origins import Origin
from lyra.contracts.plugins import (
    LocationOperationManifest,
    MetricManifest,
)
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning import computation
from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from test_support.accepted_sources import routed_request
from test_support.location_helpers import node_contract
from test_support.routing_plugins import conversion, provider, routed_plugins


def definitions(
    calls: list[str],
    description: str = "Original",
    *,
    source_origins: tuple[Origin, ...] = (),
    root_origins: tuple[Origin, ...] = (),
    revised: str | None = None,
) -> dict[str, PluginDefinition]:
    schema = pa.DataFrameSchema(
        {"value": pa.Column(int, description=description, metadata={"unit": None})},
        strict=True,
    )

    @metric(
        cache="full",
        code_revision="2" if revised == "source" else "1",
        name="source",
        origins=source_origins,
        description=description,
        outputs={"value": schema},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        return pd.DataFrame({"value": [7]})

    @metric(
        cache="full",
        code_revision="2" if revised == "root" else "1",
        name="root",
        origins=root_origins,
        description=description,
        inputs={"data": Dependency("value", producer="source")},
        outputs={"value": schema},
    )
    def root(data: pd.DataFrame) -> pd.DataFrame:
        calls.append("root")
        return data

    @metric(
        cache="full",
        code_revision="2" if revised == "other" else "1",
        name="other",
        description=description,
        outputs={"value": schema},
    )
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
    old = LocalRunner.from_definitions(
        definitions(calls), result_store_path=tmp_path / "values.sqlite"
    )
    plan = prepare_execution(old.catalog, {"metric": "root"})
    old.run("root")
    assert calls == ["source", "root"]
    calls.clear()
    plugins = definitions(calls, "Revised prose")
    current = LocalRunner.from_definitions(
        plugins, result_store_path=tmp_path / "values.sqlite"
    )
    assert Discovery(current.catalog).fingerprint != Discovery(old.catalog).fingerprint
    assert (
        prepare_execution(current.catalog, {"metric": "root"}).computation_identity
        == plan.computation_identity
    )
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    executor = GraphExecutor(
        current.catalog,
        {name: BoundPlugin(plugin) for name, plugin in plugins.items()},
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    assert executor.execute(restored).outputs["value"].value.tolist() == [7]
    assert calls == []
    assert restored.description == "Original"


@pytest.mark.parametrize("distribution", ["source", "root", "other"])
def test_package_version_does_not_invalidate_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, distribution: str
) -> None:
    versions: dict[str, str] = {}
    read = Mock(side_effect=versions.get)
    monkeypatch.setattr(computation, "installed_version", read)
    calls: list[str] = []
    plugins = definitions(calls)
    old = LocalRunner.from_definitions(
        plugins, result_store_path=tmp_path / "values.sqlite"
    )
    plan = prepare_execution(old.catalog, {"metric": "root"})
    old.run("root")
    calls.clear()
    versions[distribution] = "2"
    current = LocalRunner.from_definitions(
        plugins, result_store_path=tmp_path / "values.sqlite"
    )
    assert current.catalog.computation.nodes == old.catalog.computation.nodes
    current.run("root")
    assert calls == []
    assert (
        prepare_execution(current.catalog, {"metric": "root"}).computation_identity
        == plan.computation_identity
    )


@pytest.mark.parametrize(
    "change",
    [
        "description",
        "column_description",
        "schema_prose",
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
    before = computation.executable_contract(node)
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
            "float64" if change == "dtype" else "m",
        )
    else:
        node.inputs["data"] = Dependency(
            producer="other", output=node.inputs["data"].output
        )
    after = computation.executable_contract(node)
    editorial = change in {
        "description",
        "column_description",
        "schema_prose",
        "visibility",
    }
    assert (before == after) == editorial


def test_unrelated_addition_removal_and_owned_identity() -> None:
    plugins = definitions([])
    catalog = compile_live_catalog(plugins)
    del plugins["other"]
    smaller = compile_live_catalog(plugins)
    assert smaller.computation.nodes["root"] == catalog.computation.nodes["root"]
    assert smaller.computation.plan("root", (), {}, {}) == catalog.computation.plan(
        "root", (), {}, {}
    )
    with pytest.raises(TypeError):
        cast("Any", catalog.computation.nodes)["root"] = "changed"


@pytest.mark.parametrize(
    "field",
    ["meaning", "observation_period", "release", "field_definitions", "data_revision"],
)
def test_location_semantics_and_revision_boundaries(field: str) -> None:
    contract = node_contract()
    before = computation.executable_contract(contract)
    setattr(
        contract,
        field,
        {"id": "Changed interpretation"} if field == "field_definitions" else "changed",
    )
    assert (before == computation.executable_contract(contract)) == (
        field == "data_revision"
    )


def test_provider_scope_changes_only_explicit_consumers() -> None:
    plugins = routed_plugins()

    def compile_with_unused(revision: str) -> CompiledCatalog:
        unused = provider("unused")
        unused.code_revision = revision
        return compile_live_catalog(
            plugins | {"unused": PluginDefinition(resources={"unused": unused})}
        )

    before = compile_with_unused("1")
    after = compile_with_unused("2")
    _, request = routed_request()
    assert (
        prepare_execution(before, request).computation_identity
        == prepare_execution(after, request).computation_identity
    )
    assert before.computation.nodes == after.computation.nodes
    source = provider("source")
    source.code_revision = "2"
    plugins["routes"] = PluginDefinition(
        location_operations=[
            conversion("select_0", "source", "native"),
            conversion("select_1", "other", "native"),
        ],
        resources={
            "source": source,
            "native": provider("native"),
            "other": provider("other"),
        },
    )
    changed = compile_with_unused("2")
    assert (
        prepare_execution(changed, request).computation_identity
        != prepare_execution(before, request).computation_identity
    )


@pytest.mark.parametrize("node", ["source", "root", "other"])
def test_author_revision_changes_only_dependent_computations(node: str) -> None:
    before = compile_live_catalog(definitions([]))
    after = compile_live_catalog(definitions([], revised=node))
    assert before.computation.nodes[node] != after.computation.nodes[node]
    assert (before.computation.nodes["root"] == after.computation.nodes["root"]) == (
        node == "other"
    )


@pytest.mark.parametrize("kind", [MetricManifest, LocationOperationManifest])
@pytest.mark.parametrize("revision", ["", " ", 1, True])
def test_invalid_author_revision(kind: type, revision: object) -> None:
    with pytest.raises(ValidationError, match="code_revision"):
        kind(name="test", description="Test", code_revision=revision)


def test_deployment_revision_preserves_persistent_cache(tmp_path: Path) -> None:
    calls: list[str] = []
    plugins = definitions(calls)
    for revision in ("build-1", "build-2"):
        catalog = compile_live_catalog(plugins, build_revision=revision)
        executor = GraphExecutor(
            catalog,
            dict(catalog.implementations),
            Mock(),
            store=SQLiteStore(tmp_path / "values.sqlite"),
        )
        result = executor.execute(prepare_execution(catalog, {"metric": "root"}))
        assert result.outputs["value"].value.tolist() == [7]
    assert calls == ["source", "root"]


def test_origin_metadata_and_duplicate_order_preserve_cached_execution(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    census = Origin(id="census", revision="2025", description="Original")
    elevation = Origin(id="elevation")
    original = LocalRunner.from_definitions(
        definitions(calls, source_origins=(census, elevation)),
        result_store_path=tmp_path / "values.sqlite",
    )
    plan = prepare_execution(original.catalog, {"metric": "root"})
    original.run("root")
    calls.clear()
    current = LocalRunner.from_definitions(
        definitions(
            calls,
            source_origins=(
                elevation,
                Origin(id="census", revision="2025", description="Updated"),
                census,
            ),
        ),
        result_store_path=tmp_path / "values.sqlite",
    )
    restored = PreparedExecution.model_validate_json(plan.model_dump_json())
    executor = GraphExecutor(
        current.catalog,
        dict(current.catalog.implementations),
        Mock(),
        store=SQLiteStore(tmp_path / "values.sqlite"),
    )
    assert executor.execute(restored).outputs["value"].value.tolist() == [7]
    assert calls == []


@pytest.mark.parametrize("owner", ["source", "root"])
@pytest.mark.parametrize(
    "changed",
    [
        (),
        (Origin(id="census", revision="2026"),),
        (Origin(id="different", revision="2025"),),
        (Origin(id="census"),),
        (Origin(id="census", revision="2025"), Origin(id="elevation")),
    ],
)
def test_direct_origin_changes_invalidate_only_dependent_cached_nodes(
    tmp_path: Path, owner: str, changed: tuple[Origin, ...]
) -> None:
    calls: list[str] = []
    census = (Origin(id="census", revision="2025"),)
    original = LocalRunner.from_definitions(
        definitions(calls, source_origins=census, root_origins=census),
        result_store_path=tmp_path / "values.sqlite",
    )
    original.run("root")
    original.run("other")
    plan = prepare_execution(original.catalog, {"metric": "root"})
    calls.clear()
    current = LocalRunner.from_definitions(
        definitions(
            calls,
            source_origins=changed if owner == "source" else census,
            root_origins=changed if owner == "root" else census,
        ),
        result_store_path=tmp_path / "values.sqlite",
    )
    # Even when an upstream node declares the same origin, the consumer's own
    # acquisition declaration participates independently in its computation.
    executor = GraphExecutor(
        current.catalog, dict(current.catalog.implementations), Mock()
    )
    with pytest.raises(GraphExecutionError, match="Computation identity differs"):
        executor.execute(PreparedExecution.model_validate_json(plan.model_dump_json()))
    assert calls == []
    current.run("root")
    current.run("other")
    assert calls == (["source", "root"] if owner == "source" else ["root"])


def test_origin_projection_is_canonical_and_keeps_unversioned_distinct() -> None:
    origins = (
        Origin(id="same", revision=""),
        Origin(id="same"),
        Origin(id="other", revision="2"),
    )
    catalog = compile_live_catalog(definitions([], root_origins=origins))
    node = catalog.graph.nodes["root"].node
    projected = computation.executable_contract(node)
    assert isinstance(projected, dict)
    assert projected["origins"] == [
        {"id": "other", "revision": "2"},
        {"id": "same", "revision": ""},
        {"id": "same", "revision": None},
    ]
