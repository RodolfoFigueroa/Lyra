"""HTTP paths that preserve opaque job identifiers."""

from urllib.parse import quote

from pydantic import TypeAdapter

from lyra.contracts.jobs import JobId

_JOB_ID = TypeAdapter(JobId)


def job_path(job_id: str) -> str:
    """Return a relative job path with a validated, encoded identifier."""
    component = quote(_JOB_ID.validate_python(job_id), safe="")
    # URL clients normalize literal dot segments before sending requests.
    if component in {".", ".."}:
        component = component.replace(".", "%2E")
    return f"jobs/{component}"
