"""Capture native changes before discovery and attach their public JSON actions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from importlib import import_module
from typing import TYPE_CHECKING

from lyra.contracts.interventions import (
    InterventionSpec,
    validate_intervention_catalogs,
)

if TYPE_CHECKING:
    from lyra.api.client.endpoints import RequestSpec
    from lyra.contracts.discovery import MetricInspection
    from lyra.contracts.jobs import JobCreateResponse
    from lyra.data.interventions import Intervention


def capture(values: Sequence[Intervention] | None) -> list[Intervention]:
    """Return independent frames and removal lists before the first I/O.

    Raises:
        TypeError: If the submission contains anything other than native sets.
    """
    if values is None:
        return []
    if not isinstance(values, Sequence) or isinstance(values, str | bytes | bytearray):
        msg = "Expected a sequence of Intervention values"
        raise TypeError(msg)
    if not values:
        return []
    native = import_module("lyra.data.interventions").Intervention
    if not all(isinstance(value, native) for value in values):
        msg = "Expected Intervention values"
        raise TypeError(msg)
    return [replace(value) for value in values]


def with_interventions(
    request: RequestSpec[JobCreateResponse],
    changes: Sequence[Intervention],
    discovery: MetricInspection | None,
) -> RequestSpec[JobCreateResponse]:
    """Return the ordinary job request carrying complete inline records.

    Raises:
        ValueError: If an additions frame names an undiscovered catalog.
    """
    sets = []
    for value in changes:
        actions = {
            "id": value.id,
            "catalog": value.catalog,
            "revision": value.revision,
            "remove": value.remove,
        }
        if value.add is not None:
            if discovery is None or value.catalog not in discovery.providers:
                msg = f"Unknown intervention catalog: {value.catalog!r}"
                raise ValueError(msg)
            schema = discovery.providers[value.catalog].record_schema
            codec = import_module("lyra.data.intervention_records")
            actions["add"] = codec.encode_additions(value.add, schema)
        sets.append(InterventionSpec.model_validate(actions))
    return replace(
        request,
        json_body={
            **(request.json_body or {}),
            "interventions": [
                value.model_dump() for value in validate_intervention_catalogs(sets)
            ],
        },
    )
