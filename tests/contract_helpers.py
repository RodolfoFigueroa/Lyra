"""Canonical manifest fixtures shared by application integration tests."""

from typing import Any

from pydantic import Field

from lyra.contracts.plugins import MetricManifest
from lyra.sdk.parameters import MetricParameters, build_parameter_schema
from test_support.location_helpers import node_contract, output_schema


class ValueParameters(MetricParameters):
    value: int = Field(description="Example input value.")


class FilterParameters(MetricParameters):
    sector_filters: list[str] = Field(max_length=5, description="Sector filters.")


class YearParameters(MetricParameters):
    year: int = Field(ge=2020, le=2026, description="Reference year.")


class PositiveParameters(MetricParameters):
    value: float = Field(ge=0, description="Nonnegative value.")


def metric_manifest(
    *,
    name: str = "light_metric",
    description: str = "A metric.",
    parameters: type[MetricParameters] | None = ValueParameters,
    spatial: set[str] | None = None,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spatial = spatial or {"location"}
    return MetricManifest.model_validate(
        {
            "kind": "metric",
            "name": name,
            "description": description,
            "parameters_schema": build_parameter_schema(name, parameters),
            "location_inputs": {
                key: {"contract": node_contract(schema=output_schema(original=str))}
                for key in spatial
            },
            "outputs": {
                name: output
                or {
                    "kind": "table",
                    "columns": [
                        {
                            "name": "value",
                            "dtype": "int64",
                            "unit": "count",
                            "description": "Example output value.",
                        }
                    ],
                }
            },
        }
    ).model_dump(mode="json")


def plugin_manifest(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "nodes": [metric],
    }
