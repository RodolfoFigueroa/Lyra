"""HTTP endpoints for submitting and inspecting metric jobs."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from lyra.contracts.inspection import (
    MAX_INSPECTION_BYTES,
    ResultInspection,
    json_size,
    trim_inspection,
)
from lyra.contracts.jobs import (
    JobCreateRequest,
    JobCreateResponse,
    JobStatusInfo,
    TerminalJobResult,
)
from lyra.server.agent_auth import require_agent_key
from lyra.server.job_store import (
    InvalidJobResultError,
    JobObservation,
    JobStore,
    is_terminal_status,
)
from lyra.server.job_submission import (
    MetricPayloadValidationError,
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra.server.registry import Catalog
from lyra.server.results import ResultService, ResultUnavailableError
from lyra.server.runtime import (
    CatalogDependency,
    JobStoreDependency,
    ResultServiceDependency,
)

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
        )
    except UnknownMetricError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except MetricPayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
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
) -> JobCreateResponse:
    """Handle authenticated HTTP job submission.

    Returns:
        Independently queued job metadata.
    """
    return await create_job(request, catalog=catalog, jobs=jobs)


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


async def _result_observation(job_id: str, jobs: ResultService) -> JobObservation:
    try:
        observation = await jobs.observe_job(job_id)
    except ResultUnavailableError as exc:
        raise HTTPException(status_code=404, detail="Result unavailable") from exc
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
async def get_job_result(job_id: str, results: ResultServiceDependency) -> JSONResponse:
    """Return a complete terminal result or the current pending status.

    Raises:
        HTTPException: If the retained artifact is unavailable or invalid.
    """
    observation = await _result_observation(job_id, results)
    pending = _pending(observation)
    if pending is not None:
        return pending
    try:
        result = await results.result(observation)
    except ResultUnavailableError as exc:
        raise HTTPException(status_code=404, detail="Result unavailable") from exc
    except InvalidJobResultError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "invalid_job_result",
                "message": "Invalid stored job result.",
                "retryable": False,
            },
        ) from exc
    return JSONResponse(content=result.model_dump(mode="json"))


@router.get(
    "/jobs/{job_id}/result/inspection",
    response_model=ResultInspection,
    responses={202: {"model": ResultInspection}},
)
async def inspect_job_result(
    job_id: str, results: ResultServiceDependency
) -> JSONResponse:
    """Return saved summaries and previews within the inspection byte budget.

    Raises:
        HTTPException: If mandatory inspection fields exceed the byte limit.
    """
    observation = await _result_observation(job_id, results)
    payload = results.inspect(observation).model_dump(mode="json")
    while json_size(payload) > MAX_INSPECTION_BYTES and trim_inspection(payload):
        pass
    if json_size(payload) > MAX_INSPECTION_BYTES:
        raise HTTPException(
            status_code=500, detail={"code": "result_response_too_large"}
        )
    return JSONResponse(
        content=payload,
        status_code=200 if is_terminal_status(observation.snapshot.status) else 202,
    )


@router.get(
    "/jobs/{job_id}/result/download",
    response_model=TerminalJobResult,
    responses={202: {"model": JobStatusInfo}},
)
async def download_job_result(
    job_id: str, results: ResultServiceDependency
) -> JSONResponse:
    """Return the full result with an attachment header when complete."""
    response = await get_job_result(job_id, results)
    if response.status_code == 200:
        response.headers["Content-Disposition"] = 'attachment; filename="result.json"'
    return response
