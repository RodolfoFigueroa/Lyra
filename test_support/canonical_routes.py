"""Register canonical conversions and preferences for routing workloads."""

from collections.abc import Iterable, Mapping, Sequence

from lyra.sdk.plugin import PluginDefinition
from test_support.routing_plugins import conversion, provider


def route_plugins(
    edges: Mapping[str, tuple[str, str]],
    *,
    preferences: Iterable[Sequence[str]] = (),
    isolated: Sequence[str] = (),
) -> dict[str, PluginDefinition]:
    providers = {name for pair in edges.values() for name in pair}.union(isolated)
    operations = {
        name: conversion(name, source, target)
        for name, (source, target) in edges.items()
    }
    return {
        "routes": PluginDefinition(
            resources={name: provider(name) for name in sorted(providers)},
            location_operations=list(operations.values()),
            route_preferences=[
                tuple(operations[name] for name in route) for route in preferences
            ],
        )
    }
