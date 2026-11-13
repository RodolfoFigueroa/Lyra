import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any
from unittest.mock import Mock, create_autospec

import pandas as pd
import pandera.pandas as pa
import pytest

from lyra.contracts.errors import (
    MetricResultError,
    PluginDefinitionError,
    ResourceConfigurationError,
)
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.sdk.locations import LocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.resources import BaseIO, Resource
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration, frame

CENSUS = Origin(id="census", revision="2025")
ELEVATION = Origin(id="elevation")


@metric(
    code_revision="1",
    name="plain_registry_metric",
    description="A metric with no location inputs.",
    outputs={"result": pa.DataFrameSchema()},
)
def plain() -> pd.DataFrame:
    return pd.DataFrame()


def provider(**attributes: object) -> type[FakeProvider]:
    return type(
        "Provider",
        (FakeProvider,),
        {"id": "tracts", "origins": (CENSUS,), "schema": declaration()} | attributes,
    )


def plugin(**resources: type[BaseIO[Any]]) -> PluginDefinition:
    return PluginDefinition(resources=resources, metrics=[plain])


def test_all_providers_in_provider_only_and_separate_plugins() -> None:
    schema = declaration()
    tracts = provider(schema=schema)
    counties = provider(id="counties", schema=schema)
    combined = provider(id="combined", origins=[ELEVATION, CENSUS, CENSUS])
    definitions = {
        "census": PluginDefinition(
            resources={"tract_binding": tracts, "county_binding": counties},
        ),
        "other": plugin(combined_binding=combined),
    }
    registry = compile_provider_registry(definitions)
    assert set(registry) == {"tracts", "counties", "combined"}
    assert registry["tracts"].resource == "tract_binding"
    assert registry["counties"].resource == "county_binding"
    assert registry["tracts"].scope != registry["counties"].scope
    assert registry["tracts"].scope.code_revision is None
    assert registry["tracts"].description is None
    assert registry["combined"].scope.origins == frozenset({CENSUS, ELEVATION})
    with pytest.raises(KeyError):
        _ = registry["tract_binding"]


def test_capture_without_construction_credentials_or_mutable_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("acquired during compilation"))
    origins = [CENSUS]
    schema = declaration()
    declared = provider(origins=origins, schema=schema, description="Tracts")
    for method in ("__init__", "setup", "resolve"):
        monkeypatch.setattr(
            declared,
            method,
            create_autospec(declared.resolve, side_effect=forbidden)
            if method == "resolve"
            else forbidden,
        )
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    definitions = {"active": plugin(binding=declared)}
    registry = compile_provider_registry(definitions)
    captured = registry["tracts"]
    definitions.clear()
    origins.append(ELEVATION)
    schema.columns.clear()
    monkeypatch.setattr(declared, "id", "changed")
    monkeypatch.setattr(declared, "description", "Changed")
    assert captured.scope == ProviderScope(provider_id="tracts", origins=[CENSUS])
    assert captured.description == "Tracts"
    validated = captured.schema.validate(frame())
    assert validated[frame().columns].equals(frame())
    forbidden.assert_not_called()


@pytest.mark.parametrize("across_plugins", [False, True])
@pytest.mark.parametrize("same_class", [False, True])
def test_competing_registrations(*, across_plugins: bool, same_class: bool) -> None:
    first = provider()
    second = first if same_class else provider()
    definitions = (
        {"one": plugin(first=first), "two": plugin(second=second)}
        if across_plugins
        else {"one": plugin(first=first, second=second)}
    )
    with pytest.raises(PluginDefinitionError, match=r"Conflicting provider ID.*tracts"):
        compile_provider_registry(definitions)


def test_identical_resource_binding_coalesces_and_alias_does_not_change_scope() -> None:
    declared = provider(code_revision="opaque-v1")
    registry = compile_provider_registry(
        {"one": plugin(binding=declared), "two": plugin(binding=declared)}
    )
    renamed = compile_provider_registry({"other-version": plugin(alias=declared)})
    assert len(registry) == 1
    assert registry["tracts"].scope == renamed["tracts"].scope
    assert registry["tracts"].scope.code_revision == "opaque-v1"


