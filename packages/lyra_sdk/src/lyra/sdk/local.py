"""Synchronous local execution of trusted plugin graphs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self
from uuid import uuid4

from lyra.sdk.cache import TableCache
from lyra.sdk.computation import validate_cache_revision
from lyra.sdk.config import normalize_distribution_name
from lyra.sdk.errors import (
    PluginDefinitionError,
)
from lyra.sdk.execution import GraphExecutor
from lyra.sdk.graph import build_graph
from lyra.sdk.job_inputs import prepare_selection_input
from lyra.sdk.locations import select_location_provider
from lyra.sdk.models.job import JobProgress
from lyra.sdk.plugin_loader import (
    load_plugin_manifest,
    load_validated_plugin_definition,
)
from lyra.sdk.resources import collect_resources

if TYPE_CHECKING:
    from lyra.sdk.arguments import PreparedSelectionInvocation
    from lyra.sdk.context import RunContext
    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.resources import BaseIO, ResourceConfig
    from lyra.sdk.results import NativeOutputs

ContextFactory = Callable[[str], "RunContext"]


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
        cache_dir: Path | None = None,
    ) -> None:
        """Load explicitly selected installed plugins and their manifests.

        Raises:
            ValueError: If plugin names repeat or the graph is invalid.
        """
        definitions = {}
        for name in plugins:
            distribution = normalize_distribution_name(name)
            if distribution in definitions:
                msg = f"Duplicate plugin distribution: {distribution!r}"
                raise ValueError(msg)
            definitions[distribution] = load_validated_plugin_definition(
                load_plugin_manifest(distribution)
            )
        self._initialize(
            definitions, context_factory, resource_config, resource_overrides, cache_dir
        )

    @classmethod
    def from_definitions(
        cls,
        plugins: Mapping[str, PluginDefinition],
        *,
        context_factory: ContextFactory | None = None,
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO]] | None = None,
        cache_dir: Path | None = None,
    ) -> Self:
        """Build a runner directly from definitions, without installed metadata.

        Returns:
            A runner whose plugin ownership keys are the supplied mapping keys.
        """
        runner = cls.__new__(cls)
        cls._initialize(
            runner,
            plugins,
            context_factory,
            resource_config,
            resource_overrides,
            cache_dir,
        )
        return runner

    def _initialize(
        self,
        plugins: Mapping[str, PluginDefinition],
        context_factory: ContextFactory | None,
        resource_config: Mapping[str, ResourceConfig] | None,
        resource_overrides: Mapping[str, type[BaseIO]] | None,
        cache_dir: Path | None,
    ) -> None:
        if not plugins or any(not key.strip() for key in plugins):
            msg = "Supply at least one plugin with nonblank ownership keys."
            raise ValueError(msg)
        self._cache = TableCache(cache_dir) if cache_dir is not None else None
        self._plugins = dict(plugins)
        collect_resources(self._plugins)
        self.location_provider = select_location_provider(
            {key: definition.location_provider for key, definition in plugins.items()}
        )
        self._graph = build_graph(
            {
                key: definition.node_manifests
                for key, definition in self._plugins.items()
            }
        )
        self._context_factory = context_factory
        self._resource_config = resource_config
        self._resource_overrides = resource_overrides

    def _prepare_invocation(
        self, metric: str, inputs: Mapping[str, object]
    ) -> PreparedSelectionInvocation:
        entry = self._graph.nodes.get(metric)
        if entry is None or entry.node.kind != "metric":
            msg = f"Unknown public metric: {metric!r}"
            raise PluginDefinitionError(msg)
        definition = self._plugins[entry.distribution]
        return prepare_selection_input(definition, metric, inputs)

    def run(
        self,
        metric: str,
        inputs: Mapping[str, object],
        *,
        cache_revision: str | None = None,
    ) -> NativeOutputs:
        """Execute one public metric and return native tables without copying.

        Each location argument selects IDs by collection and unit type. Provider
        resolution and handlers share one execution resource scope. Scratch storage
        is shared within the run and removed before returning.

        Returns:
            Named frames with declared dtypes, fresh RangeIndexes, and native CRS.

        """
        cache_revision = validate_cache_revision(cache_revision)
        invocation = self._prepare_invocation(metric, inputs)
        if self._context_factory is not None:
            return GraphExecutor(
                self._graph,
                self._plugins,
                self._context_factory,
                self._resource_config,
                self._resource_overrides,
                cache=self._cache,
                cache_revision=cache_revision,
                location_provider=self.location_provider,
            ).execute(invocation)
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

            return GraphExecutor(
                self._graph,
                self._plugins,
                context_factory,
                self._resource_config,
                self._resource_overrides,
                cache=self._cache,
                cache_revision=cache_revision,
                location_provider=self.location_provider,
            ).execute(invocation)
