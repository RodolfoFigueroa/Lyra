"""Shared retained-result access for HTTP and MCP."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from lyra.contracts.errors import ResultAccessError
from lyra.contracts.jobs import (
    FailedJobResult,
    JobId,
    TableJobResult,
    TerminalJobResult,
)
from lyra.data.inspection import summarize_outputs
from lyra.data.results import serialize_outputs
from lyra.runtime.results import ResultStore
from lyra.server.job_store import InvalidJobResultError
from lyra.server.result_projection import project_observation

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.contracts.inspection import ResultInspection
    from lyra.server.job_store import JobObservation, JobStore


_JOB_ID = TypeAdapter(JobId)


class ResultService:
    """Combine native job metadata and the configured relational result store."""

    def __init__(self, jobs: JobStore, result_store_path: Path) -> None:
        """Bind application-owned storage dependencies."""
        self.jobs = jobs
        self.result_store_path = result_store_path

    def read_observation(self, job_id: str) -> JobObservation | None:
        """Return retained metadata and a bounded inspection of native outputs.

        Raises:
            InvalidJobResultError: If required retained data is missing or invalid.
        """
        job_id = _JOB_ID.validate_python(job_id)
        observation = self.jobs.read_job(job_id)
        if observation is None or observation.snapshot.status != "succeeded":
            return observation
        if observation.result_ref is None:
            message = "Missing retained result reference"
            raise InvalidJobResultError(message)
        try:
            with ResultStore.open(self.result_store_path) as store:
                result = store.read_result(observation.result_ref)
            inspection = summarize_outputs(result.tables)
        except (ResultAccessError, OSError, ValueError) as error:
            message = "Invalid retained job result"
            raise InvalidJobResultError(message) from error
        if observation.execution_id != result.metadata.execution_id:
            message = "Retained execution differs from job result"
            raise InvalidJobResultError(message)
        provenance = observation.provenance
        if provenance is not None and (
            result.metadata.metric_name != provenance.accepted.plan.metric
            or {
                name: output.table_schema
                for name, output in result.metadata.outputs.items()
            }
            != provenance.accepted.plan.output_metadata
        ):
            message = "Retained result differs from accepted outputs"
            raise InvalidJobResultError(message)
        return replace(observation, retained_result=result, inspection=inspection)

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Return a retained observation without blocking the event loop."""
        return await asyncio.to_thread(self.read_observation, job_id)

    @staticmethod
    def inspect(observation: JobObservation) -> ResultInspection:
        """Return the shared bounded HTTP/MCP result projection."""
        return project_observation(observation.snapshot.job_id, observation)

    @staticmethod
    def read_result(observation: JobObservation) -> TerminalJobResult:
        """Return public JSON data from a retained result or structured failure.

        Raises:
            InvalidJobResultError: If required retained data is missing or invalid.
        """
        snapshot = observation.snapshot
        if snapshot.status == "failed":
            return FailedJobResult(
                job_id=snapshot.job_id,
                error=snapshot.error or {},
                provenance=observation.provenance,
            )
        retained = observation.retained_result
        if snapshot.status != "succeeded" or retained is None:
            message = "Missing retained job result"
            raise InvalidJobResultError(message)
        return TableJobResult.model_validate(
            {
                "job_id": snapshot.job_id,
                "execution_id": retained.metadata.execution_id,
                "outputs": serialize_outputs(
                    retained.metadata.metric_name, retained.tables
                ),
                "output_metadata": {
                    name: output.table_schema.model_copy(deep=True)
                    for name, output in retained.metadata.outputs.items()
                },
                "provenance": observation.provenance.model_copy(deep=True)
                if observation.provenance
                else None,
            }
        )

    async def result(self, observation: JobObservation) -> TerminalJobResult:
        """Return the full result using an offloaded result read."""
        return await asyncio.to_thread(self.read_result, observation)
