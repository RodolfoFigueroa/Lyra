"""Cache declarations are validated and captured before resource acquisition."""

import json
from typing import Literal, cast
from unittest.mock import Mock

import geopandas as gpd
import pandera.pandas as pa
import pytest
from pydantic import ValidationError

from lyra.contracts.dependencies import LocationDependency
from lyra.contracts.errors import PluginDefinitionError
from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.plugins import LocationOperationManifest, MetricManifest
from lyra.contracts.provider_scope import RecordIdentity
from lyra.contracts.providers import ProviderDeclaration
from lyra.runtime.planning.node_declarations import (
    NodeDeclaration,
    compile_node_declarations,
)
from lyra.runtime.planning.provider_registry import (
    capture_provider,
    compile_provider_registry,
)
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from test_support.location_schema import column
from tests.node_declaration_helpers import Tracts, provider

INVALID_NODE_POLICIES = [True, False, None, 0, 1, "FULL", "per-location", [], {}]
INVALID_PROVIDER_POLICIES = [None, 0, 1, "off", "full", "per_location", "true", [], {}]


def identity_schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {"location_id": column(str), "row_key": column(str)},
        strict=True,
        unique=["location_id", "row_key"],
    )


def source(main: gpd.GeoDataFrame) -> None:
    pass


@pytest.mark.parametrize("cache", [None, False, True])
def test_provider_policy_capture_and_identity(
    *, cache: bool | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("Resource acquired"))
    monkeypatch.setattr(Tracts, "__init__", forbidden)
    monkeypatch.setattr(Tracts, "setup", forbidden)
    cls = provider(**({} if cache is None else {"cache": cache}))
    captured = capture_provider("places", cls)
    assert captured.cache is (False if cache is None else cache)
    declaration = captured.declaration
    assert declaration.cache is captured.cache
    assert (
        ProviderDeclaration.model_validate_json(declaration.model_dump_json())
        == declaration
    )
    assert PluginDefinition(resources={"places": cls}).providers == (declaration,)
    cls.cache = not captured.cache
    assert captured.declaration.cache is captured.cache
    changed = capture_provider("places", cls)
    assert (
        RecordIdentity(scope=captured.scope, location_id="a").key
        == RecordIdentity(scope=changed.scope, location_id="a").key
    )
    forbidden.assert_not_called()


@pytest.mark.parametrize("cache", INVALID_PROVIDER_POLICIES)
def test_invalid_provider_policy_before_acquisition(
    cache: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("Resource acquired"))
    monkeypatch.setattr(Tracts, "__init__", forbidden)
    monkeypatch.setattr(Tracts, "setup", forbidden)
    with pytest.raises(PluginDefinitionError, match="bool"):
        _ = PluginDefinition(resources={"places": provider(cache=cache)}).providers
    payload = capture_provider("places", Tracts).declaration.model_dump(mode="json")
    payload["cache"] = cache
    with pytest.raises(ValidationError, match="cache"):
        ProviderDeclaration.model_validate_json(json.dumps(payload))
    forbidden.assert_not_called()


