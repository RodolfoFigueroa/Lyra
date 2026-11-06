"""Deterministic persistent identity for executable graph contracts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from lyra.sdk.location_contracts import LocationContract
from lyra.sdk.models.cache_revision import CacheRevision
from lyra.sdk.plugin_loader import installed_version
from pydantic import BaseModel, TypeAdapter

if TYPE_CHECKING:
    from lyra.sdk.graph import PluginGraph
    from lyra.sdk.locations import SelectedLocationProvider


def validate_cache_revision(value: str | None) -> str | None:
    """Validate a caller revision without coercing or trimming it.

    Returns:
        The exact supplied revision or None.
    """
    return TypeAdapter(CacheRevision | None).validate_python(value)


# Bump these when the corresponding interpretation or stored representation changes.
PROTOCOLS = {"computation": 1, "invocation": 1, "content": 1, "artifacts": 2}


def computation_namespace(
    graph: PluginGraph,
    provider: SelectedLocationProvider | None,
    revision: str | None,
) -> str:
    """Hash contracts and available versions, never implementation or source data.

    The complete enabled graph is included conservatively, so all transitive
    metric and location dependencies are covered before a root cache lookup.
    Missing distribution versions are represented by JSON null.

    Returns:
        The deterministic namespace digest.
    """
    revision = validate_cache_revision(revision)
    distributions = {entry.distribution for entry in graph.nodes.values()}
    if provider is not None:
        distributions.add(provider.distribution)
    payload = {
        "protocols": PROTOCOLS,
        "revision": revision,
        "nodes": {
            name: {
                "distribution": entry.distribution,
                "contract": structural_contract(entry.node),
            }
            for name, entry in graph.nodes.items()
        },
        "versions": {name: installed_version(name) for name in sorted(distributions)},
        "provider": None
        if provider is None
        else {
            "distribution": provider.distribution,
            "contract": structural_contract(provider.declaration),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def structural_contract(model: BaseModel) -> object:
    """Serialize declarations while excluding only location data revision fields.

    Returns:
        JSON-compatible structural metadata, including nested declarations.
    """

    def exclusions(value: object) -> dict:
        if isinstance(value, BaseModel):
            result: dict = {
                name: exclusions(getattr(value, name))
                for name in type(value).model_fields
            }
            if isinstance(value, LocationContract):
                result["data_revision"] = True
            return result
        if isinstance(value, dict):
            return {key: exclusions(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return {index: exclusions(item) for index, item in enumerate(value)}
        return {}

    return model.model_dump(mode="json", exclude=exclusions(model))
