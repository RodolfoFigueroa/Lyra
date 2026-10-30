"""Deterministic persistent identity for executable graph contracts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Annotated

from lyra.sdk.plugin_loader import installed_version
from pydantic import StringConstraints, TypeAdapter

if TYPE_CHECKING:
    from lyra.sdk.graph import PluginGraph
    from lyra.sdk.locations import SelectedLocationProvider

CacheRevision = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r"\S")
]
"""Exact nonblank caller revision, at most 256 characters."""


def validate_cache_revision(value: str | None) -> str | None:
    """Validate a caller revision without coercing or trimming it.

    Returns:
        The exact supplied revision or None.
    """
    return TypeAdapter(CacheRevision | None).validate_python(value)


# Bump these when the corresponding interpretation or stored representation changes.
PROTOCOLS = {"computation": 1, "invocation": 1, "content": 1, "artifacts": 1}


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
                "contract": entry.node.model_dump(mode="json"),
            }
            for name, entry in graph.nodes.items()
        },
        "versions": {name: installed_version(name) for name in sorted(distributions)},
        "provider": None
        if provider is None
        else {
            "distribution": provider.distribution,
            "contract": provider.declaration.model_dump(mode="json"),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
