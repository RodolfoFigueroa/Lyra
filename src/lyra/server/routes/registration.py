"""HTTP route registration shared by the server and schema generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.server.routes import (
    admin,
    data_types,
    health,
    jobs,
    metrics,
    reports,
    results,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_http_routes(app: FastAPI) -> None:
    """Attach the HTTP routers in server dispatch order."""
    for router in (
        admin.router,
        health.router,
        jobs.router,
        data_types.router,
        metrics.router,
        results.router,
        reports.router,
    ):
        app.include_router(router)
