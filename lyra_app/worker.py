"""RQ entry points for executing metric jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lyra.sdk.accepted import PreparedExecution
from lyra.sdk.cache import TableCache
from lyra.sdk.catalog import CompiledCatalog, compile_catalog
from lyra.sdk.computation import executable_contract
from lyra.sdk.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    MetricInputError,
    MetricResultError,
    UnresolvedLocationIDsError,
)
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import PluginGraph
from lyra.sdk.locations import SelectedLocationProvider
from lyra.sdk.models.job import (
    FailedJobResult,
    JobEnvelope,
    JobProgress,
    TableJobResult,
)
from lyra.sdk.models.plugin import NodeManifest, PluginManifest
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.plugin_loader import load_validated_plugin_definition
from lyra.sdk.resources import ResourceConfigurationError
from lyra.sdk.results import serialize_outputs
from lyra.sdk.types import JsonObject
from pydantic import TypeAdapter
from redis.exceptions import RedisError
from rq import get_current_job
from rq.job import Job

from lyra_app.config import JobsConfig, LyraConfig
from lyra_app.plugins import load_installed_plugins

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerMetricEntry:
    """Bundle a runner metric's queue, output contract, and callable."""

    metric_name: str
    queue: str
    contract: NodeManifest
    plugin: PluginManifest
    distribution: str
    catalog: CompiledCatalog
    implementations: dict[str, PluginDefinition]

    @property
    def graph(self) -> PluginGraph:
        """The worker's captured graph."""
        return self.catalog.graph

    @property
    def location_provider(self) -> SelectedLocationProvider | None:
        """The worker's captured provider."""
        return self.catalog.location_provider


@dataclass
class WorkerProgress:
    """Share job progress coalescing across node invocations."""

    job_id: str
    settings: JobsConfig
    _pending_progress: JobProgress | None = field(default=None, init=False, repr=False)
    _last_progress_emit: float | None = field(default=None, init=False, repr=False)

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Validate and coalesce optional progress snapshots."""
        self._pending_progress = JobProgress(
            timestamp=datetime.now(UTC),
            stage=stage,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )
        if self._last_progress_emit is None or (
            (time.monotonic() - self._last_progress_emit) * 1000
            >= self.settings.progress_min_interval_ms
        ):
            self.flush_progress()

    def flush_progress(self) -> None:
        """Best-effort publish at most once per configured interval."""
        now = time.monotonic()
        if self._last_progress_emit is not None and (
            (now - self._last_progress_emit) * 1000
            < self.settings.progress_min_interval_ms
        ):
            return
        if self._pending_progress is None:
            return
        self._last_progress_emit = now
        native = get_current_job()
        if native is not None:
            native.meta["progress"] = self._pending_progress.model_dump(mode="json")
            try:
                native.save_meta()
            except RedisError:
                logger.warning(
                    "Progress persistence failed for %s", self.job_id, exc_info=True
                )
        self._pending_progress = None


@dataclass
class WorkerRunContext:
    """Provide logging, progress, and scratch storage for execution."""

    job_id: str
    node: str
    logger: logging.Logger | logging.LoggerAdapter
    temp_dir: Path
    progress: WorkerProgress = field(repr=False)

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Publish a validated, coalesced job progress snapshot."""
        self.progress.report_progress(
            stage=stage, current=current, total=total, unit=unit, message=message
        )

    def flush_progress(self) -> None:
        """Publish pending progress subject to the job's throttle."""
        self.progress.flush_progress()


RUNNER_REGISTRY: dict[str, RunnerMetricEntry] = {}
_RUNNER_TEMP_BASE: Path | None = None


def set_runner_temp_base(path: Path | None) -> None:
    """Set or clear the process-wide parent directory for per-job scratch data."""
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _RUNNER_TEMP_BASE = path


