"""Explicit retained-resource routes relative to the public API base URL."""

from urllib.parse import quote, urlsplit, urlunsplit

from lyra.contracts.references import (
    validate_job_id,
    validate_retrieval_name,
)


def result_description_path(job_id: str) -> str:
    """Return the whole-result inspection route."""
    return f"results/{validate_job_id(job_id)}"


def result_description_download_path(job_id: str) -> str:
    """Return the complete description download route."""
    return f"{result_description_path(job_id)}/description/download"


def report_inspection_path(job_id: str) -> str:
    """Return the report inspection route for an execution."""
    identifier = validate_job_id(job_id)
    return f"executions/{identifier}/report/inspection"


def report_download_path(job_id: str) -> str:
    """Return the complete report download route for an execution."""
    identifier = validate_job_id(job_id)
    return f"executions/{identifier}/report/download"


def _table_path(job_id: str, output_name: str) -> str:
    reference = validate_job_id(job_id)
    segment = quote(validate_retrieval_name(output_name), safe="")
    if segment in {".", ".."}:
        segment = segment.replace(".", "%2E")
    return f"results/{reference}/tables/{segment}"


def table_download_path(job_id: str, output_name: str) -> str:
    """Return the Arrow download route for a named output."""
    return f"{_table_path(job_id, output_name)}/download"


def table_rows_path(job_id: str, output_name: str) -> str:
    """Return the JSON row-page route for a named output."""
    return f"{_table_path(job_id, output_name)}/rows"


def _location_input_path(job_id: str, input_name: str) -> str:
    reference = validate_job_id(job_id)
    segment = quote(validate_retrieval_name(input_name), safe="")
    if segment in {".", ".."}:
        segment = segment.replace(".", "%2E")
    return f"results/{reference}/location-inputs/{segment}"


def location_input_download_path(job_id: str, input_name: str) -> str:
    """Return the Arrow download route for a named root location input."""
    return f"{_location_input_path(job_id, input_name)}/download"


def location_input_rows_path(job_id: str, input_name: str) -> str:
    """Return the JSON page route for a named root location input."""
    return f"{_location_input_path(job_id, input_name)}/rows"


def absolute_api_url(base_url: str, path: str) -> str:
    """Return an absolute route preserving the configured public path prefix."""
    base = urlsplit(base_url)
    return urlunsplit(
        (
            base.scheme,
            base.netloc,
            f"{base.path.rstrip('/')}/{path.lstrip('/')}",
            "",
            "",
        )
    )
