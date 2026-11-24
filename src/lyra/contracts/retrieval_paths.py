"""Explicit retained-resource routes relative to the public API base URL."""

from urllib.parse import urlencode, urlsplit, urlunsplit

from lyra.contracts.references import (
    validate_job_id,
    validate_retrieval_name,
)


def result_description_path(job_id: str) -> str:
    """Return the complete result description route."""
    return f"results/{validate_job_id(job_id)}"


def report_inspection_path(job_id: str) -> str:
    """Return the report inspection route for an execution."""
    identifier = validate_job_id(job_id)
    return f"executions/{identifier}/report/inspection"


def report_download_path(job_id: str) -> str:
    """Return the complete report download route for an execution."""
    identifier = validate_job_id(job_id)
    return f"executions/{identifier}/report/download"


def table_download_path(job_id: str, output_name: str) -> str:
    """Return the Arrow download route for a named output."""
    query = urlencode({"output_name": validate_retrieval_name(output_name)})
    return f"{result_description_path(job_id)}/tables/download?{query}"


def location_input_download_path(job_id: str, input_name: str) -> str:
    """Return the Arrow download route for a named root location input."""
    query = urlencode({"input_name": validate_retrieval_name(input_name)})
    return f"{result_description_path(job_id)}/location-inputs/download?{query}"


def absolute_api_url(base_url: str, path: str) -> str:
    """Return an absolute route preserving the configured public path prefix."""
    base = urlsplit(base_url)
    route = urlsplit(path)
    return urlunsplit(
        (
            base.scheme,
            base.netloc,
            f"{base.path.rstrip('/')}/{route.path.lstrip('/')}",
            route.query,
            "",
        )
    )