def load_runner_metric_entries(
    worker_name: str,
    *,
    config: LyraConfig,
) -> dict[str, RunnerMetricEntry]:
    """Load executable metric entries assigned to one worker pool.

    Returns:
        A mapping from metric name to its validated runner contract and the
        transitive implementations required by its graph.
    """
    queues = set(config.get_worker(worker_name).queues)
    plugins = load_installed_plugins(config.plugins)
    catalog = compile_catalog({p.distribution: p.manifest for p in plugins})
    graph = catalog.graph
    selected = {
        metric.name: (plugin, metric, plugin.metric_queues[metric.name])
        for plugin in plugins
        for metric in catalog.manifests[plugin.distribution].nodes
        if metric.kind == "metric" and plugin.metric_queues[metric.name] in queues
    }
    # Preferences can change while accepted jobs still pin earlier routes.
    # Keep every canonical implementation available, including its dependencies.
    required = graph.dependency_closure(
        [*selected, *(edge.operation for edge in catalog.canonical_edges)]
    )
    distributions = {graph.nodes[name].distribution for name in required}
    location_provider = catalog.location_provider
    if location_provider is not None:
        distributions.add(location_provider.distribution)
    implementations = {
        plugin.distribution: load_validated_plugin_definition(
            catalog.manifests[plugin.distribution]
        )
        for plugin in plugins
        if plugin.distribution in distributions
    }
    return {
        name: RunnerMetricEntry(
            metric_name=name,
            contract=metric,
            plugin=catalog.manifests[plugin.distribution],
            distribution=plugin.distribution,
            queue=queue,
            catalog=catalog,
            implementations=implementations,
        )
        for name, (plugin, metric, queue) in selected.items()
    }


def refresh_runner_registry(
    worker_name: str,
    *,
    config: LyraConfig,
) -> dict[str, RunnerMetricEntry]:
    """Replace the process runner registry for one configured worker pool.

    Returns:
        A copy of the newly loaded runner registry.
    """
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _WORKER_CONFIG.config = config.model_copy(deep=True)
    registry = load_runner_metric_entries(worker_name, config=config)
    RUNNER_REGISTRY.clear()
    RUNNER_REGISTRY.update(registry)
    _RUNNER_TEMP_BASE = config.worker_temp_dir(worker_name)
    logger.info(
        "Loaded %d runner metric(s).",
        len(RUNNER_REGISTRY),
    )
    return dict(RUNNER_REGISTRY)


def _runner_temp_base() -> Path:
    if _RUNNER_TEMP_BASE is not None:
        return _RUNNER_TEMP_BASE

    msg = "Runner temp base is not configured. Start workers with worker_launcher."
    raise RuntimeError(msg)


def _safe_path_segment(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in value
    )


