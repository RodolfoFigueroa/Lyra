"""Shared retained provider and correspondence fixtures with isolated acquisition."""

from dataclasses import replace
from unittest.mock import Mock

from lyra.contracts.outputs import forwarding_schema
from lyra.contracts.provider_scope import ProviderScope
from lyra.contracts.results import LocationInputDescription
from lyra.runtime.location_inputs import (
    InputBindings,
    InputSelection,
    LocationInputs,
    OperationSelection,
)
from lyra.runtime.operation_pairs import OperationPairs
from lyra.runtime.provider_records import ProviderRecords, provider_registration
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.sqlite_store import Header, SQLiteStore
from test_support.publication import publish_result
from tests.acquisition_helpers import acquisition_fixture
from tests.provider_schema_helpers import frame
from tests.result_description_helpers import publish_values


def retained(store: SQLiteStore) -> tuple[InputSelection, InputSelection]:
    prepared, _ = acquisition_fixture(Mock(side_effect=AssertionError("acquisition")))
    provider = replace(
        prepared.provider,
        scope=prepared.provider.scope.model_copy(
            update={"description": "Target records"}
        ),
    )
    ProviderRecords(store).publish(provider, provider.schema.validate(frame()))
    registration = provider_registration(provider)
    direct = InputSelection(
        description=LocationInputDescription(
            kind="provider",
            provider_scope=provider.scope,
            table_schema=provider.schema.metadata,
            availability="available",
        ),
        registration=registration,
        records=("a", "b"),
    )
    source = ProviderScope(
        provider_id="sources", code_revision="v1", description="Source records"
    )
    header = Header(
        value_key="operation",
        node_id="nearby",
        output_scope="location",
        owner_scope=source,
        target_scope=provider.scope,
    )
    OperationPairs(store).publish(
        ((header, ("A", "B", "C", "empty")),),
        {"A": ("a", "b"), "B": ("a",), "C": ("b",)},
    )
    metadata = forwarding_schema(provider.schema.metadata)
    correspondence = InputSelection(
        description=LocationInputDescription(
            kind="correspondence",
            provider_scope=provider.scope,
            source_scope=source,
            table_schema=metadata,
            availability="available",
        ),
        registration=registration.forwarded,
        operations=(
            OperationSelection(header=header, sources=("A", "B"), targets=("a",)),
        ),
    )
    return direct, correspondence


def publish_inputs(
    store: SQLiteStore,
    selections: dict[str, InputSelection],
    *,
    output_keys: tuple[str, ...] = ("one",),
) -> str:
    references = {
        name: (LocationInputs(store).publish(selection),)
        for name, selection in selections.items()
    }
    description = publish_values(store, output_keys)
    description = description.model_copy(
        update={
            "location_inputs": InputBindings(
                names=tuple(references), selections=references
            )
        }
    )
    return publish_result(ResultDescriptions(store), description)
