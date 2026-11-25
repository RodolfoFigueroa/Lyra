"""Shared retained-result access for HTTP and MCP."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from lyra.contracts.errors import (
    MetricResultError,
    ReportNotFoundError,
    ResultAccessError,
    ResultNotFoundError,
)
from lyra.contracts.jobs import (
    FailedJobResult,
    TableJobResult,
    TerminalJobResult,
)
from lyra.contracts.references import validate_job_id
from lyra.contracts.reports import (
    CompleteExecutionReport,
    ExecutionReport,
    ExecutionReportInspection,
    ExecutionReportPreview,
)
from lyra.contracts.results import (
    CompleteResultDescription,
    ResultDescription,
    ResultDescriptionInspection,
    ResultDescriptionPreview,
)
from lyra.contracts.retrieval_paths import (
    absolute_api_url,
    report_download_path,
    result_description_path,
)
from lyra.data.inspection import summarize_outputs
from lyra.data.results import serialize_outputs
from lyra.runtime.results import ResultStore
from lyra.server.job_store import InvalidJobResultError
from lyra.server.result_projection import project_observation

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import BaseModel

    from lyra.contracts.inspection import ResultInspection
    from lyra.contracts.outputs import OutputMetadata
    from lyra.data.results import NativeOutputs
    from lyra.server.job_store import JobObservation, JobStore
    from lyra.server.response_budget import ResponseBudget


def inspect_description(
    description: ResultDescription,
    *,
    public_api_base_url: str,
    budget: ResponseBudget[BaseModel],
) -> ResultDescriptionInspection:
    """Return an intact complete description or fixed preview for this transport."""
    complete = CompleteResultDescription(description=description)
    preview = ResultDescriptionPreview(
        job_id=description.job_id,
        download_url=absolute_api_url(
            public_api_base_url,
            result_description_path(description.job_id),
        ),
    )
    return cast("ResultDescriptionInspection", budget.choose(complete, preview))


def inspect_report(
    report: ExecutionReport,
    *,
    public_api_base_url: str,
    budget: ResponseBudget[BaseModel],
) -> ExecutionReportInspection:
    """Return the whole report or a fixed preview within the transport budget."""
    complete = CompleteExecutionReport(report=report)
    preview = ExecutionReportPreview(
        job_id=report.job_id,
        metric=report.request.metric,
        status=report.status,
        download_url=absolute_api_url(
            public_api_base_url, report_download_path(report.job_id)
        ),
    )
    return cast("ExecutionReportInspection", budget.choose(complete, preview))


class ResultService:
    """Combine native job metadata and the configured relational result store."""

    def __init__(self, jobs: JobStore, result_store_path: Path) -> None:
        """Bind application-owned storage dependencies."""
        self.jobs = jobs
        self.result_store_path = result_store_path

    def validate_table(self, job_id: str, output_name: str) -> None:
        """Validate the selected output metadata before issuing its link.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with ResultStore.open(self.result_store_path) as store:
                store.validate_table(job_id, output_name)
        except FileNotFoundError as error:
            raise ResultNotFoundError(job_id, output_name) from error

    def stream_table(self, job_id: str, output_name: str) -> Generator[bytes]:
        """Yield generated Arrow bytes, closing the store on every exit.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with (
                ResultStore.open(self.result_store_path) as store,
                closing(store.stream_table(job_id, output_name)) as stream,
            ):
                yield from stream
        except FileNotFoundError as error:
            raise ResultNotFoundError(job_id, output_name) from error

    def validate_location_input(self, job_id: str, input_name: str) -> None:
        """Validate input lookup and availability before issuing a download link.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with ResultStore.open(self.result_store_path) as store:
                store.validate_location_input(job_id, input_name)
        except FileNotFoundError as error:
            raise ResultNotFoundError(job_id, input_name=input_name) from error

    def stream_location_input(self, job_id: str, input_name: str) -> Generator[bytes]:
        """Yield generated input Arrow bytes, closing the store on every exit.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with (
                ResultStore.open(self.result_store_path) as store,
                closing(store.stream_location_input(job_id, input_name)) as stream,
            ):
                yield from stream
        except FileNotFoundError as error:
            raise ResultNotFoundError(job_id, input_name=input_name) from error

    def read_description(self, job_id: str) -> ResultDescription:
        """Return complete retained metadata independently of jobs and plugins.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return store.describe_result(job_id)
        except FileNotFoundError as error:
            raise ResultNotFoundError(job_id) from error

    async def describe(self, job_id: str) -> ResultDescription:
        """Return metadata without blocking the event loop or loading tables/reports."""
        return await asyncio.to_thread(self.read_description, job_id)

    def read_report(self, job_id: str) -> ExecutionReport:
        """Return a complete retained report independently of jobs and plugins.

        Raises:
            ReportNotFoundError: If the configured store does not exist yet.
        """
        validate_job_id(job_id)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return store.read_report(job_id)
        except FileNotFoundError as error:
            raise ReportNotFoundError(job_id) from error

    async def report(self, job_id: str) -> ExecutionReport:
        """Return a report without blocking the event loop or loading tables."""
        return await asyncio.to_thread(self.read_report, job_id)

    def read_observation(self, job_id: str) -> JobObservation | None:
        """Return the live or durable job observation after verifying its identity.

        Raises:
            InvalidJobResultError: If required retained data is missing or invalid.
        """
        job_id = validate_job_id(job_id)
        observation = self.jobs.read_job(job_id)
        if observation is not None and observation.snapshot.job_id != job_id:
            message = "Queue identity differs from requested job"
            raise InvalidJobResultError(message)
        return observation

    def _outputs(
        self, observation: JobObservation
    ) -> tuple[str, NativeOutputs, dict[str, OutputMetadata]]:
        snapshot = observation.snapshot
        metric = (
            observation.provenance.accepted.plan.metric
            if observation.provenance
            else snapshot.metric
        )
        if snapshot.status != "succeeded" or metric is None:
            msg = "Missing accepted metric or successful completion"
            raise InvalidJobResultError(msg)
        try:
            with ResultStore.open(self.result_store_path) as store:
                tables, declarations = store.job_outputs(snapshot.job_id, metric)
                return metric, tables, declarations
        except (ResultAccessError, OSError, ValueError) as error:
            message = "Invalid retained job result"
            raise InvalidJobResultError(message) from error

    async def observe_job(self, job_id: str) -> JobObservation | None:
        """Return a retained observation without blocking the event loop."""
        return await asyncio.to_thread(self.read_observation, job_id)

    def inspect(self, observation: JobObservation) -> ResultInspection:
        """Return the shared bounded HTTP/MCP result projection.

        Raises:
            InvalidJobResultError: If required retained values cannot be summarized.
        """
        if observation.snapshot.status == "succeeded":
            _, tables, declarations = self._outputs(observation)
            try:
                summaries = summarize_outputs(tables, declarations)
            except ValueError as error:
                msg = "Invalid retained job inspection"
                raise InvalidJobResultError(msg) from error
            observation = replace(
                observation, inspection=summaries, declarations=declarations
            )
        return project_observation(observation.snapshot.job_id, observation)

    def read_result(self, observation: JobObservation) -> TerminalJobResult:
        """Return public JSON data from a retained result or structured failure.

        Raises:
            InvalidJobResultError: If the retained outputs cannot be encoded.
        """
        snapshot = observation.snapshot
        if snapshot.status == "failed":
            return FailedJobResult(
                job_id=snapshot.job_id,
                error=snapshot.error or {},
                provenance=observation.provenance,
            )
        metric, tables, declarations = self._outputs(observation)
        try:
            outputs = serialize_outputs(metric, tables)
        except MetricResultError as error:
            msg = "Invalid retained job result"
            raise InvalidJobResultError(msg) from error
        return TableJobResult.model_validate(
            {
                "job_id": snapshot.job_id,
                "outputs": outputs,
                "output_metadata": declarations,
                "provenance": observation.provenance.model_copy(deep=True)
                if observation.provenance
                else None,
            }
        )

    async def result(self, observation: JobObservation) -> TerminalJobResult:
        """Return the full result using an offloaded result read."""
        return await asyncio.to_thread(self.read_result, observation)
