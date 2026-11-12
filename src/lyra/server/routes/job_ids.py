"""Route opaque job IDs before decoding their URL component."""

from urllib.parse import unquote

from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope
from typing_extensions import override


class JobIdRoute(APIRoute):
    """Preserve encoded separators in job IDs through framework routing."""

    @override
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        """Match the encoded path and decode only the captured job identifier.

        Returns:
            The framework match and child scope, including decoded path parameters.
        """
        if (
            scope["type"] != "http"
            or "job_id" not in self.param_convertors
            or "raw_path" not in scope
        ):
            return super().matches(scope)
        path = scope["raw_path"].decode("latin-1")
        root = scope.get("root_path", "")
        # Mount/proxy prefixes are decoded in root_path. Restore that prefix for
        # the framework, leaving the route-relative path percent encoded. Some
        # ASGI servers supply a raw_path that already excludes the proxy prefix.
        if root:
            for end in range(len(path) + 1):
                if (end == len(path) or path[end] == "/") and unquote(
                    path[:end]
                ) == root:
                    path = root + path[end:]
                    break
        match, child = super().matches({**scope, "path": path})
        if match != Match.NONE:
            child["path_params"] = {
                **child["path_params"],
                "job_id": unquote(child["path_params"]["job_id"]),
            }
        return match, child
