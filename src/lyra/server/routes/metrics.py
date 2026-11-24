"""HTTP projections of the compiled public capability catalog."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from lyra.contracts.discovery import MetricInspection, MetricSummary
from lyra.server.runtime import CatalogDependency

router = APIRouter(tags=["Catalog"])


@router.get("/metrics")
async def list_metrics(
    response: Response,
    catalog: CatalogDependency,
    query: str = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MetricSummary]:
    """Search public metrics.

    Returns:
        A bounded page of compact summaries.
    """
    discovery = catalog.discovery
    response.headers["ETag"] = discovery.fingerprint
    page, _ = discovery.page(query, offset=offset, limit=limit)
    return page


@router.get("/metrics/{metric_name}")
async def get_metric(metric_name: str, catalog: CatalogDependency) -> MetricInspection:
    """Inspect a registered public metric.

    Returns:
        The selected metric's inputs, outputs, parameters, and bindings.

    Raises:
        HTTPException: If the public metric does not exist.
    """
    try:
        return catalog.discovery.inspect(metric_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Metric not found") from exc
