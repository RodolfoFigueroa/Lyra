"""Explicit retained-resource routes relative to the public API base URL."""

from urllib.parse import quote, urlsplit, urlunsplit

from lyra.contracts.references import validate_execution_id, validate_result_reference


def result_description_path(result_ref: str) -> str:
    """Return the whole-result inspection route."""
    return f"results/{quote(validate_result_reference(result_ref), safe='')}"


def result_description_download_path(result_ref: str) -> str:
    """Return the complete description download route."""
    return f"{result_description_path(result_ref)}/description/download"


def report_inspection_path(execution_id: str) -> str:
    """Return the report inspection route for an execution."""
    identifier = quote(validate_execution_id(execution_id), safe="")
    return f"executions/{identifier}/report/inspection"


def report_download_path(execution_id: str) -> str:
    """Return the complete report download route for an execution."""
    identifier = quote(validate_execution_id(execution_id), safe="")
    return f"executions/{identifier}/report/download"


def _table_path(result_ref: str, output_name: str) -> str:
    reference = validate_result_reference(result_ref, output_name)
    segment = quote(output_name, safe="")
    if segment in {".", ".."}:
        segment = segment.replace(".", "%2E")
    return f"results/{quote(reference, safe='')}/tables/{segment}"


def table_download_path(result_ref: str, output_name: str) -> str:
    """Return the Arrow download route for a named output."""
    return f"{_table_path(result_ref, output_name)}/download"


def table_rows_path(result_ref: str, output_name: str) -> str:
    """Return the JSON row-page route for a named output."""
    return f"{_table_path(result_ref, output_name)}/rows"


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
