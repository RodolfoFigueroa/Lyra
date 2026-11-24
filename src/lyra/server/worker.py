"""RQ entry points for executing metric jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter
from redis.exceptions import RedisError
from rq import get_current_job
from rq.job import Job

from lyra.contracts.accepted import WorkerRequest
from lyra.contracts.config import JobsConfig, LyraConfig
from lyra.contracts.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    MetricInputError,
    MetricResultError,
    ResourceConfigurationError,
    UnresolvedLocationIDsError,
)
from lyra.contracts.jobs import (
    FailedJobResult,
    JobEnvelope,
    JobProgress,
)
from lyra.contracts.types import JsonObject
from lyra.runtime.execution import (
    GraphExecutionError,
    GraphExecutor,
    PlanValidationError,
)
from lyra.runtime.planning.computation import executable_contract
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.provider_records import ProviderRecords
from lyra.runtime.report_collection import ReportCollection
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.server.registry import Catalog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerRuntime:
    """One pool's catalog, queue assignments and execution settings."""

    catalog: Catalog
    queues: frozenset[str]
    config: LyraConfig


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


_WORKER_RUNTIME: WorkerRuntime | None = None
_RUNNER_TEMP_BASE: Path | None = None


def set_runner_temp_base(path: Path | None) -> None:
    """Set or clear the process-wide parent directory for per-job scratch data."""
    global _RUNNER_TEMP_BASE  # ruff:ignore[global-statement]

    _RUNNER_TEMP_BASE = path


def initialize_worker(worker_name: str, *, config: LyraConfig) -> WorkerRuntime:
    """Load all enabled plugins and bind a worker to its configured queues.

    Returns:
        The process runtime for subsequent jobs.
    """
    global _WORKER_RUNTIME  # ruff:ignore[global-statement]

    config = config.model_copy(deep=True)
    queues = frozenset(config.get_worker(worker_name).queues)
    catalog = Catalog.load(config.plugins, build_revision=config.build_revision)
    runtime = WorkerRuntime(catalog, queues, config)
    set_runner_temp_base(config.worker_temp_dir(worker_name))
    _WORKER_RUNTIME = runtime
    return runtime


def require_worker_runtime() -> WorkerRuntime:
    """Return the runtime initialized for this worker process.

    Raises:
        RuntimeError: If worker startup has not completed.
    """
    if _WORKER_RUNTIME is None:
        message = "Worker configuration has not been initialized."
        raise RuntimeError(message)
    return _WORKER_RUNTIME


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
    elif isinstance(exc, DeploymentChangedError):
        error = {"type": "deployment_changed", "message": str(exc)}
    elif isinstance(exc, BoundPlanError | PlanValidationError):
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
        The retained result reference for native job storage.

    Raises:
        BoundPlanError: If the accepted job identity differs.
        DeploymentChangedError: If the deployment changed since submission.
    """
    job = JobEnvelope.model_validate(envelope_payload)
    if job.job_id != native.id:
        message = "Accepted job identity differs from worker job"
        raise BoundPlanError(message)
    runtime = require_worker_runtime()
    if job.accepted.plan.build_revision != runtime.catalog.compiled.build_revision:
        message = "deployment_changed: resubmit against the current deployment"
        raise DeploymentChangedError(message)
    return _execute_bound_job(job.accepted.plan, native, runtime)


def require_worker_config() -> LyraConfig:
    """Return configuration bound during worker startup."""
    return require_worker_runtime().config


class DeploymentChangedError(ValueError):
    """The queued job belongs to another immutable deployment."""


class BoundPlanError(ValueError):
    """The worker cannot honor an accepted computation binding."""


def _execute_bound_job(
    plan: WorkerRequest, native: Job, runtime: WorkerRuntime
) -> JsonObject:
    """Prepare normalized values against the compatible live worker catalog.

    Returns:
        The retained result reference.

    Raises:
        BoundPlanError: If this installation cannot honor the accepted computation.
        RuntimeError: If execution did not publish a terminal result description.
    """
    queue = runtime.catalog.metric_queues.get(plan.metric)
    if queue not in runtime.queues or queue != native.origin:
        message = "Accepted metric is unavailable in this worker pool"
        raise BoundPlanError(message)
    try:
        live = prepare_execution(
            runtime.catalog.compiled,
            plan.request.submission_payload(),
            normalized_uses=plan.parameter_uses,
        )
    except ValueError as exc:
        raise BoundPlanError(str(exc)) from exc
    for name in sorted(plan.intervention_targets.keys() | live.intervention_targets):
        pinned = plan.intervention_targets.get(name)
        current = live.intervention_targets.get(name)
        if (
            pinned is None
            or current is None
            or executable_contract(pinned) != executable_contract(current)
        ):
            msg = f"Intervention target {name!r} differs from its pinned declaration"
            raise BoundPlanError(msg)
    if live.request.interventions != plan.request.interventions:
        msg = "Intervention differs from its pinned declaration"
        raise BoundPlanError(msg)
    if live.required_scopes() != plan.provider_scopes:
        msg = "Required provider scopes differ from acceptance"
        raise BoundPlanError(msg)
    if (
        live.has_parameters != plan.has_parameters
        or live.computation_identity != plan.computation_identity
    ):
        msg = "Selected computation differs from acceptance"
        raise BoundPlanError(msg)
    report = ReportCollection()
    progress = WorkerProgress(native.id, runtime.config.jobs)
    with SQLiteStore(runtime.config.result_store_path) as store:
        executor = GraphExecutor(
            runtime.catalog.compiled,
            lambda node: build_run_context(native.id, node=node, progress=progress),
            runtime.config.resources,
            provider_records=ProviderRecords(store),
            retain_result=True,
        )
        try:
            outcome = executor.execute(live, report=report)
            if outcome.result_ref is None:
                message = "Executor did not retain the terminal result"
                raise RuntimeError(message)
            return {
                "result_ref": outcome.result_ref,
                "execution_id": outcome.report_state.execution_id,
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