def build_run_context(
    job_id: str,
    *,
    node: str,
    progress: WorkerProgress | None = None,
) -> WorkerRunContext:
    """Build operational services for a node within a job.

    Pass the same progress reporter for each invocation in a graph job.

    Returns:
        A context with node identity and job-scoped scratch storage.
    """
    temp_dir = _runner_temp_base() / _safe_path_segment(job_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return WorkerRunContext(
        job_id=job_id,
        node=node,
        logger=logging.LoggerAdapter(
            logging.getLogger(f"{__name__}.{node}"),
            {"structured_fields": {"job_id": job_id, "node": node}},
        ),
        temp_dir=temp_dir,
        progress=progress
        if progress is not None
        else WorkerProgress(job_id, require_worker_config().jobs),
    )


def _save_failure(job_id: str, exc: Exception) -> None:
    error: JsonObject
    graph_error = exc if isinstance(exc, GraphExecutionError) else None
    if graph_error is not None and isinstance(graph_error.__cause__, Exception):
        exc = graph_error.__cause__
    if isinstance(exc, MetricInputError | MetricResultError):
        error = {
            "type": "invalid_input"
            if isinstance(exc, MetricInputError)
            else "invalid_result",
            "message": exc.message,
            "path": exc.path,
        }
    elif isinstance(
        exc,
        (
            LocationSelectionError,
            UnresolvedLocationIDsError,
            LocationAcquisitionError,
            LocationProviderContractError,
        ),
    ):
        error = {
            "type": "location_resolution",
            "message": "Location resolution failed.",
            "path": exc.input_name,
            "ids": list(exc.ids),
            "reason": type(exc).__name__,
        }
    elif isinstance(exc, BoundPlanError):
        error = {"type": "bound_plan", "message": str(exc)}
    elif isinstance(exc, ResourceConfigurationError):
        error = {
            "type": "resource_configuration",
            "message": "Execution resource configuration failed.",
            "path": exc.input_name,
        }
    else:
        error = {"type": "worker", "message": "Job execution failed."}
    if isinstance(exc, MetricResultError):
        error.update(failures=list(exc.failures), truncated=exc.truncated)
    if graph_error is not None:
        error.update(
            node=graph_error.node,
            node_kind=graph_error.kind,
            dependency_path=list(graph_error.path),
            receiving_input=graph_error.receiving_input,
        )
    elif isinstance(exc, MetricInputError | MetricResultError):
        error.update(node=exc.metric, dependency_path=[exc.metric])
    _persist_failure(job_id, error)


def _persist_failure(job_id: str, error: JsonObject) -> None:
    native = get_current_job()
    if native is not None:
        native.meta["failure"] = FailedJobResult(job_id=job_id, error=error).model_dump(
            mode="json", exclude={"provenance"}
        )
        try:
            native.save_meta()
        except RedisError:
            logger.warning(
                "Failure metadata persistence failed for %s", job_id, exc_info=True
            )


def run_metric_task(envelope_payload: JsonObject) -> JsonObject:
    """Execute the installed plugin and return an SDK-validated result to RQ.

    Returns:
        A JSON payload for RQ's native successful result storage.

    Raises:
        RuntimeError: Outside RQ or when the submitted plugin contract differs.
    """
    native = get_current_job()
    if native is None:
        msg = "Metric execution requires an RQ worker."
        raise RuntimeError(msg)
    native.meta.pop("execution", None)
    native.meta["worker_id"] = native.worker_name
    try:
        native.save_meta()
    except RedisError:
        logger.warning("Could not save worker identity for %s", native.id)
    try:
        return execute_job(envelope_payload, native)
    except Exception as exc:
        logger.exception("Graph job %s failed", native.id)
        _save_failure(native.id, exc)
        raise


def execute_job(envelope_payload: JsonObject, native: Job) -> JsonObject:
    """Execute the required accepted plan within one resource scope.

    Returns:
        The validated table result for native job storage.

    Raises:
        BoundPlanError: If the accepted job identity differs.
    """
    job = JobEnvelope.model_validate(envelope_payload)
    if job.job_id != native.id:
        message = "Accepted job identity differs from worker job"
        raise BoundPlanError(message)
    result = _execute_bound_job(job.accepted.plan, native)
    return TableJobResult.model_validate(result).model_dump(
        mode="json", exclude={"provenance"}
    )


@dataclass
class _WorkerConfiguration:
    config: LyraConfig | None = None


_WORKER_CONFIG = _WorkerConfiguration()


def require_worker_config() -> LyraConfig:
    """Return configuration bound during worker startup.

    Raises:
        RuntimeError: If worker startup has not bound configuration.
    """
    if _WORKER_CONFIG.config is None:
        message = "Worker configuration has not been initialized."
        raise RuntimeError(message)
    return _WORKER_CONFIG.config


class BoundPlanError(ValueError):
    """The worker cannot honor an accepted computation binding."""


def _execute_bound_job(plan: PreparedExecution, native: Job) -> JsonObject:
    """Execute persisted acceptance metadata without consulting route selection.

    This component is the worker boundary for durable acceptance wiring.

    Returns:
        Serialized outputs and current execution provenance.

    Raises:
        BoundPlanError: If this installation cannot honor the accepted computation.
    """
    entry = RUNNER_REGISTRY.get(plan.node)
    if entry is None or entry.queue != native.origin:
        message = "Accepted metric is unavailable in this worker pool"
        raise BoundPlanError(message)
    provider = entry.location_provider
    if provider is not None:
        definition = entry.implementations.get(provider.distribution)
        if (
            definition is None
            or definition.location_provider is None
            or executable_contract(definition.location_provider)
            != executable_contract(provider.declaration)
        ):
            message = (
                "Accepted location provider implementation differs or is unavailable"
            )
            raise BoundPlanError(message)
    progress = WorkerProgress(native.id, require_worker_config().jobs)
    cache_dir = require_worker_config().cache_dir
    executor = GraphExecutor(
        entry.catalog,
        entry.implementations,
        lambda node: build_run_context(native.id, node=node, progress=progress),
        require_worker_config().resources,
        cache=TableCache(cache_dir) if cache_dir is not None else None,
        cache_revision=plan.cache_revision,
    )
    try:
        try:
            executor.validate_plan(plan)
        except ValueError as exc:
            raise BoundPlanError(str(exc)) from exc
        if entry.contract.kind != "metric":
            message = "Accepted root is not a metric"
            raise BoundPlanError(message)
        outputs = executor.execute(plan)
        return {
            "job_id": native.id,
            "outputs": serialize_outputs(plan.node, outputs),
            "output_metadata": {
                name: value.model_dump(mode="json")
                for name, value in entry.contract.outputs.items()
            },
        }
    finally:
        progress.flush_progress()
        _capture_observations(native, executor)
        try:
            native.save_meta()
        except RedisError:
            logger.warning(
                "Provenance persistence failed for %s", native.id, exc_info=True
            )


def _capture_observations(native: Job, executor: GraphExecutor) -> None:
    if executor.provenance is not None:
        native.meta["execution"] = TypeAdapter(
            type(executor.provenance.execution)
        ).dump_python(executor.provenance.execution, mode="json")
