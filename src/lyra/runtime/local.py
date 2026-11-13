"""Synchronous local execution of trusted plugin graphs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

from lyra.contracts.config import normalize_distribution_name
from lyra.contracts.jobs import JobCreateRequest, JobProgress
from lyra.runtime.cache import TableCache
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.loading import load_installed_plugin_definition

if TYPE_CHECKING:
    from lyra.contracts.accepted import PreparedExecution
    from lyra.contracts.provenance import ExecutionProvenance
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.results import NativeOutputs
    from lyra.sdk.context import RunContext
    from lyra.sdk.plugin import PluginDefinition
    from lyra.sdk.resources import BaseIO

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
        resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
        artifact_dir: Path | None = None,
    ) -> None:
        """Load explicitly selected installed plugin definitions.

        Raises:
            ValueError: If plugin names repeat or the graph is invalid.
        """
        plugins_by_name = {}
        for name in plugins:
            distribution = normalize_distribution_name(name)
            if distribution in plugins_by_name:
                msg = f"Duplicate plugin distribution: {distribution!r}"
                raise ValueError(msg)
            plugins_by_name[distribution] = load_installed_plugin_definition(
                distribution
            )
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
        resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
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
            plugins_by_name[name] = definition
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
        plugins: Mapping[str, PluginDefinition],
        context_factory: ContextFactory | None,
        resource_config: Mapping[str, ResourceConfig] | None,
        resource_overrides: Mapping[str, type[BaseIO[Any]]] | None,
        artifact_dir: Path | None,
    ) -> None:
        if not plugins:
            msg = "Supply at least one plugin with nonblank ownership keys."
            raise ValueError(msg)
        self._provenance: ExecutionProvenance | None = None
        self._cache = TableCache(artifact_dir) if artifact_dir is not None else None
        self.catalog = compile_live_catalog(plugins)
        self._plugins = dict(self.catalog.implementations)
        self.providers = self.catalog.providers
        self._graph = self.catalog.graph
        self._context_factory = context_factory
        self._resource_config = resource_config
        self._resource_overrides = resource_overrides

    def inspect(self) -> ExecutionProvenance | None:
        """Return the latest run's observations and accepted bound metadata."""
        return deepcopy(self._provenance)

    def run_development(
        self,
        metric: str,
        *,
        inputs: Mapping[str, object] | None = None,
        parameters: Mapping[str, object] | None = None,
        cache_revision: str | None = None,
    ) -> NativeOutputs:
        """Execute a metric, including private roots, using named ID selections.

        Returns:
            Native named output tables.
        """
        return self._run(metric, inputs, parameters, cache_revision, allow_private=True)

    def run(
        self,
        metric: str,
        *,
        inputs: Mapping[str, object] | None = None,
        parameters: Mapping[str, object] | None = None,
        cache_revision: str | None = None,
    ) -> NativeOutputs:
        """Execute a public metric using JSON selections and parameter values.

        Omitted mappings and Python None use empty mappings. Provider resolution
        and handlers share one resource scope, closed before returning.

        Returns:
            Named native frames with declared dtypes and fresh RangeIndexes.
        """
        return self._run(
            metric, inputs, parameters, cache_revision, allow_private=False
        )

    def _run(
        self,
        metric: str,
        inputs: Mapping[str, object] | None,
        parameters: Mapping[str, object] | None,
        cache_revision: str | None,
        *,
        allow_private: bool,
    ) -> NativeOutputs:
        self._provenance = None
        try:
            request = JobCreateRequest.model_validate(
                {
                    "metric": metric,
                    "inputs": {} if inputs is None else inputs,
                    "parameters": {} if parameters is None else parameters,
                    "cache_revision": cache_revision,
                }
            )
        except (ValueError, TypeError) as exc:
            raise PreparationError(metric, "request", str(exc)) from exc
        invocation = prepare_execution(
            self.catalog, request.model_dump(), allow_private=allow_private
        )
        return self._execute(invocation)

    def _execute(self, invocation: PreparedExecution) -> NativeOutputs:
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
        invocation: PreparedExecution,
        context_factory: ContextFactory,
    ) -> NativeOutputs:
        executor = GraphExecutor(
            self.catalog,
            self._plugins,
            context_factory,
            self._resource_config,
            self._resource_overrides,
            cache=self._cache,
            cache_revision=invocation.cache_revision,
        )
        try:
            return executor.execute(invocation).outputs
        finally:
            self._provenance = executor.provenance
