"""Shared isolated provider/storage test boundaries."""

from unittest.mock import Mock, create_autospec

import pandera.pandas as pa

from lyra.contracts.origins import Origin
from lyra.contracts.resources import ResourceConfig
from lyra.runtime.planning.provider_preparation import PreparedProviderInput
from lyra.runtime.planning.provider_registry import compile_provider_registry
from lyra.runtime.resources import ResourceScope
from lyra.sdk.plugin import PluginDefinition
from tests.provider_helpers import FakeProvider
from tests.provider_schema_helpers import declaration

CENSUS = Origin(id="census", revision="2025")


def acquisition_fixture(
    resolver: object,
    *,
    schema: pa.DataFrameSchema | None = None,
) -> tuple[PreparedProviderInput, ResourceScope]:
    if isinstance(resolver, Mock):
        resolver = create_autospec(FakeProvider.resolve, side_effect=resolver)
    declared = type(
        "Provider",
        (FakeProvider,),
        {
            "id": "tracts",
            "origins": (CENSUS,),
            "resolve": resolver,
            "secret_names": (),
            "setup": lambda _: None,
            "close": lambda _: None,
            "schema": declaration() if schema is None else schema,
        },
    )
    registry = compile_provider_registry(
        {"test": PluginDefinition(resources={"binding": declared})}
    )
    return (
        PreparedProviderInput("places", frozenset({"a", "b"}), registry["tracts"]),
        ResourceScope(
            {"binding": declared},
            {"binding": ResourceConfig(config={"label": "configured"})},
        ),
    )
