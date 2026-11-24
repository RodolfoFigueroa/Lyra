"""Service-free acceptance of native and inline intervention values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.contracts.interventions import (
    AcceptedIntervention,
    InterventionSpec,
)
from lyra.data.intervention_records import decode_additions, encode_validated_additions
from lyra.data.interventions import Intervention

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lyra.sdk.provider_schema import ProviderSchema

__all__ = ["Intervention", "accept_interventions"]


def accept_interventions(
    values: Sequence[Intervention | InterventionSpec] | None,
    schemas: Mapping[str, ProviderSchema],
) -> tuple[AcceptedIntervention, ...]:
    """Capture complete sets against supplied registrations without acquisition.

    Returns:
        Owned inline records and resolved schemas, preserving submitted order.

    Raises:
        ValueError: If a catalog is unknown or multiple sets target one catalog.
        TypeError: If a supplied set is not a supported native or JSON value.
    """
    if values is None:
        return ()
    accepted = []
    catalogs = set()
    for value in values:
        if not isinstance(value, Intervention | InterventionSpec):
            msg = "Expected Intervention or InterventionSpec values"
            raise TypeError(msg)
        if value.catalog in catalogs:
            msg = "Only one intervention set per catalog is allowed"
            raise ValueError(msg)
        catalogs.add(value.catalog)
        if value.catalog not in schemas:
            msg = f"Unknown intervention catalog: {value.catalog!r}"
            raise ValueError(msg)
        accepted.append(_accept(value, schemas[value.catalog]))
    return tuple(accepted)


def _accept(
    value: Intervention | InterventionSpec, schema: ProviderSchema
) -> AcceptedIntervention:
    if isinstance(value, Intervention):
        frame = value.add
    else:
        # Revalidate mutable nested JSON and reject internal schema fields at
        # the caller boundary before any frame construction.
        value = InterventionSpec.model_validate(value.model_dump())
        frame = decode_additions(value.add, schema.metadata) if value.add else None
    payload = {
        "id": value.id,
        "catalog": value.catalog,
        "revision": value.revision,
        "remove": value.remove,
        "addition_schema": schema.metadata,
    }
    if frame is not None and len(frame):
        validated = schema.validate(frame)
        payload["add"] = encode_validated_additions(validated, schema.metadata)
    return AcceptedIntervention.model_validate(payload)
