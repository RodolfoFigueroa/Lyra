"""Errors raised while defining, preparing, and validating plugin computations."""

from collections.abc import Sequence

from lyra.contracts.types import JsonObject


class UnresolvedLocationIDsError(ValueError):
    """Requested IDs could not be resolved within a collection and unit type.

    ids contains only offending IDs, for both partial and total misses. The
    calling resolver associates this scope and these IDs with its named input.
    """

    def __init__(
        self,
        collection: str,
        unit_type: str,
        ids: Sequence[str],
        *,
        input_name: str | None = None,
    ) -> None:
        """Retain a snapshot of the unresolved IDs and requested scope."""
        self.node: str | None = None
        self.input_name = input_name
        self.collection = collection
        self.unit_type = unit_type
        self.ids = tuple(ids)
        super().__init__(
            f"Unresolved location IDs for input {input_name!r} in "
            f"{collection!r}/{unit_type!r}: {self.ids!r}"
        )


class LocationAcquisitionError(RuntimeError):
    """Provider setup or acquisition failed independently of ID validity.

    Supply scope and requested IDs for batch failures. Setup failures may omit
    them; the resolver owns the active input context. Chain the underlying cause
    and keep credentials out of the message.
    """

    def __init__(
        self,
        message: str,
        *,
        collection: str | None = None,
        unit_type: str | None = None,
        ids: Sequence[str] = (),
        input_name: str | None = None,
    ) -> None:
        """Retain acquisition context without classifying IDs as unresolved."""
        self.node: str | None = None
        self.input_name = input_name
        self.collection = collection
        self.unit_type = unit_type
        self.ids = tuple(ids)
        super().__init__(message)


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


class LocationSelectionError(ValueError):
    """A named input is malformed or selects an undeclared scope."""

    def __init__(self, input_name: str, message: str, ids: Sequence[str] = ()) -> None:
        """Capture the input name and offending IDs without provider diagnostics."""
        self.node: str | None = None
        self.input_name = input_name
        self.ids = tuple(ids)
        super().__init__(f"Location input {input_name!r}: {message}; IDs: {self.ids!r}")


class LocationProviderContractError(RuntimeError):
    """A provider returned invalid features or inconsistent resolution metadata."""

    def __init__(self, input_name: str, message: str, ids: Sequence[str] = ()) -> None:
        """Capture the input name and IDs implicated in a provider violation."""
        self.node: str | None = None
        self.input_name = input_name
        self.ids = tuple(ids)
        super().__init__(f"Location input {input_name!r}: {message}; IDs: {self.ids!r}")


class ResourceConfigurationError(RuntimeError):
    """A resource could not be configured for execution."""

    input_name: str | None = None


class ResultAccessError(LookupError):
    """A retained result or execution report lookup failed."""


class _ResultLookupError(ResultAccessError):
    """A result lookup failed with its reference and output context."""

    def __init__(self, result_ref: object, output_name: str | None = None) -> None:
        """Capture the requested reference and optional output name."""
        self.result_ref = result_ref
        self.output_name = output_name
        super().__init__(
            f"{type(self).__name__}: {result_ref!r}, output={output_name!r}"
        )


class InvalidResultReferenceError(_ResultLookupError):
    """The supplied reference is malformed."""


class ResultNotFoundError(_ResultLookupError):
    """The reference or named output is not retained."""


class ResultIntegrityError(_ResultLookupError):
    """Required retained data or relationships are damaged."""


class _ReportLookupError(ResultAccessError):
    def __init__(self, execution_id: object) -> None:
        self.execution_id = execution_id
        super().__init__(f"{type(self).__name__}: {execution_id!r}")


class InvalidExecutionIDError(_ReportLookupError):
    """The supplied execution ID is malformed."""


class ReportNotFoundError(_ReportLookupError):
    """The execution has no retained successful report."""


class ReportIntegrityError(_ReportLookupError):
    """The report or its required relationships are damaged."""


class ExecutionPersistenceError(RuntimeError):
    """Required publication failed for an attempted execution."""

    def __init__(self, execution_id: object) -> None:
        """Capture the attempted ID; chain the underlying storage failure."""
        self.execution_id = execution_id
        super().__init__(f"Execution persistence failed: {execution_id!r}")