def test_only_supplied_active_plugins_participate() -> None:
    installed = {"active": plugin(one=provider()), "disabled": plugin(two=provider())}
    registry = compile_provider_registry(
        {name: definition for name, definition in installed.items() if name == "active"}
    )
    assert registry["tracts"].resource == "one"
    assert compile_provider_registry({}) == {}


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("id", None),
        ("id", ""),
        ("id", 123),
        ("code_revision", 123),
        ("description", 123),
        ("origins", None),
        ("origins", [{"id": "census"}]),
        ("schema", None),
        ("schema", {}),
        ("schema", pa.DataFrameSchema(strict=False)),
    ],
)
def test_invalid_declarations(attribute: str, value: object) -> None:
    with pytest.raises(PluginDefinitionError, match="Invalid provider registration"):
        compile_provider_registry(
            {"active": plugin(binding=provider(**{attribute: value}))}
        )


@pytest.mark.parametrize("attribute", ["id", "schema"])
def test_missing_required_declaration(attribute: str) -> None:
    attributes = {
        "id": "tracts",
        "schema": declaration(),
        "resolve": FakeProvider.resolve,
    }
    attributes.pop(attribute)
    declared = type("MissingDeclaration", (LocationProvider,), attributes)
    with pytest.raises(PluginDefinitionError, match="Invalid provider registration"):
        compile_provider_registry({"active": plugin(binding=declared)})


def test_non_instantiable_provider_rejected() -> None:
    abstract = type(
        "AbstractProvider",
        (LocationProvider,),
        {
            "id": "abstract",
            "origins": (),
            "schema": declaration(),
        },
    )
    with pytest.raises(PluginDefinitionError, match="must be concrete"):
        compile_provider_registry({"active": plugin(binding=abstract)})


def test_combined_resource_graph_is_validated_before_acquisition() -> None:
    class Dependent(FakeProvider):
        id = "tracts"
        origins = (CENSUS,)
        schema = declaration()
        database: Annotated[BaseIO, Resource("database")]

    with pytest.raises(ResourceConfigurationError, match="Missing or incompatible"):
        compile_provider_registry({"active": plugin(binding=Dependent)})
    registry = compile_provider_registry(
        {"active": plugin(binding=Dependent), "infrastructure": plugin(database=BaseIO)}
    )
    assert registry["tracts"].resource == "binding"


def test_executable_checks_remain_authoritative() -> None:
    schema = declaration()
    schema.columns["count"].checks = [pa.Check.gt(10)]
    registry = compile_provider_registry(
        {"active": plugin(binding=provider(schema=schema))}
    )
    with pytest.raises(MetricResultError, match="greater_than"):
        registry["tracts"].schema.validate(frame())


async def _async_resolver(_self: object, _ids: list[str]) -> None:
    await asyncio.sleep(0)


def _generator_resolver(_self: object, _ids: list[str]) -> Iterator[object]:
    yield None


async def _async_generator_resolver(
    _self: object, _ids: list[str]
) -> AsyncIterator[object]:
    await asyncio.sleep(0)
    yield None


@pytest.mark.parametrize(
    "resolver",
    [
        None,
        lambda _self: None,
        lambda _self, _contract, _ids: None,
        lambda _self, _ids, _extra=None: None,
        lambda _self, *, _ids: None,
        lambda _self, *_ids: None,
        lambda _self, _ids=None: None,
        staticmethod(lambda _ids: None),
        classmethod(lambda _cls, _ids: None),
        _async_resolver,
        _generator_resolver,
        _async_generator_resolver,
    ],
)
def test_invalid_resolvers_fail_before_construction(
    resolver: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = provider(resolve=resolver)
    forbidden = Mock(side_effect=AssertionError("Acquisition during registration"))
    monkeypatch.setattr(declared, "__init__", forbidden)
    monkeypatch.setattr(declared, "setup", forbidden)
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    with pytest.raises(PluginDefinitionError, match="Provider resolve must"):
        compile_provider_registry({"test": plugin(places=declared)})
    forbidden.assert_not_called()
