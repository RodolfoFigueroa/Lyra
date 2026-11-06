"""Authenticated HTTP handoffs for MCP clients."""

from urllib.parse import quote, urlsplit, urlunsplit

from lyra.server.mcp.models import LyraAPIHandoffOutput


def handoff(base_url: str, job_id: str, suffix: str) -> LyraAPIHandoffOutput:
    """Build authenticated access metadata without exposing credentials.

    Returns:
        An absolute URL preserving the configured base path.
    """
    base = urlsplit(base_url)
    path = f"{base.path.rstrip('/')}/jobs/{quote(job_id, safe='-._~')}/result/{suffix}"
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
