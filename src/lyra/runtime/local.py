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
from lyra.contracts.errors import ExecutionPersistenceError
from lyra.contracts.jobs import JobCreateRequest, JobProgress
from lyra.runtime.execution import GraphExecutionError, GraphExecutor
from lyra.runtime.native_inputs import copy_frame
from lyra.runtime.persistence import persistence_boundary
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.report_collection import ReportCollection
from lyra.runtime.results import RunMetadata, RunResult
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk.loading import load_installed_plugin_definition

if TYPE_CHECKING:
    from lyra.contracts.accepted import PreparedExecution
    from lyra.contracts.provenance import ExecutionProvenance
    from lyra.contracts.resources import ResourceConfig
    from lyra.data.interventions import Intervention
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
        result_store_path: Path | None = None,
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
            result_store_path,
        )

    @classmethod
    def from_definitions(
        cls,
        plugins: Mapping[str, PluginDefinition],
        *,
        context_factory: ContextFactory | None = None,
        resource_config: Mapping[str, ResourceConfig] | None = None,
        resource_overrides: Mapping[str, type[BaseIO[Any]]] | None = None,
        result_store_path: Path | None = None,
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
            result_store_path,
        )
        return runner

    def _initialize(
        self,
        plugins: Mapping[str, PluginDefinition],
        context_factory: ContextFactory | None,
        resource_config: Mapping[str, ResourceConfig] | None,
        resource_overrides: Mapping[str, type[BaseIO[Any]]] | None,
        result_store_path: Path | None,
    ) -> None:
        if not plugins:
            msg = "Supply at least one plugin with nonblank ownership keys."
            raise ValueError(msg)
        self._provenance: ExecutionProvenance | None = None
        self._store_path = (
            result_store_path
            if result_store_path is not None
            else Path(".lyra/results.sqlite3")
        ).resolve()
        self.catalog = compile_live_catalog(plugins)
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
        interventions: Sequence[Intervention] | None = None,
    ) -> RunResult:
        """Execute a metric, including private roots, using named ID selections.

        Required result/report storage failures propagate ExecutionPersistenceError.

        Returns:
            Native tables, detached metadata, and the eager complete report.
        """
        return self._run(metric, inputs, parameters, interventions, allow_private=True)

    def run(
        self,
        metric: str,
        *,
        inputs: Mapping[str, object] | None = None,
        parameters: Mapping[str, object] | None = None,
        interventions: Sequence[Intervention] | None = None,
    ) -> RunResult:
        """Execute a public metric using JSON selections and parameter values.

        Omitted mappings and Python None use empty mappings. Provider resolution
        and handlers share one resource scope, closed before returning.
        Required result/report storage failures propagate ExecutionPersistenceError.

        Returns:
            Native frames, detached metadata, and the eager complete report,
            after required values and final result/report publication are durable.
        """
        return self._run(metric, inputs, parameters, interventions, allow_private=False)

    def _run(
        self,
        metric: str,
        inputs: Mapping[str, object] | None,
        parameters: Mapping[str, object] | None,
        interventions: Sequence[Intervention] | None,
        *,
        allow_private: bool,
    ) -> RunResult:
        self._provenance = None
        try:
            request = JobCreateRequest.model_validate(
                {
                    "metric": metric,
                    "inputs": {} if inputs is None else inputs,
                    "parameters": {} if parameters is None else parameters,
                }
            )
        except (ValueError, TypeError) as exc:
            raise PreparationError(metric, "request", str(exc)) from exc
        invocation = prepare_execution(
            self.catalog,
            request,
            allow_private=allow_private,
            native_interventions=interventions,
        )
        return self._execute(invocation)

    def _execute(self, invocation: PreparedExecution) -> RunResult:
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
    ) -> RunResult:
        report = ReportCollection()
        with persistence_boundary(report.execution_id):
            store = SQLiteStore(self._store_path)
        with store:
            return self._execute_in_store(invocation, context_factory, store, report)

    def _execute_in_store(
        self,
        invocation: PreparedExecution,
        context_factory: ContextFactory,
        store: SQLiteStore,
        report: ReportCollection,
    ) -> RunResult:
        executor = GraphExecutor(
            self.catalog,
            context_factory,
            self._resource_config,
            self._resource_overrides,
            provider_records=ProviderRecords(store),
            retain_result=True,
        )
        try:
            outcome = executor.execute(invocation, report=report)
            metadata = RunMetadata.model_validate(
                outcome.metadata.model_dump()
                | {
                    "store_path": self._store_path,
                    "result_ref": outcome.result_ref,
                    "execution_id": report.execution_id,
                }
            )
            return RunResult(
                tables={
                    name: copy_frame(frame) for name, frame in outcome.outputs.items()
                },
                metadata=metadata,
                report=report.complete(metadata.result_ref).model_copy(deep=True),
            )
        except GraphExecutionError as error:
            cause = error.__cause__
            while isinstance(cause, GraphExecutionError):
                cause = cause.__cause__
            if isinstance(cause, ExecutionPersistenceError):
                raise cause from cause.__cause__
            raise
        finally:
            self._provenance = executor.provenance
