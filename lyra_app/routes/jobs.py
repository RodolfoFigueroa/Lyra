"""HTTP endpoints for submitting and inspecting metric jobs."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from lyra.sdk.models.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobStatusInfo,
    TerminalJobResult,
    parse_job_result,
    result_ref_for_job,
)

from lyra_app.agent_auth import require_agent_key
from lyra_app.db.connection import ApplicationDatabaseRuntime, DatabaseUnavailableError
from lyra_app.db.dependencies import DatabaseRuntimeDependency
from lyra_app.job_store import JobStore, is_terminal_status
from lyra_app.job_submission import (
    SubmissionUnavailableError,
    UnknownMetricError,
    submit_job,
)
from lyra_app.registry import Catalog, MetricPayloadValidationError
from lyra_app.routes.errors import database_unavailable_http_exception
from lyra_app.runtime import CatalogDependency, JobStoreDependency
from lyra_app.spatial_inputs import (
    SpatialInputResolutionUnavailableError,
    SpatialInputValidationError,
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


def _result_status_payload(
    snapshot: JobStatusInfo,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": snapshot.job_id,
        "status": snapshot.status,
        "updated_at": snapshot.updated_at.isoformat(),
        "result_ref": result_ref_for_job(snapshot.job_id),
        "detail": "Result is not available yet",
    }
    if snapshot.metric is not None:
        payload["metric"] = snapshot.metric
    if snapshot.error is not None:
        payload["error"] = snapshot.error
        payload["detail"] = "Job finished without a successful result"
    if snapshot.progress is not None:
        payload["progress"] = snapshot.progress.model_dump(
            mode="json",
            exclude_none=True,
        )
    return payload


async def create_job(
    request: JobCreateRequest,
    *,
    catalog: Catalog,
    jobs: JobStore,
    database: ApplicationDatabaseRuntime,
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
            database=database,
        )
    except UnknownMetricError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except MetricPayloadValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except SpatialInputValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except (DatabaseUnavailableError, SpatialInputResolutionUnavailableError) as exc:
        error = database_unavailable_http_exception(database.config)
        raise error from exc
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
    database: DatabaseRuntimeDependency,
) -> JobCreateResponse:
    """Handle authenticated HTTP job submission.

    Returns:
        Independently queued job metadata.
    """
    return await create_job(request, catalog=catalog, jobs=jobs, database=database)


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
    return JobStatusInfo.model_validate(snapshot.model_dump(mode="json"))


@router.get("/jobs/{job_id}/result", response_model=TerminalJobResult)
async def get_job_result(job_id: str, jobs: JobStoreDependency) -> JSONResponse:
    """Return the raw terminal result retained for a job.

    Returns:
        The validated terminal result as a JSON response.

    Raises:
        HTTPException: If Redis is unavailable or the result is not retained.
    """
    payload = await jobs.get_job_result_async(job_id)
    if payload is None:
        await _get_observed_job_status(job_id, jobs)
        payload = await jobs.get_job_result_async(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Result expired or not found")

    result = parse_job_result(payload)
    return JSONResponse(content=result.model_dump(mode="json"))


@router.get("/jobs/{job_id}/result/descriptor", response_model=None)
async def get_job_result_descriptor(
    job_id: str, jobs: JobStoreDependency
) -> JSONResponse:
    """Return result metadata or current non-success status for a job.

    Returns:
        A terminal descriptor, failure metadata, or an accepted running response.

    Raises:
        HTTPException: If Redis is unavailable or a successful result has expired.
    """
    descriptor = await jobs.get_job_result_descriptor_async(job_id)
    if descriptor is not None:
        return JSONResponse(
            content=descriptor.model_dump(mode="json"),
        )

    snapshot = await _get_observed_job_status(job_id, jobs)
    if snapshot is not None and is_terminal_status(snapshot.status):
        descriptor = await jobs.get_job_result_descriptor_async(job_id)
        if descriptor is not None:
            return JSONResponse(
                content=descriptor.model_dump(mode="json"),
            )
    if snapshot is None or snapshot.status == "succeeded":
        raise HTTPException(status_code=404, detail="Result expired or not found")

    status_code = (
        status.HTTP_200_OK
        if is_terminal_status(snapshot.status)
        else status.HTTP_202_ACCEPTED
    )
    return JSONResponse(
        content=_result_status_payload(snapshot),
        status_code=status_code,
    )


@router.get("/jobs/{job_id}/result/download", response_model=TerminalJobResult)
async def download_job_result(job_id: str, jobs: JobStoreDependency) -> JSONResponse:
    """Download the complete retained JSON result envelope.

    Returns:
        The same JSON envelope returned by result retrieval.
    """
    response = await get_job_result(job_id, jobs)
    response.headers["Content-Disposition"] = 'attachment; filename="result.json"'
    return response
