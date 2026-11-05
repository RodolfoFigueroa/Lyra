"""Normalized contracts retain validation and independent derived views."""

import copy
from typing import TYPE_CHECKING, Annotated

import pandera.pandas as pa
import pytest
from jsonschema import Draft202012Validator
from lyra.sdk import (
    Locations,
    MetricParameters,
    PluginDefinition,
    PluginDefinitionError,
    metric,
)
from lyra.sdk.models.location_payload import LocationSelection
from lyra.sdk.models.plugin import (
    NODE_MANIFEST_ADAPTER,
    LocationInputDeclaration,
    LocationOperationManifest,
    MetricManifest,
    PluginManifest,
)
from lyra.sdk.parameters import build_parameter_schema
from lyra.sdk.schema import check_request_schema
from pydantic import Field, ValidationError

from tests.location_helpers import node_contract

if TYPE_CHECKING:
    from lyra.sdk.types import JsonObject


class Nested(MetricParameters):
    count: int = Field(default=2, ge=1, description="Count")


class Parameters(MetricParameters):
    nested: Nested = Field(description="Nested settings")


def definition() -> PluginDefinition:
    @metric(
        name="normalized",
        description="Normalized contract",
        outputs={"table": pa.DataFrameSchema({})},
        locations={"places": node_contract()},
    )
    def handler(
        parameters: Parameters,
        places: Annotated[Locations, Field(description="Selected places")],
    ) -> object:
        return parameters, places

    return PluginDefinition(metrics=[handler])


def test_manifest_only_schema_and_detached_description() -> None:
    plugin = definition()
    original = plugin.node_manifests[0]
    encoded = original.model_dump(mode="json")
    assert (
        not {
            "request_schema",
            "spatial_inputs",
            "location_contracts",
            "location_output",
        }
        & encoded.keys()
    )
    restored = NODE_MANIFEST_ADAPTER.validate_json(original.model_dump_json())
    assert isinstance(restored, MetricManifest)
    assert restored.request_schema == original.request_schema
    schema = restored.request_schema
    check_request_schema(schema)
    validator = Draft202012Validator(schema)
    selection = {"collection": "test:units", "unit_type": "lot", "ids": ["one"]}
    assert validator.is_valid({"parameters": {"nested": {}}, "places": selection})
    assert not validator.is_valid(
        {"parameters": {"nested": {"count": 0}}, "places": selection}
    )
    assert not validator.is_valid({"places": selection})
    schema["properties"].clear()
    assert set(restored.request_schema["properties"]) == {"parameters", "places"}
    description = plugin.describe("normalized")
    description.manifest.location_inputs["places"].contract.meaning = "Changed"
    assert description.manifest.parameters_schema is not None
    description.manifest.parameters_schema.clear()
    assert plugin.node_manifests[0] == original


@pytest.mark.parametrize(
    "field",
    [
        "canonical",
        "output_contract",
        "location_output",
        "request_schema",
        "spatial_inputs",
    ],
)
def test_metric_rejects_nonmetric_and_redundant_fields(field: str) -> None:
    payload = definition().node_manifests[0].model_dump()
    payload[field] = None
    with pytest.raises(ValidationError):
        NODE_MANIFEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "field", ["private", "outputs", "family", "edition", "edition_metadata"]
)
def test_operation_rejects_metric_fields(field: str) -> None:
    operation = LocationOperationManifest(
        name="convert", description="Convert", output_contract=node_contract()
    )
    payload = operation.model_dump()
    payload[field] = None
    with pytest.raises(ValidationError):
        NODE_MANIFEST_ADAPTER.validate_python(payload)
    del payload[field]
    assert NODE_MANIFEST_ADAPTER.validate_python(payload) == operation
    del payload["output_contract"]
    with pytest.raises(ValidationError):
        NODE_MANIFEST_ADAPTER.validate_python(payload)


def test_operation_binding_and_canonical_validation() -> None:
    contract = node_contract()
    with pytest.raises(ValidationError, match="exactly one"):
        LocationOperationManifest(
            name="convert",
            description="Convert",
            output_contract=contract,
            canonical=True,
        )
    with pytest.raises(ValidationError, match="only to metrics"):
        LocationOperationManifest(
            name="convert",
            description="Convert",
            output_contract=contract,
            location_inputs={
                "source": LocationInputDeclaration(
                    contract=contract, source_bindings=(contract.reference,)
                )
            },
        )


def test_empty_parameters_differ_from_absent_parameters() -> None:
    class Empty(MetricParameters):
        pass

    node = definition().node_manifests[0]
    node.parameters_schema = None
    assert "parameters" not in node.request_schema["properties"]
    node.parameters_schema = build_parameter_schema("normalized", Empty)
    assert "parameters" in node.request_schema["required"]
    assert node.request_schema["properties"]["parameters"]["properties"] == {}


def test_definition_names_and_literal_reference_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = LocationSelection.model_json_schema()
    selection["$defs"] = {"Shared": {"type": "string"}}
    selection["properties"]["unit_type"] = {"$ref": "#/$defs/Shared"}
    monkeypatch.setattr(
        LocationSelection, "model_json_schema", lambda **_: copy.deepcopy(selection)
    )
    node = definition().node_manifests[0]
    literal: JsonObject = {"$ref": "#/$defs/Shared", "properties": {"$ref": "literal"}}
    parameter_schema: JsonObject = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/Shared"}},
        "$defs": {
            "Shared": {"type": "object", "default": literal, "examples": [literal]}
        },
    }
    node.parameters_schema = parameter_schema
    schema = node.request_schema
    check_request_schema(schema)
    assert schema["$defs"]["parameter__Shared"]["default"] == literal
    assert schema["$defs"]["parameter__Shared"]["examples"] == [literal]
    assert schema["$defs"]["location__Shared"] == {"type": "string"}
    assert node.parameters_schema["$defs"] == {
        "Shared": {"type": "object", "default": literal, "examples": [literal]}
    }


@pytest.mark.parametrize(
    "reference", ["https://example.invalid/schema", "#/$defs/Missing"]
)
def test_manifest_rejects_unresolved_or_external_parameter_references(
    reference: str,
) -> None:
    payload = definition().node_manifests[0].model_dump()
    payload["parameters_schema"]["properties"]["nested"] = {"$ref": reference}
    with pytest.raises(ValidationError, match="reference"):
        NODE_MANIFEST_ADAPTER.validate_python(payload)


def test_manifest_discriminator_is_required() -> None:
    payload = definition().node_manifests[0].model_dump()
    del payload["kind"]
    with pytest.raises(ValidationError, match="kind"):
        PluginManifest.model_validate(
            {
                "plugin": {"name": "test", "version": "1"},
                "factory": "test:create",
                "nodes": [payload],
            }
        )


def test_root_reference_cannot_hide_parameter_fields() -> None:
    payload = definition().node_manifests[0].model_dump()
    payload["parameters_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "$defs": {"Cycle": {"$ref": "#/$defs/Cycle"}},
        "$ref": "#/$defs/Cycle",
    }
    with pytest.raises(ValidationError, match="directly describe"):
        NODE_MANIFEST_ADAPTER.validate_python(payload)


def test_unknown_source_binding_is_rejected_at_registration() -> None:
    def handler(places: Locations) -> object:
        return places

    with pytest.raises(PluginDefinitionError, match="Unknown source binding input"):
        metric(
            name="invalid",
            description="Invalid binding",
            outputs={"table": pa.DataFrameSchema({})},
            locations={"places": node_contract()},
            source_bindings={"missing": (node_contract().reference,)},
        )(handler)
