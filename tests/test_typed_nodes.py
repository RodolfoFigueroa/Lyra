import pandas as pd
import pandera.pandas as pa
import pytest
from lyra.sdk import (
    Locations,
    PluginDefinition,
    PluginDefinitionError,
    location_operation,
    metric,
)
from lyra.sdk.errors import MetricInputError
from lyra.sdk.models.plugin import PluginInfo, PluginManifest
from pydantic import ValidationError

from tests.test_location_contracts import dataset
from tests.test_location_providers import FakeProvider
from tests.test_location_schema import column, contract, declaration, locations


def test_named_concrete_inputs_and_operation_output() -> None:
    first = dataset()
    second = dataset(source="other")
    schema = contract(declaration(count=column(int, checks=pa.Check.ge(0))))

    @metric(
        name="compare",
        description="Compare population observations.",
        outputs={
            "comparison": pa.DataFrameSchema(
                {},
                metadata={
                    "meaning": "Population difference",
                    "granularity": "One row per unit",
                },
            )
        },
        locations={"baseline": first, "current": second},
    )
    def compare(baseline: Locations, current: Locations) -> pd.DataFrame:
        assert baseline.contract == first.reference
        assert current.contract == second.reference
        return pd.DataFrame()

    @location_operation(
        name="convert",
        description="Select another observation.",
        output=schema,
        locations={"source": first},
        output_contract=second,
    )
    def convert(source: Locations) -> object:
        return source.to_geodataframe(id_column="key", geometry_column="shape")

    plugin = PluginDefinition(metrics=[compare], location_operations=[convert])
    value = locations({"count": 2}).with_contract(first)
    other = value.with_contract(second)
    prepared = plugin.prepare_resolved_invocation(
        "compare", {"baseline": value, "current": other}
    )
    assert prepared.locations["baseline"].contract == first.reference
    prepare = plugin.prepare_resolved_invocation
    with pytest.raises(MetricInputError, match="concrete contract"):
        prepare(
            "compare",
            {"baseline": value.with_binding(None, None), "current": other},
        )
    with pytest.raises(MetricInputError, match="concrete contract"):
        prepare("compare", {"baseline": other, "current": other})
    invocation = plugin.prepare_resolved_invocation("convert", {"source": value})
    result = plugin.normalize_location_result(invocation, convert(value))
    assert result.contract == second.reference
    with pytest.raises(ValueError, match="concrete output"):
        plugin.validate_cached_locations(invocation, value)
    manifest = plugin.manifest(
        plugin=PluginInfo(name="test", version="1"), factory="test:create"
    )
    restored = PluginManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    for index, field in [(0, "location_contracts"), (1, "output_contract")]:
        payload = manifest.model_dump()
        del payload["nodes"][index][field]
        with pytest.raises(ValidationError, match="contract"):
            PluginManifest.model_validate(payload)
    assert (
        restored.nodes[0].location_contracts["baseline"].field_definitions
        == first.field_definitions
    )

    assert restored.nodes[0].outputs["comparison"].meaning == "Population difference"
    assert restored.nodes[0].outputs["comparison"].granularity == "One row per unit"


def test_declarations_reject_incomplete_inputs_and_wrong_output_schema() -> None:
    first = dataset()

    def handler(left: Locations, right: Locations) -> pd.DataFrame:
        del left, right
        return pd.DataFrame()

    with pytest.raises(PluginDefinitionError, match="every named location"):
        metric(
            name="incomplete",
            description="Incomplete contracts.",
            outputs={"result": pa.DataFrameSchema({})},
            locations={"left": first},
        )(handler)
    with pytest.raises(PluginDefinitionError, match="operation schema"):
        location_operation(
            name="invalid_target",
            locations={"left": first, "right": first},
            description="Incompatible target structure.",
            output=contract(declaration()),
            output_contract=first,
        )(handler)


def test_node_contract_must_agree_with_registered_provider() -> None:
    registered = FakeProvider.contracts[0]
    altered = registered.model_copy(update={"meaning": "Conflicting meaning"})

    @metric(
        name="conflict",
        description="Check registration agreement.",
        outputs={"result": pa.DataFrameSchema({})},
        locations={"subject": altered},
    )
    def conflict(subject: Locations) -> pd.DataFrame:
        del subject
        return pd.DataFrame()

    plugin = PluginDefinition(
        metrics=[conflict],
        resources={"places": FakeProvider},
        location_provider="places",
    )
    with pytest.raises(ValueError, match="contracts disagree"):
        plugin.manifest(
            plugin=PluginInfo(name="test", version="1"), factory="test:create"
        )
