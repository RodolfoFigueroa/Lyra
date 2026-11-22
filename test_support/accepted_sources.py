"""Reusable accepted sources fixtures."""

from typing import Any

from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.sdk.parameters import MetricParameters
from test_support.routing_plugins import routed_plugins


def routed_request(
    model: type[MetricParameters] | None = None,
    *,
    private: bool = False,
    multihop: bool = False,
) -> tuple[CompiledCatalog, dict[str, Any]]:
    """Prepare a two-input live catalog and caller-owned selections."""
    catalog = compile_live_catalog(
        routed_plugins(model, private=private, multihop=multihop)
    )
    return catalog, {
        "metric": "score",
        "inputs": {
            name: {"provider": source, "ids": ["unknown"]}
            for name, source in (("places", "source"), ("reference", "other"))
        },
    }
