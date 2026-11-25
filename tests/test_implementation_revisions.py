"""Opaque optional revisions agree across declaration and execution boundaries."""

from typing import Any
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.dependencies import LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.origins import Origin
from lyra.contracts.plugins import PluginManifest
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.table_fixtures import value_schema
from tests.node_declaration_helpers import provider


def definitions(options: dict[str, Any], owner: str | None = None) -> PluginDefinition:
    @metric(
        name="root",
        description="Root",
        outputs={"result": value_schema()},
        inputs={"places": LocationDependency(provider="tracts")},
        **(options if owner in {None, "metric"} else {}),
    )
    def root(places: gpd.GeoDataFrame) -> pd.DataFrame:
        pytest.fail(f"Inspection executed metric: {places}")

    @location_operation(
        name="select",
        description="Select",
        inputs={"places": LocationDependency(provider="tracts")},
        target_provider="tracts",
        **(options if owner in {None, "operation"} else {}),
    )
    def select(places: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        pytest.fail(f"Inspection executed operation: {places}")

    return PluginDefinition(
        metrics=[root],
        location_operations=[select],
        resources={
            "places": provider(
                origins=(Origin(id="source"),),
                **(options if owner in {None, "provider"} else {}),
            )
        },
    )


@pytest.mark.parametrize("revision", [None, "2", "02", " revision / β \n", "x" * 300])
def test_exact_revision_survives_inspection_and_preparation(
    revision: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = definitions({"code_revision": revision})
    forbidden = Mock(side_effect=AssertionError("Inspection acquired resources"))
    for resource in plugin.resources.values():
        monkeypatch.setattr(resource, "__init__", forbidden)
        monkeypatch.setattr(resource, "setup", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    catalog = compile_live_catalog({"test": plugin})
    for declaration in plugin.declarations:
        assert declaration.code_revision == revision
    manifest = catalog.manifests["test"]
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert [node.code_revision for node in restored.nodes] == [revision, revision]
    for name in ("root", "select"):
        assert plugin.describe(name).manifest.code_revision == revision
    scope = Discovery(catalog).inspect("root").providers["tracts"].scope
    assert scope is not None
    assert scope.code_revision == revision
    assert scope.origins == frozenset({Origin(id="source", revision=None)})
    plan = prepare_execution(
        catalog,
        {"metric": "root", "inputs": {"places": {"provider": "tracts", "ids": []}}},
    )
    assert PreparedExecution.model_validate_json(plan.model_dump_json()) == plan
    forbidden.assert_not_called()


def test_omission_and_explicit_none_have_identical_declarations_and_plans() -> None:
    omitted = compile_live_catalog({"test": definitions({})})
    explicit = compile_live_catalog({"test": definitions({"code_revision": None})})
    assert omitted.manifests == explicit.manifests
    payload = explicit.manifests["test"].model_dump(mode="json")
    for node in payload["nodes"]:
        del node["code_revision"]
    assert PluginManifest.model_validate(payload) == omitted.manifests["test"]
    request = {
        "metric": "root",
        "inputs": {"places": {"provider": "tracts", "ids": []}},
    }
    assert prepare_execution(omitted, request) == prepare_execution(explicit, request)


@pytest.mark.parametrize("owner", ["metric", "operation", "provider"])
@pytest.mark.parametrize("revision", [1, True, "", " \t\n"])
def test_decorators_reject_invalid_revisions(revision: object, owner: str) -> None:
    with pytest.raises(PluginDefinitionError):
        compile_live_catalog({"test": definitions({"code_revision": revision}, owner)})


@pytest.mark.parametrize("owner", ["root", "select", "tracts"])
def test_exact_tokens_define_distinct_identities(owner: str) -> None:
    catalogs = []
    for token in ("2", "02", " 2 "):
        catalog = compile_live_catalog(
            {
                "test": definitions(
                    {"code_revision": token},
                    {"root": "metric", "select": "operation", "tracts": "provider"}[
                        owner
                    ],
                )
            }
        )
        catalogs.append(catalog)
    if owner == "tracts":
        assert (
            len(
                {
                    catalog.providers[owner].declaration.contract.provider_scope
                    for catalog in catalogs
                }
            )
            == 3
        )
    else:
        assert len({catalog.computation.nodes[owner] for catalog in catalogs}) == 3
