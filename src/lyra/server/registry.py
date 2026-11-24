"""Captured application catalog."""

import hashlib
import json
from dataclasses import dataclass
from functools import cached_property
from operator import itemgetter

from lyra.contracts.config import PluginsConfig
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.discovery import Discovery
from lyra.server.plugins import load_installed_plugins


@dataclass(frozen=True)
class Catalog:
    """Authoritative compiled contracts."""

    compiled: CompiledCatalog

    @cached_property
    def discovery(self) -> Discovery:
        """Reuse public projections for this captured catalog."""
        return Discovery(self.compiled)

    @cached_property
    def operational_fingerprint(self) -> str:
        """Identify captured manifests and distributions."""
        payload = []
        for distribution, manifest in sorted(self.compiled.manifests.items()):
            data = manifest.model_dump(mode="json")
            data["distribution"] = distribution
            data["nodes"] = sorted(data["nodes"], key=itemgetter("name"))
            payload.append(data)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @classmethod
    def load(
        cls, plugins: PluginsConfig, *, build_revision: str | None = None
    ) -> "Catalog":
        """Load and compile enabled live plugin definitions.

        Returns:
            Captured contracts for this application.
        """
        return cls(load_installed_plugins(plugins, build_revision=build_revision))

    def get_loaded_metric_names(self) -> list[str]:
        """Return sorted public concrete metric names."""
        return sorted(
            name
            for name, entry in self.compiled.graph.nodes.items()
            if entry.node.is_public_metric
        )
