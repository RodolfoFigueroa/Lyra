"""Explicit application dependencies backed by isolated service doubles."""

from unittest.mock import AsyncMock, Mock

from lyra.sdk.artifacts import ArtifactStore

from lyra_app.config import LyraConfig
from lyra_app.job_store import JobStore
from lyra_app.registry import Catalog
from lyra_app.results import ResultService
from lyra_app.runtime import ApplicationRuntime
from tests.config_helpers import api_credentials


def make_runtime(
    config: LyraConfig, catalog: Catalog | None = None
) -> ApplicationRuntime:
    connection = Mock()
    asynchronous = Mock(aclose=AsyncMock(), ping=AsyncMock(return_value=True))
    jobs = JobStore(connection, config.jobs)
    return ApplicationRuntime(
        config=config,
        catalog=catalog if catalog is not None else Catalog.load(config.plugins),
        redis=connection,
        async_redis=asynchronous,
        jobs=jobs,
        results=ResultService(jobs, ArtifactStore(config.artifact_dir)),
        credentials=api_credentials(),
    )
