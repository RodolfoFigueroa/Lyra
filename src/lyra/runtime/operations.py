"""Completed operation mappings and managed acquisition of authoritative targets."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import pandas as pd

from lyra.contracts.locations import LocationContractReference, LocationSelection
from lyra.contracts.outputs import ColumnMetadata, OutputMetadata
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.invocation import BoundPlugin
from lyra.runtime.locations import LocationResolver
from lyra.runtime.native_inputs import NativeLocationInput


@dataclass(frozen=True)
class OperationResult:
    """Owned source groups, separate source scope, and unique target records."""

    groups: Mapping[str, tuple[str, ...]]
    source_scope: LocationContractReference
    targets: NativeLocationInput
    source: NativeLocationInput | None = None

    def __post_init__(self) -> None:
        """Detach mappings and scope from their producers."""
        object.__setattr__(self, "groups", MappingProxyType(dict(self.groups)))
        object.__setattr__(
            self, "source_scope", self.source_scope.model_copy(deep=True)
        )


def execute_operation(
    plugin: BoundPlugin,
    invocation: PreparedResolvedInvocation,
    resolver: LocationResolver,
    *,
    retained: Mapping[str, tuple[str, ...]] | None = None,
    target_use_id: str | None = None,
) -> OperationResult:
    """Invoke and acquire targets within the caller's execution resource scope.

    Returns:
        A successful operation's completed groups and authoritative targets.

    Raises:
        ValueError: If the invocation does not declare a fixed operation target.
    """
    node = plugin.node_manifest(invocation.node)
    if (
        node is None
        or node.kind != "location_operation"
        or node.target_provider is None
    ):
        message = "Operation invocation requires a fixed target provider"
        raise ValueError(message)
    source = next(iter(invocation.locations.values()))
    if node.has_target_selector and source.ids:
        target = resolver.providers[node.target_provider]
        expression = plugin.invoke_target_selector(invocation, target)
        if expression is None:
            message = "Nonempty operation sources require a selector expression"
            raise ValueError(message)
        if resolver.report is not None:
            resolver.report.observe_selector(invocation.bound_use)
        candidates = resolver.select(
            node.target_provider, expression, use_id=target_use_id
        )
        groups = plugin.invoke_operation(invocation, candidates)
    else:
        groups = plugin.invoke_operation(invocation)
    return acquire_operation_targets(
        {**(retained or {}), **groups},
        next(iter(invocation.locations.values())),
        node.target_provider,
        resolver,
        use_id=target_use_id,
    )


def acquire_operation_targets(
    groups: Mapping[str, tuple[str, ...]],
    source: NativeLocationInput,
    target_provider: str,
    resolver: LocationResolver,
    *,
    use_id: str | None = None,
) -> OperationResult:
    """Acquire distinct targets through the run's exact-ID boundary.

    Returns:
        Completed groups and validated records, including empty target scope.
    """
    resolver.retain(source)
    ids = sorted({target for targets in groups.values() for target in targets})
    targets = resolver.resolve(
        {"targets": LocationSelection(provider=target_provider, ids=ids)},
        use_ids={"targets": use_id},
    )["targets"]
    return OperationResult(groups, source.contract, targets, source)


def forward_correspondences(
    result: OperationResult, target_description: OutputMetadata
) -> NativeLocationInput:
    """Expand validated records by explicit pair IDs without provider revalidation.

    Returns:
        Native target records retaining immediate source relationships and scopes.

    Raises:
        ValueError: If structural names collide or mapping pairs are invalid.
    """
    targets = result.targets.frame
    if {"source_location_id", "row_key"} & set(targets.columns):
        message = "Target attributes collide with correspondence structural names"
        raise ValueError(message)
    pairs = [
        (source, target) for source, ids in result.groups.items() for target in ids
    ]
    if len(pairs) != len(set(pairs)) or any(
        not isinstance(source, str) or not isinstance(target, str)
        for source, target in pairs
    ):
        message = "Correspondence identities require unique non-null string pairs"
        raise ValueError(message)
    # Label selection preserves extension dtypes, geometry, and CRS even when empty.
    frame = (
        targets.set_index("location_id", drop=False)
        .loc[[target for _, target in pairs]]
        .reset_index(drop=True)
    )
    frame.insert(
        0,
        "source_location_id",
        pd.Series([source for source, _ in pairs], dtype=object),
    )
    description = target_description.model_copy(deep=True)
    # Arbitrary producer checks apply to authoritative records, before expansion.
    description.checks = []
    description.unique = ["source_location_id", "location_id"]
    for column in description.columns:
        column.unique = False
        column.checks = []
    description.columns.insert(
        0,
        ColumnMetadata(
            name="source_location_id",
            dtype="str",
            unit=None,
            description="Immediate source location ID",
            nullable=False,
        ),
    )
    return NativeLocationInput(
        frame,
        result.targets.contract,
        source_scope=result.source_scope,
        description=description,
        source=result.source,
    )
