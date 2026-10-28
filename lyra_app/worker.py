"""RQ entry points for executing metric jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from lyra.sdk.db import LyraDB
from lyra.sdk.errors import MetricInputError, MetricResultError
from lyra.sdk.execution import GraphExecutionError, GraphExecutor
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.models.job import (
    FailedJobResult,
    JobEnvelope,
    JobProgress,
    JobRunProvenance,
    TableJobResult,
)
from lyra.sdk.models.plugin import NodeManifest, PluginManifest
from lyra.sdk.plugin import PluginDefinition
from lyra.sdk.plugin_loader import load_validated_plugin_definition
from lyra.sdk.results import serialize_outputs
from lyra.sdk.types import JsonObject
from redis.exceptions import RedisError
from rq import get_current_job
from rq.job import Job

from lyra_app.config import JobsConfig, LyraConfig, WorkerCredentials
from lyra_app.db import connection as database_connection
from lyra_app.db.client import LyraDBImplicit
from lyra_app.db.connection import is_database_unavailable_error
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
    definition: PluginDefinition
    graph: PluginGraph
    implementations: dict[str, PluginDefinition]


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
    """Provide operational services without a database client."""

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


@dataclass
class WorkerDatasetContext(WorkerRunContext):
    """Provide dataset acquisition services in addition to operational services."""

    db: LyraDB


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
    graph = build_graph({p.distribution: p.manifest.nodes for p in plugins})
    selected = {
        metric.name: (plugin, metric, plugin.metric_queues[metric.name])
        for plugin in plugins
        for metric in plugin.manifest.nodes
        if metric.role == "metric" and plugin.metric_queues[metric.name] in queues
    }
    required: set[str] = set()
    pending = list(selected)
    while pending:
        name = pending.pop()
        if name in required:
            continue
        required.add(name)
        pending.extend(
            edge.output.producer.node.name for edge in graph.dependencies[name].values()
        )
    distributions = {graph.nodes[name].distribution for name in required}
    implementations = {
        plugin.distribution: load_validated_plugin_definition(plugin.manifest)
        for plugin in plugins
        if plugin.distribution in distributions
    }
    return {
        name: RunnerMetricEntry(
            metric_name=name,
            contract=metric,
            plugin=plugin.manifest,
            distribution=plugin.distribution,
            queue=queue,
            definition=implementations[plugin.distribution],
            graph=graph,
            implementations=implementations,
        )
        for name, (plugin, metric, queue) in selected.items()
    }


def refresh_runner_registry(
    worker_name: str,
    *,
    config: LyraConfig,
    credentials: WorkerCredentials,
) -> dict[str, RunnerMetricEntry]:
    """Replace the process runner registry for one configured worker pool.

    Returns:
        A copy of the newly loaded runner registry.
    """
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _WORKER_CONFIG.config = config.model_copy(deep=True)
    _WORKER_CONFIG.credentials = credentials
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
    job: JobEnvelope,
    *,
    node: str | None = None,
    progress: WorkerProgress | None = None,
) -> WorkerRunContext:
    """Build operational services for a node within a job.

    Pass the same progress reporter for each invocation in a graph job.

    Returns:
        A context with node identity and job-scoped scratch storage.
    """
    node = job.metric if node is None else node
    temp_dir = _runner_temp_base() / _safe_path_segment(job.job_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return WorkerRunContext(
        job_id=job.job_id,
        node=node,
        logger=logging.LoggerAdapter(
            logging.getLogger(f"{__name__}.{node}"),
            {"structured_fields": {"job_id": job.job_id, "node": node}},
        ),
        temp_dir=temp_dir,
        progress=progress
        if progress is not None
        else WorkerProgress(job.job_id, require_worker_config().jobs),
    )


def build_dataset_context(
    job: JobEnvelope,
    *,
    node: str,
    progress: WorkerProgress | None = None,
) -> WorkerDatasetContext:
    """Build database and operational services for a dataset invocation.

    Returns:
        A dataset context using the managed worker database engine.
    """
    context = build_run_context(job, node=node, progress=progress)
    return WorkerDatasetContext(
        job_id=context.job_id,
        node=context.node,
        logger=context.logger,
        temp_dir=context.temp_dir,
        progress=context.progress,
        db=_build_db_context(),
    )


def _build_db_context() -> LyraDB:
    return LyraDBImplicit(
        database_connection.get_worker_engine(
            require_worker_config(),
            password=require_worker_credentials().postgres_password,
        )
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
    elif is_database_unavailable_error(exc):
        error = {
            "type": "database_unavailable",
            "message": "The database is temporarily unavailable.",
            "retryable": True,
        }
    else:
        error = {"type": "worker", "message": "Job execution failed."}
    if graph_error is not None:
        error.update(node=graph_error.node, dependency_path=list(graph_error.path))
    elif isinstance(exc, MetricInputError | MetricResultError):
        error.update(node=exc.metric, dependency_path=[exc.metric])
    native = get_current_job()
    if native is not None:
        native.meta["failure"] = FailedJobResult(job_id=job_id, error=error).model_dump(
            mode="json"
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
    native.meta["worker_id"] = native.worker_name
    try:
        native.save_meta()
    except RedisError:
        logger.warning("Could not save worker identity for %s", native.id)
    try:
        return _execute_metric(envelope_payload, native)
    except Exception as exc:
        logger.exception("Graph job %s failed", native.id)
        _save_failure(native.id, exc)
        raise
    finally:
        database_connection.dispose_worker_engine()


def _execute_metric(envelope_payload: JsonObject, native: Job) -> JsonObject:
    job = JobEnvelope.model_validate(envelope_payload)
    entry = RUNNER_REGISTRY.get(job.metric)
    provenance = native.meta.get("provenance", {})
    if entry is None:
        msg = "Metric is unavailable in this worker installation."
        raise RuntimeError(msg)
    if (
        job.job_id != native.id
        or entry.queue != native.origin
        or entry.contract.model_dump(mode="json") != native.meta.get("contract")
        or entry.distribution != native.meta.get("distribution")
        or entry.plugin.plugin.model_dump(mode="json") != provenance.get("plugin")
    ):
        msg = "Submitted metric identity or contract differs from the worker."
        raise RuntimeError(msg)
    progress = WorkerProgress(job.job_id, require_worker_config().jobs)

    def context_factory(
        node: str, role: Literal["metric", "dataset"]
    ) -> WorkerRunContext:
        builder = build_dataset_context if role == "dataset" else build_run_context
        return builder(job, node=node, progress=progress)

    invocation = entry.definition.prepare_invocation(job.metric, job.input)
    executor = GraphExecutor(entry.graph, entry.implementations, context_factory)
    try:
        outputs = executor.execute(invocation)
    finally:
        progress.flush_progress()
    return TableJobResult.model_validate(
        {
            "job_id": job.job_id,
            "outputs": serialize_outputs(job.metric, outputs),
            "output_metadata": entry.contract.outputs,
            "provenance": JobRunProvenance.model_validate(provenance),
        }
    ).model_dump(mode="json")


@dataclass
class _WorkerConfiguration:
    config: LyraConfig | None = None
    credentials: WorkerCredentials | None = None


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


def require_worker_credentials() -> WorkerCredentials:
    """Return credentials bound during worker startup.

    Raises:
        RuntimeError: If worker startup has not bound credentials.
    """
    if _WORKER_CONFIG.credentials is None:
        message = "Worker credentials have not been initialized."
        raise RuntimeError(message)
    return _WORKER_CONFIG.credentials
