"""FastAPI dependency providers for database access."""

from typing import Annotated

from fastapi import Depends, FastAPI, Request

from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.runtime import require_runtime


def require_database_runtime(app: FastAPI) -> ApplicationDatabaseRuntime:
    """Return the configured application database runtime.

    Returns:
        The database owned by the active application.
    """
    return require_runtime(app).database


def get_database_runtime(request: Request) -> ApplicationDatabaseRuntime:
    """Return the database runtime owned by the request's application."""
    return require_database_runtime(request.app)


DatabaseRuntimeDependency = Annotated[
    ApplicationDatabaseRuntime,
    Depends(get_database_runtime),
]
