"""Authenticated HTTP handoffs for MCP clients."""

from lyra.contracts.job_paths import job_path
from lyra.contracts.retrieval_paths import absolute_api_url
from lyra.server.mcp.models import LyraAPIHandoffOutput


def handoff(base_url: str, job_id: str, suffix: str) -> LyraAPIHandoffOutput:
    """Build authenticated access metadata without exposing credentials.

    Returns:
        An absolute URL preserving the configured base path.
    """
    path = f"{job_path(job_id)}/result/{suffix}"
    return LyraAPIHandoffOutput.model_validate(
        {
            "method": "GET",
            "url": absolute_api_url(base_url, path),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        }
    )
