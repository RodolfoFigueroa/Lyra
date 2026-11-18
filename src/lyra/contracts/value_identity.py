"""Canonical declared value identities, independent of execution and storage."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from lyra.contracts.origins import Origin
    from lyra.contracts.provider_scope import DatasetScope


def check_json_value(value: object, path: str = "value") -> None:
    """Reject Python values that JSON encoding would coerce or cannot represent.

    Raises:
        ValueError: If a value is not a finite JSON primitive or container.
    """
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            check_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            check_json_value(item, f"{path}.{key}")
        return
    message = f"{path}: expected a finite JSON value with string object keys"
    raise ValueError(message)


def value_key(payload: object) -> str:
    """Return the SHA-256 key of strictly supported compact JSON values."""
    check_json_value(payload, "identity")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ComputationIdentity:
    """Complete effective bindings for one metric or operation computation."""

    kind: Literal["metric", "operation"]
    node_id: str
    code_revision: str | None
    output_scope: Literal["location", "unpartitioned"]
    parameters: dict
    inputs: dict
    primary_input: str | None = None
    origins: frozenset[Origin] = frozenset()
    target: DatasetScope | None = None
    computation_scope: Literal["complete", "per_location"] = "complete"

    @property
    def payload(self) -> dict:
        """A descriptor with unordered origins normalized."""
        result = {
            "kind": self.kind,
            "implementation": [self.node_id, self.code_revision],
            "output_scope": self.output_scope,
            "computation_scope": self.computation_scope,
            "parameters": self.parameters,
            "inputs": self.inputs,
        }
        if self.primary_input is not None:
            result["primary_input"] = self.primary_input
        if self.kind == "operation":
            result["target"] = self.target.identity_payload if self.target else None
        else:
            result["origins"] = sorted(
                (
                    {"id": origin.id, "revision": origin.revision}
                    for origin in self.origins
                ),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
        return result

    @property
    def key(self) -> str:
        """The computation key itself is its stored-value key."""
        return value_key(self.payload)