@pytest.mark.parametrize("cache", [None, "off", "full", "per_location"])
def test_operation_policy_capture_and_roundtrip(cache: CachePolicy | None) -> None:
    def handler(main: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        del main
        return {}

    options: dict[Literal["cache"], CachePolicy] = (
        {} if cache is None else {"cache": cache}
    )
    location_operation(
        name="select",
        description="Select",
        inputs={"main": LocationDependency(provider="tracts")},
        target_provider="tracts",
        **options,
    )(handler)
    plugin = PluginDefinition(
        location_operations=[handler], resources={"places": Tracts}
    )
    manifest = plugin.describe("select").manifest
    assert isinstance(manifest, LocationOperationManifest)
    assert manifest.cache == (cache or "off")
    assert manifest.scope == "location"
    assert manifest.primary_input == "main"
    assert (
        LocationOperationManifest.model_validate_json(manifest.model_dump_json())
        == manifest
    )


@pytest.mark.parametrize("cache", INVALID_NODE_POLICIES)
def test_invalid_operation_decorator_policy(cache: object) -> None:
    with pytest.raises(PluginDefinitionError, match="cache must"):
        location_operation(
            name="select",
            description="Select",
            inputs={"main": LocationDependency(provider="tracts")},
            target_provider="tracts",
            cache=cast("CachePolicy", cache),
        )(source)


@pytest.mark.parametrize("kind", ["metric", "location_operation"])
@pytest.mark.parametrize("cache", INVALID_NODE_POLICIES)
def test_captured_node_policies_revalidated(
    kind: Literal["metric", "location_operation"],
    cache: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("Resource acquired"))
    monkeypatch.setattr(Tracts, "__init__", forbidden)
    monkeypatch.setattr(Tracts, "setup", forbidden)
    declaration = NodeDeclaration(
        "measure",
        source,
        inputs={"main": LocationDependency(provider="tracts")},
        kind=kind,
        target="tracts" if kind == "location_operation" else None,
        cache=cast("CachePolicy", cache),
    )
    with pytest.raises(ValueError, match="cache must"):
        compile_node_declarations(
            compile_provider_registry(
                {"test": PluginDefinition(resources={"places": Tracts})}
            ),
            [declaration],
        )
    forbidden.assert_not_called()


def test_captured_operation_owns_source_without_marker() -> None:
    declaration = NodeDeclaration(
        "select",
        source,
        inputs={"main": LocationDependency(provider="tracts")},
        kind="location_operation",
        target="tracts",
        cache="per_location",
    )
    compiled = compile_node_declarations(
        compile_provider_registry(
            {"test": PluginDefinition(resources={"places": Tracts})}
        ),
        [declaration],
    )["select"].declaration
    assert compiled.primary_input == "main"
    assert compiled.scope == "location"


@pytest.mark.parametrize("cache", [*INVALID_NODE_POLICIES, "per_location"])
def test_serialized_metric_rejects_invalid_policy_or_scope(cache: object) -> None:
    def handler() -> None:
        pass

    metric(
        name="summary",
        description="Summary",
        outputs={"data": pa.DataFrameSchema(strict=True)},
    )(handler)
    manifest = PluginDefinition(metrics=[handler]).describe("summary").manifest
    payload = manifest.model_dump(mode="json") | {"cache": cache}
    with pytest.raises(ValidationError):
        MetricManifest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("cache", ["off", "full", "per_location"])
def test_metric_metadata_roundtrip(cache: CachePolicy) -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    metric(
        name="measure",
        description="Measure",
        inputs={"main": LocationDependency(provider="tracts", primary=True)},
        outputs={"data": identity_schema()},
        cache=cache,
    )(handler)
    manifest = (
        PluginDefinition(metrics=[handler], resources={"places": Tracts})
        .describe("measure")
        .manifest
    )
    assert MetricManifest.model_validate_json(manifest.model_dump_json()) == manifest


@pytest.mark.parametrize("cache", INVALID_NODE_POLICIES)
def test_serialized_operation_rejects_invalid_policy(cache: object) -> None:
    def handler(main: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        del main
        return {}

    location_operation(
        name="select",
        description="Select",
        inputs={"main": LocationDependency(provider="tracts")},
        target_provider="tracts",
    )(handler)
    manifest = (
        PluginDefinition(location_operations=[handler], resources={"places": Tracts})
        .describe("select")
        .manifest
    )
    payload = manifest.model_dump(mode="json") | {"cache": cache}
    with pytest.raises(ValidationError):
        LocationOperationManifest.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        LocationOperationManifest.model_validate(
            manifest.model_copy(update={"cache": cache})
        )


@pytest.mark.parametrize("primary", [False, True])
def test_serialized_metric_cannot_override_ownership(*, primary: bool) -> None:
    def handler(main: gpd.GeoDataFrame) -> None:
        pass

    metric(
        name="measure",
        description="Measure",
        inputs={"main": LocationDependency(provider="tracts", primary=primary)},
        outputs={
            "data": identity_schema() if primary else pa.DataFrameSchema(strict=True)
        },
    )(handler)
    manifest = (
        PluginDefinition(metrics=[handler], resources={"places": Tracts})
        .describe("measure")
        .manifest
    )
    payload = manifest.model_dump(mode="json") | {
        "scope": "unpartitioned" if primary else "location",
        "primary_input": None if primary else "main",
    }
    with pytest.raises(ValidationError, match="ownership"):
        MetricManifest.model_validate_json(json.dumps(payload))


def test_captured_unmarked_metric_rejects_per_location() -> None:
    declaration = NodeDeclaration(
        "measure",
        source,
        inputs={"main": LocationDependency(provider="tracts")},
        cache="per_location",
    )
    with pytest.raises(ValueError, match="Unpartitioned"):
        compile_node_declarations(
            compile_provider_registry(
                {"test": PluginDefinition(resources={"places": Tracts})}
            ),
            [declaration],
        )
