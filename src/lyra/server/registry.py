"""Captured application catalog and queue routing."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from operator import itemgetter
from types import MappingProxyType

from lyra.contracts.config import PluginsConfig
from lyra.runtime.planning.catalog import CompiledCatalog, compile_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.server.plugins import load_installed_plugins


@dataclass(frozen=True)
class Catalog:
    """Authoritative compiled contracts with application-owned queue routing."""

    compiled: CompiledCatalog
    metric_queues: Mapping[str, str]

    def __post_init__(self) -> None:
        """Capture routing for exactly the compiled metric nodes.

        Raises:
            ValueError: If routing does not match the compiled metric nodes.
        """
        expected = {
            name
            for name, entry in self.compiled.graph.nodes.items()
            if entry.node.kind == "metric"
        }
        if set(self.metric_queues) != expected:
            message = "Queue assignments must cover exactly the compiled metric nodes."
            raise ValueError(message)
        object.__setattr__(
            self, "metric_queues", MappingProxyType(dict(self.metric_queues))
        )

    @cached_property
    def discovery(self) -> Discovery:
        """Reuse public projections for this captured catalog."""
        return Discovery(self.compiled)

    @cached_property
    def operational_fingerprint(self) -> str:
        """Identify captured manifests, distributions, and metric queue assignments."""
        payload = []
        for distribution, manifest in sorted(self.compiled.manifests.items()):
            data = manifest.model_dump(mode="json")
            data["distribution"] = distribution
            for node in data["nodes"]:
                if node["kind"] == "metric":
                    node["queue"] = self.metric_queues[node["name"]]
            data["nodes"] = sorted(data["nodes"], key=itemgetter("name"))
            payload.append(data)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def load(cls, plugins: PluginsConfig) -> "Catalog":
        """Load and compile enabled manifests without importing plugin handlers.

        Returns:
            Captured contracts and validated routing for this application.
        """
        installed = load_installed_plugins(plugins)
        return cls(
            compiled=compile_catalog({p.distribution: p.manifest for p in installed}),
            metric_queues={
                name: queue
                for plugin in installed
                for name, queue in plugin.metric_queues.items()
            },
        )

    def get_loaded_metric_names(self) -> list[str]:
        """Return sorted public concrete metric names."""
        return sorted(
            name
            for name, entry in self.compiled.graph.nodes.items()
            if entry.node.is_public_metric
        )

    def get_loaded_metric_queues(self) -> dict[str, str]:
        """Return public concrete metric queue assignments in name order."""
        return {
            name: self.metric_queues[name] for name in self.get_loaded_metric_names()
        }
