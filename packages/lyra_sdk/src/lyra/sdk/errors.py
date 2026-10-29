"""Errors raised while defining, preparing, and validating plugin computations."""

from lyra.sdk.types import JsonObject


class PluginDefinitionError(ValueError):
    """An authoring definition or schema uses an unsupported capability."""


class _MetricError(ValueError):
    def __init__(self, metric: str, path: str, message: str) -> None:
        self.metric = metric
        self.path = path
        self.message = message
        super().__init__(f"Metric {metric!r}, {path}: {message}")


class MetricInputError(_MetricError):
    """Submitted parameters fail structural or Python-only validation."""


class MetricResultError(_MetricError):
    """A native computation result violates the declared output contract."""

    def __init__(
        self,
        metric: str,
        path: str,
        message: str,
        *,
        failures: list[JsonObject] | None = None,
    ) -> None:
        """Retain bounded diagnostics; exception chaining preserves Pandera details."""
        entries = failures or [
            {"output": path, "column": None, "check": None, "message": message}
        ]
        self.failures = [
            {**entry, "message": str(entry.get("message", ""))[:1000]}
            for entry in entries[:20]
        ]
        self.truncated = (
            len(entries) > 20
            or len(message) > 2000
            or any(len(str(entry.get("message", ""))) > 1000 for entry in entries[:20])
        )
        super().__init__(metric, path, message[:2000])
