"""Shared retained-result access for HTTP and MCP."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from lyra.contracts.jobs import FailedJobResult, TableJobResult, TerminalJobResult
from lyra.data.results import serialize_outputs
from lyra.server.job_store import InvalidJobResultError, JobObservation, JobStore
from lyra.server.result_projection import project_observation

if TYPE_CHECKING:
    from lyra.contracts.inspection import ResultInspection
    from lyra.runtime.artifacts import ArtifactStore


class ResultUnavailableError(LookupError):
    """A completed job no longer has a retained output artifact."""


class ResultService:
    """Combine native job metadata and immutable filesystem outputs."""

    def __init__(self, jobs: JobStore, artifacts: ArtifactStore) -> None:
        """Bind application-owned storage dependencies."""
        self.jobs = jobs
        self.artifacts = artifacts

    def read_observation(self, job_id: str) -> JobObservation | None:
        """Return metadata and saved inspection without decoding output tables.

        Raises:
            ResultUnavailableError: If retained artifact data is missing.
            InvalidJobResultError: If retained artifact data is invalid.
        """
        observation = self.jobs.read_job(job_id)
        if observation is None or observation.snapshot.status != "succeeded":
            return observation
        if observation.artifact_id is None:
            raise ResultUnavailableError(job_id)
        try:
            return replace(
                observation, inspection=self.artifacts.inspect(observation.artifact_id)
            )
        except FileNotFoundError as exc:
            raise ResultUnavailableError(job_id) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidJobResultError(str(exc)) from exc

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Return a retained observation without blocking the event loop."""
        return await asyncio.to_thread(self.read_observation, job_id)

    @staticmethod
    def inspect(observation: JobObservation) -> ResultInspection:
        """Return the shared bounded HTTP/MCP result projection."""
        return project_observation(observation.snapshot.job_id, observation)

    def read_result(self, observation: JobObservation) -> TerminalJobResult:
        """Return public JSON data from a retained artifact or structured failure.

        Raises:
            ResultUnavailableError: If retained artifact data is missing.
            InvalidJobResultError: If retained artifact data is invalid.
        """
        snapshot = observation.snapshot
        if snapshot.status == "failed":
            return FailedJobResult(
                job_id=snapshot.job_id,
                error=snapshot.error or {},
                provenance=observation.provenance,
            )
        if observation.artifact_id is None or observation.provenance is None:
            raise ResultUnavailableError(snapshot.job_id)
        try:
            outputs = self.artifacts.read(observation.artifact_id)
            return TableJobResult.model_validate(
                {
                    "job_id": snapshot.job_id,
                    "outputs": serialize_outputs(
                        observation.provenance.accepted.plan.node, outputs
                    ),
                    "output_metadata": (
                        observation.provenance.accepted.plan.output_metadata
                    ),
                    "provenance": observation.provenance,
                }
            )
        except FileNotFoundError as exc:
            raise ResultUnavailableError(snapshot.job_id) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise InvalidJobResultError(str(exc)) from exc

    async def result(self, observation: JobObservation) -> TerminalJobResult:
        """Return the full result using an offloaded artifact read."""
        return await asyncio.to_thread(self.read_result, observation)
