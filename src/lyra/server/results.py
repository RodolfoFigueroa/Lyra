"""Shared retained-result access for HTTP and MCP."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from lyra.contracts.errors import (
    ReportNotFoundError,
    ResultAccessError,
    ResultNotFoundError,
)
from lyra.contracts.jobs import (
    FailedJobResult,
    JobId,
    TableJobResult,
    TerminalJobResult,
)
from lyra.contracts.references import validate_execution_id, validate_result_reference
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
    ResultRowPage,
)
from lyra.contracts.retrieval_paths import (
    absolute_api_url,
    report_download_path,
    result_description_download_path,
)
from lyra.data.inspection import summarize_outputs
from lyra.data.results import serialize_outputs
from lyra.runtime.results import ResultStore
from lyra.server.job_store import InvalidJobResultError
from lyra.server.result_projection import project_observation
from lyra.server.result_rows import RowPageBuilder, RowPageContext

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from pydantic import BaseModel

    from lyra.contracts.inspection import ResultInspection
    from lyra.server.job_store import JobObservation, JobStore
    from lyra.server.response_budget import ResponseBudget


_JOB_ID = TypeAdapter(JobId)


def inspect_description(
    description: ResultDescription,
    *,
    public_api_base_url: str,
    budget: ResponseBudget[BaseModel],
) -> ResultDescriptionInspection:
    """Return an intact complete description or fixed preview for this transport."""
    complete = CompleteResultDescription(description=description)
    preview = ResultDescriptionPreview(
        result_ref=description.result_ref,
        execution_id=description.execution_id,
        download_url=absolute_api_url(
            public_api_base_url,
            result_description_download_path(description.result_ref),
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
        execution_id=report.execution_id,
        metric=report.request.metric,
        status=report.status,
        download_url=absolute_api_url(
            public_api_base_url, report_download_path(report.execution_id)
        ),
    )
    return cast("ExecutionReportInspection", budget.choose(complete, preview))


class ResultService:
    """Combine native job metadata and the configured relational result store."""

    def __init__(self, jobs: JobStore, result_store_path: Path) -> None:
        """Bind application-owned storage dependencies."""
        self.jobs = jobs
        self.result_store_path = result_store_path

    def read_rows(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        """Read a bounded SQL page independently of jobs and complete tables.

        Returns:
            Encoded rows and an optional continuation cursor.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, output_name)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return RowPageBuilder(
                    store, context.public_api_base_url, context.budget
                ).build(result_ref, output_name, limit, cursor)
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, output_name) from error

    async def rows(
        self,
        result_ref: str,
        output_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        """Select and encode a page without blocking the event loop.

        Returns:
            Encoded rows and an optional continuation cursor.
        """
        return await asyncio.to_thread(
            self.read_rows,
            result_ref,
            output_name,
            limit,
            cursor,
            context=context,
        )

    def validate_table(self, result_ref: str, output_name: str) -> None:
        """Validate a table link without reading table values or report bodies.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, output_name)
        try:
            with ResultStore.open(self.result_store_path) as store:
                store.validate_table(result_ref, output_name)
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, output_name) from error

    def stream_table(self, result_ref: str, output_name: str) -> Generator[bytes]:
        """Yield generated Arrow bytes, closing the store on every exit.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, output_name)
        try:
            with (
                ResultStore.open(self.result_store_path) as store,
                closing(store.stream_table(result_ref, output_name)) as stream,
            ):
                yield from stream
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, output_name) from error

    def read_location_input_rows(
        self,
        result_ref: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        """Read an input page with the transport's complete encoded-response budget.

        Returns:
            Complete encoded rows and the continuation after the last returned row.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, input_name=input_name)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return RowPageBuilder(
                    store, context.public_api_base_url, context.budget
                ).build_location_input(result_ref, input_name, limit, cursor)
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, input_name=input_name) from error

    async def location_input_rows(
        self,
        result_ref: str,
        input_name: str,
        limit: int = 100,
        cursor: str | None = None,
        *,
        context: RowPageContext,
    ) -> ResultRowPage:
        """Select and encode an input page without blocking the event loop.

        Returns:
            Complete encoded rows and an optional continuation cursor.
        """
        return await asyncio.to_thread(
            self.read_location_input_rows,
            result_ref,
            input_name,
            limit,
            cursor,
            context=context,
        )

    def validate_location_input(self, result_ref: str, input_name: str) -> None:
        """Validate input lookup and availability before issuing a download link.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, input_name=input_name)
        try:
            with ResultStore.open(self.result_store_path) as store:
                store.validate_location_input(result_ref, input_name)
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, input_name=input_name) from error

    def stream_location_input(
        self, result_ref: str, input_name: str
    ) -> Generator[bytes]:
        """Yield generated input Arrow bytes, closing the store on every exit.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref, input_name=input_name)
        try:
            with (
                ResultStore.open(self.result_store_path) as store,
                closing(store.stream_location_input(result_ref, input_name)) as stream,
            ):
                yield from stream
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref, input_name=input_name) from error

    def read_description(self, result_ref: str) -> ResultDescription:
        """Return complete retained metadata independently of jobs and plugins.

        Raises:
            ResultNotFoundError: If the configured store does not exist yet.
        """
        validate_result_reference(result_ref)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return store.describe_result(result_ref)
        except FileNotFoundError as error:
            raise ResultNotFoundError(result_ref) from error

    async def describe(self, result_ref: str) -> ResultDescription:
        """Return metadata without blocking the event loop or loading tables/reports."""
        return await asyncio.to_thread(self.read_description, result_ref)

    def read_report(self, execution_id: str) -> ExecutionReport:
        """Return a complete retained report independently of jobs and plugins.

        Raises:
            ReportNotFoundError: If the configured store does not exist yet.
        """
        validate_execution_id(execution_id)
        try:
            with ResultStore.open(self.result_store_path) as store:
                return store.read_report(execution_id)
        except FileNotFoundError as error:
            raise ReportNotFoundError(execution_id) from error

    async def report(self, execution_id: str) -> ExecutionReport:
        """Return a report without blocking the event loop or loading tables."""
        return await asyncio.to_thread(self.read_report, execution_id)

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
                "result_ref": retained.metadata.result_ref,
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
