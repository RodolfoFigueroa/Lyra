"""Local JSON Schema validation shared by plugin generation and execution."""

import math
from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator
from lyra.sdk.client_contract import JSON_SCHEMA_DIALECT
from lyra.sdk.models.location_payload import LocationSelection

_SCHEMA_MAPS = {"$defs", "properties", "patternProperties", "dependentSchemas"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_VALUES = {
    "items",
    "contains",
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
    "unevaluatedItems",
}


def schema_nodes(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield schema nodes and paths without traversing example/default data."""
    yield "", schema
    for key, value in schema.items():
        children: list[tuple[str, dict[str, Any]]] = []
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            children = [
                (str(name), child)
                for name, child in value.items()
                if isinstance(child, dict)
            ]
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            children = [
                (str(index), child)
                for index, child in enumerate(value)
                if isinstance(child, dict)
            ]
        elif key in _SCHEMA_VALUES and isinstance(value, dict):
            children = [("", value)]
        for name, child in children:
            for suffix, node in schema_nodes(child):
                yield f"/{key}/{name}{suffix}".rstrip("/"), node


def resolve_schema_reference(
    schema: dict[str, Any], reference: str
) -> dict[str, Any] | bool:
    """Resolve a local definition pointer without accessing external resources.

    Returns:
        The referenced schema value.

    Raises:
        ValueError: If the reference is external or does not resolve.
        TypeError: If the reference does not identify a schema.
    """
    if not reference.startswith("#/$defs/"):
        msg = f"Schema reference must start with #/$defs/: {reference!r}"
        raise ValueError(msg)
    value: Any = schema
    try:
        for segment in reference[2:].split("/"):
            key = segment.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        msg = f"Unresolved schema reference: {reference!r}"
        raise ValueError(msg) from exc
    if not isinstance(value, dict | bool):
        msg = f"Reference does not identify a schema: {reference!r}"
        raise TypeError(msg)
    return value


def check_request_schema(schema: dict[str, Any]) -> None:
    """Check the dialect, structure, and self-contained references of a schema.

    Raises:
        ValueError: If the schema declares an unsupported resource or dialect.
    """
    if schema.get("$schema") != JSON_SCHEMA_DIALECT:
        msg = "Request schema must declare Draft 2020-12."
        raise ValueError(msg)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        message = f"Invalid request JSON Schema: {exc.message}"
        raise ValueError(message) from exc
    for path, node in schema_nodes(schema):
        if any(key in node for key in ("$id", "$dynamicRef", "$recursiveRef")):
            msg = f"Unsupported schema resource or dynamic reference at {path or '/'}"
            raise ValueError(msg)
        if "$ref" in node:
            resolve_schema_reference(schema, node["$ref"])


def subschema_validator(
    root: dict[str, Any],
    schema: dict[str, Any],
) -> Validator:
    """Build a validator for a subschema with the root's local definitions.

    Returns:
        A validator that preserves references into the complete request schema.
    """
    return Draft202012Validator({**schema, "$defs": root.get("$defs", {})})


def check_json_value(value: object, path: str = "value") -> None:
    """Reject Python values that JSON encoding would coerce or cannot represent.

    Raises:
        ValueError: If a value is not a finite JSON primitive or container.
    """
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            check_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            check_json_value(item, f"{path}.{key}")
        return
    message = f"{path}: expected a finite JSON value with string object keys"
    raise ValueError(message)


def check_parameter_schema(schema: dict[str, Any]) -> None:
    """Validate a self-contained object parameter schema.

    Raises:
        ValueError: If the declaration is invalid.
    """
    check_request_schema(schema)
    if (
        "$ref" in schema
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
    ):
        message = "Parameters must directly describe an object with properties"
        raise ValueError(message)


def build_node_request_schema(
    name: str, parameters: dict[str, Any] | None, locations: Mapping[str, str | None]
) -> dict[str, Any]:
    """Compose detached request schemas with collision-free local definitions.

    Returns:
        The complete Draft 2020-12 request schema.
    """
    definitions: dict[str, Any] = {}

    def embed(schema: dict[str, Any], namespace: str) -> dict[str, Any]:
        schema = deepcopy(schema)
        schema.pop("$schema", None)
        for _, node in schema_nodes(schema):
            reference = node.get("$ref")
            if isinstance(reference, str):
                node["$ref"] = "#/$defs/" + namespace + reference[len("#/$defs/") :]
        definitions.update(
            {namespace + key: value for key, value in schema.pop("$defs", {}).items()}
        )
        return schema

    properties: dict[str, Any] = {}
    if parameters is not None:
        properties["parameters"] = embed(parameters, "parameter__")
    if locations:
        selection = embed(
            LocationSelection.model_json_schema(mode="validation"), "location__"
        )
        for field, description in sorted(locations.items()):
            properties[field] = {
                **deepcopy(selection),
                "description": description or f"Lyra-resolved {field} reference.",
            }
    result = {
        "$schema": JSON_SCHEMA_DIALECT,
        "title": f"{name}_request",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }
    if definitions:
        result["$defs"] = definitions
    check_request_schema(result)
    return result
