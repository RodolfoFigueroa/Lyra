"""Synchronous local execution of trusted plugin graphs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self
from uuid import uuid4

from lyra.contracts.accepted import PreparedExecution
from lyra.contracts.config import normalize_distribution_name
from lyra.contracts.errors import (
    PluginDefinitionError,
)
from lyra.contracts.jobs import JobProgress
from lyra.contracts.plugins import PluginInfo, PluginManifest
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.loading import load_bound_plugin
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.manifests import load_plugin_manifest
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.resources import collect_resources
from lyra.sdk.resources import resource_metadata

if TYPE_CHECKING:
    from lyra.contracts.provenance import ExecutionProvenance
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.runtime.arguments import PreparedResolvedInvocation
    from lyra.sdk.context import RunContext
    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.resources import BaseIO

ContextFactory = Callable[[str], "RunContext"]


class _Unspecified(Enum):
    VALUE = "unspecified"


@dataclass(frozen=True)
class _LocalContext:
    job_id: str
    node: str
    logger: logging.LoggerAdapter
    temp_dir: Path

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Validate progress and emit it through the node logger."""
        progress = JobProgress(
            timestamp=datetime.now(UTC),
            stage=stage,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )
        self.logger.info("Progress: %s", progress.model_dump(mode="json"))


class LocalRunner:
    """Run installed or in-memory plugins without application services.

    Factories are loaded and the graph is validated once at construction. Each
    run has fresh execution state. Custom context factories own their context
    services. Injected I/O resources are initialized and closed by each run.
    """

    def __init__(
        self,
        plugins: Sequence[str],
        *,
        context_factory: ContextFactory | None = None,
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO]] | None = None,
        artifact_dir: Path | None = None,
    ) -> None:
        """Load explicitly selected installed plugins and their manifests.

        Raises:
            ValueError: If plugin names repeat or the graph is invalid.
        """
        plugins_by_name = {}
        for name in plugins:
            distribution = normalize_distribution_name(name)
            if distribution in plugins_by_name:
                msg = f"Duplicate plugin distribution: {distribution!r}"
                raise ValueError(msg)
            manifest = load_plugin_manifest(distribution)
            plugins_by_name[distribution] = (manifest, load_bound_plugin(manifest))
        self._initialize(
            plugins_by_name,
            context_factory,
            resource_config,
            resource_overrides,
            artifact_dir,
        )

    @classmethod
    def from_definitions(
        cls,
        plugins: Mapping[str, PluginDefinition],
        *,
        context_factory: ContextFactory | None = None,
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO]] | None = None,
        artifact_dir: Path | None = None,
    ) -> Self:
        """Build a runner directly from definitions, without installed metadata.

        Returns:
            A runner whose plugin ownership keys are the supplied mapping keys.

        Raises:
            ValueError: If a plugin ownership key is blank or the graph is invalid.
        """
        plugins_by_name = {}
        for name, definition in plugins.items():
            if not name.strip():
                msg = "Supply at least one plugin with nonblank ownership keys."
                raise ValueError(msg)
            # The local catalog compiles declarations directly from in-memory plugins.
            manifest = PluginManifest.model_construct(
                plugin=PluginInfo(name=name, version="local"),
                factory="local:create_plugin",
                nodes=list(definition.node_manifests),
                resources={
                    key: resource_metadata(provider)
                    for key, provider in definition.resources.items()
                },
                location_provider=definition.location_provider,
                route_preferences=definition.route_preferences,
            )
            plugins_by_name[name] = (manifest, BoundPlugin(definition))
        runner = cls.__new__(cls)
        cls._initialize(
            runner,
            plugins_by_name,
            context_factory,
            resource_config,
            resource_overrides,
            artifact_dir,
        )
        return runner

    def _initialize(
        self,
        plugins: Mapping[str, tuple[PluginManifest, BoundPlugin]],
        context_factory: ContextFactory | None,
        resource_config: Mapping[str, ResourceConfig] | None,
        resource_overrides: Mapping[str, type[BaseIO]] | None,
        artifact_dir: Path | None,
    ) -> None:
        if not plugins:
            msg = "Supply at least one plugin with nonblank ownership keys."
            raise ValueError(msg)
        self._provenance: ExecutionProvenance | None = None
        self._cache = TableCache(artifact_dir) if artifact_dir is not None else None
        self._plugins = {name: binding for name, (_, binding) in plugins.items()}
        collect_resources(
            {name: plugin.resources for name, plugin in self._plugins.items()}
        )
        self.catalog = compile_catalog(
            {name: manifest for name, (manifest, _) in plugins.items()}
        )
        self.location_provider = self.catalog.location_provider
        self._graph = self.catalog.graph
        self._context_factory = context_factory
        self._resource_config = resource_config
        self._resource_overrides = resource_overrides

    def inspect(self) -> ExecutionProvenance | None:
        """Return the latest run's observations and accepted bound metadata."""
        return self._provenance

    def run_development(
        self, metric: str, inputs: Mapping[str, object]
    ) -> NativeOutputs:
        """Execute a concrete metric, including private roots, on resolved inputs.

        Author-declared dependencies may use noncanonical operations.

        Returns:
            Native named output tables.

        Raises:
            PluginDefinitionError: If the concrete metric is unknown.
        """
        self._provenance = None
        entry = self._graph.nodes.get(metric)
        if entry is None or entry.node.kind != "metric":
            message = f"Unknown metric: {metric!r}"
            raise PluginDefinitionError(message)
        invocation = self._plugins[entry.distribution].prepare_resolved_invocation(
            metric, inputs
        )
        return self._execute(invocation)

    def run(
        self,
        metric: str,
        inputs: Mapping[str, object],
        *,
        cache_revision: str | _Unspecified | None = _Unspecified.VALUE,
        edition: str | _Unspecified | None = _Unspecified.VALUE,
    ) -> NativeOutputs:
        """Execute one public metric and return native tables without copying.

        Each location argument selects IDs by collection and unit type. Provider
        resolution and handlers share one execution resource scope. Scratch storage
        is shared within the run and removed before returning.

        Returns:
            Named frames with declared dtypes, fresh RangeIndexes, and native CRS.

        """
        self._provenance = None
        invocation = prepare_execution(
            self.catalog,
            {
                "metric": metric,
                **({"edition": edition} if edition is not _Unspecified.VALUE else {}),
                **(
                    {"parameters": inputs["parameters"]}
                    if "parameters" in inputs
                    else {}
                ),
                "inputs": {
                    key: value for key, value in inputs.items() if key != "parameters"
                },
                **(
                    {"cache_revision": cache_revision}
                    if cache_revision is not _Unspecified.VALUE
                    else {}
                ),
            },
        )
        return self._execute(invocation)

    def _execute(
        self, invocation: PreparedExecution | PreparedResolvedInvocation
    ) -> NativeOutputs:
        if self._context_factory is not None:
            return self._execute_with_context(invocation, self._context_factory)
        job_id = str(uuid4())
        with TemporaryDirectory(prefix="lyra-local-") as directory:

            def context_factory(node: str) -> RunContext:
                return _LocalContext(
                    job_id,
                    node,
                    logging.LoggerAdapter(
                        logging.getLogger(f"lyra.local.{node}"),
                        {"job_id": job_id, "node": node},
                    ),
                    Path(directory),
                )

            return self._execute_with_context(invocation, context_factory)

    def _execute_with_context(
        self,
        invocation: PreparedExecution | PreparedResolvedInvocation,
        context_factory: ContextFactory,
    ) -> NativeOutputs:
        executor = GraphExecutor(
            self.catalog,
            self._plugins,
            context_factory,
            self._resource_config,
            self._resource_overrides,
            cache=self._cache,
            cache_revision=(
                invocation.cache_revision
                if isinstance(invocation, PreparedExecution)
                else None
            ),
        )
        try:
            return executor.execute(invocation).outputs
        finally:
            self._provenance = executor.provenance
