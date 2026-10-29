"""HTTP endpoints for submitting and inspecting metric jobs."""

from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from lyra.sdk.models.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobStatusInfo,
    ResultDescriptor,
    TerminalJobResult,
    build_result_descriptor,
)
from lyra.sdk.resources import ResourceConfig

from lyra_app.agent_auth import require_agent_key
from lyra_app.job_store import (
    InvalidJobResultError,
    JobObservation,
    JobStore,
    is_terminal_status,
)
from lyra_app.job_submission import (
    LocationResolutionUnavailableError,
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra_app.registry import Catalog, MetricPayloadValidationError
from lyra_app.runtime import CatalogDependency, JobStoreDependency, RuntimeDependency

router = APIRouter(tags=["Jobs"], dependencies=[Depends(require_agent_key)])


async def _get_observed_job_status(
    job_id: str,
    jobs: JobStore,
) -> JobStatusInfo | None:
    snapshot = await jobs.get_job_status_async(job_id)
    if snapshot is None:
        return None
    return snapshot


async def create_job(
    request: JobCreateRequest,
    *,
    catalog: Catalog,
    jobs: JobStore,
    resource_config: Mapping[str, ResourceConfig] | None = None,
) -> JobCreateResponse:
    """Submit a validated job request and translate domain failures to HTTP errors.

    Returns:
        Independently queued job metadata.

    Raises:
        HTTPException: If the request, metric, capacity, or backing service prevents
            submission.
    """
    try:
        return await submit_job(
            request,
            catalog=catalog,
            jobs=jobs,
            resource_config=resource_config,
        )
    except UnknownMetricError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except MetricPayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except LocationResolutionUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "location_provider_unavailable",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except SubmissionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job_route(
    request: JobCreateRequest,
    catalog: CatalogDependency,
    jobs: JobStoreDependency,
    runtime: RuntimeDependency,
) -> JobCreateResponse:
    """Handle authenticated HTTP job submission.

    Returns:
        Independently queued job metadata.
    """
    return await create_job(
        request, catalog=catalog, jobs=jobs, resource_config=runtime.config.resources
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, jobs: JobStoreDependency) -> JobStatusInfo:
    """Return the latest observed status for a retained job.

    Returns:
        The current lifecycle, progress, and error metadata.

    Raises:
        HTTPException: If Redis is unavailable or the job is no longer retained.
    """
    snapshot = await _get_observed_job_status(job_id, jobs)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    return snapshot


async def _result_observation(job_id: str, jobs: JobStore) -> JobObservation:
    try:
        observation = await jobs.observe_job(job_id)
    except InvalidJobResultError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "invalid_job_result",
                "message": "Invalid stored job result.",
                "retryable": False,
            },
        ) from exc
    if observation is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    if is_terminal_status(observation.snapshot.status) and observation.result is None:
        raise HTTPException(status_code=404, detail="Result unavailable")
    return observation


def _pending(observation: JobObservation) -> JSONResponse | None:
    if not is_terminal_status(observation.snapshot.status):
        return JSONResponse(
            status_code=202, content=observation.snapshot.model_dump(mode="json")
        )
    return None


@router.get(
    "/jobs/{job_id}/result",
    response_model=TerminalJobResult,
    responses={202: {"model": JobStatusInfo}},
)
async def get_job_result(job_id: str, jobs: JobStoreDependency) -> JSONResponse:
    """Return a terminal result or the current pending status.

    Raises:
        HTTPException: If the result is unavailable.
    """
    observation = await _result_observation(job_id, jobs)
    pending = _pending(observation)
    if pending is not None:
        return pending
    if observation.result is None:
        raise HTTPException(status_code=404, detail="Result unavailable")
    return JSONResponse(content=observation.result.model_dump(mode="json"))


@router.get(
    "/jobs/{job_id}/result/descriptor",
    response_model=ResultDescriptor,
    responses={202: {"model": JobStatusInfo}},
)
async def get_job_result_descriptor(
    job_id: str, jobs: JobStoreDependency
) -> JSONResponse:
    """Return a terminal descriptor or the current pending status.

    Raises:
        HTTPException: If the result is unavailable.
    """
    observation = await _result_observation(job_id, jobs)
    pending = _pending(observation)
    if pending is not None:
        return pending
    if observation.result is None:
        raise HTTPException(status_code=404, detail="Result unavailable")
    descriptor = build_result_descriptor(
        observation.result,
        completed_at=observation.snapshot.completed_at
        or observation.snapshot.updated_at,
        provenance=observation.provenance,
        lifetime=observation.lifetime,
    )
    return JSONResponse(content=descriptor.model_dump(mode="json"))


@router.get(
    "/jobs/{job_id}/result/download",
    response_model=TerminalJobResult,
    responses={202: {"model": JobStatusInfo}},
)
async def download_job_result(job_id: str, jobs: JobStoreDependency) -> JSONResponse:
    """Return a terminal download or the current pending status."""
    response = await get_job_result(job_id, jobs)
    if response.status_code == 200:
        response.headers["Content-Disposition"] = 'attachment; filename="result.json"'
    return response
