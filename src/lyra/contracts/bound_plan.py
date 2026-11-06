"""Persistence boundaries for the authoritative accepted-plan contract."""

from lyra.contracts.accepted import PreparedExecution


def serialize_bound_plan(plan: PreparedExecution) -> str:
    """Return JSON containing validated captured acceptance metadata."""
    return plan.model_dump_json()


def deserialize_bound_plan(payload: str) -> PreparedExecution:
    """Validate captured bindings without consulting a catalog or resources.

    Returns:
        The owned accepted plan.
    """
    return PreparedExecution.model_validate_json(payload)
