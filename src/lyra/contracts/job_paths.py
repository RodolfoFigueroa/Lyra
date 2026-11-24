"""HTTP paths for job resources."""

from lyra.contracts.references import validate_job_id


def job_path(job_id: str) -> str:
    """Return the relative path for a validated job identifier."""
    return f"jobs/{validate_job_id(job_id)}"
