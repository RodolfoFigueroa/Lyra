"""HTTP projections of the compiled public capability catalog."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from lyra.contracts.discovery import FamilySummary, MetricInspection
from lyra.server.runtime import CatalogDependency

router = APIRouter(tags=["Catalog"])


@router.get("/metrics")
async def list_metrics(
    response: Response,
    catalog: CatalogDependency,
    query: str = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[FamilySummary]:
    """Search public families.

    Returns:
        A bounded page of compact summaries.
    """
    discovery = catalog.discovery
    response.headers["ETag"] = discovery.fingerprint
    return discovery.search(query, offset=offset, limit=limit)


@router.get("/metrics/{metric_name}")
async def get_metric(
    metric_name: str, catalog: CatalogDependency, edition: str | None = None
) -> MetricInspection:
    """Inspect a shared family or its concrete public edition.

    Returns:
        The shared or concrete executable contract.

    Raises:
        HTTPException: If the family or public edition does not exist.
    """
    try:
        return catalog.discovery.inspect(metric_name, edition=edition)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Metric or edition not found"
        ) from exc
