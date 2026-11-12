"""Authenticated HTTP handoffs for MCP clients."""

from urllib.parse import urlsplit, urlunsplit

from lyra.contracts.job_paths import job_path
from lyra.server.mcp.models import LyraAPIHandoffOutput


def handoff(base_url: str, job_id: str, suffix: str) -> LyraAPIHandoffOutput:
    """Build authenticated access metadata without exposing credentials.

    Returns:
        An absolute URL preserving the configured base path.
    """
    base = urlsplit(base_url)
    path = f"{base.path.rstrip('/')}/{job_path(job_id)}/result/{suffix}"
    return LyraAPIHandoffOutput.model_validate(
        {
            "method": "GET",
            "url": urlunsplit((base.scheme, base.netloc, path, "", "")),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        }
    )
