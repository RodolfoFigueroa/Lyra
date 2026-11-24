"""Capture execution-owned location selections at the metric retention boundary."""

from typing import TYPE_CHECKING

from lyra.contracts.outputs import forwarding_schema
from lyra.contracts.provider_scope import RecordReferences
from lyra.contracts.results import LocationInputDescription
from lyra.runtime.location_inputs import (
    InputBindings,
    InputSelection,
    LocationInputs,
    OperationSelection,
)
from lyra.runtime.provider_records import ProviderRecords, provider_registration
from lyra.runtime.sqlite_store import DamagedDataError

if TYPE_CHECKING:
    from lyra.runtime.arguments import PreparedResolvedInvocation
    from lyra.runtime.planning.bindings import BoundInvocation
    from lyra.runtime.planning.catalog import CompiledCatalog
    from lyra.runtime.sqlite_store import Header, SQLiteStore


def capture_inputs(
    store: "SQLiteStore | None",
    catalog: "CompiledCatalog",
    invocation: "PreparedResolvedInvocation",
    computations: tuple[tuple["Header", tuple[str, ...]], ...],
    *,
    enabled: bool,
) -> dict[str, InputBindings]:
    """Retain original values and share independent memberships across partitions.

    Returns:
        Input inventories keyed by the existing metric computation addresses.

    Raises:
        DamagedDataError: If forwarding metadata is absent.
    """
    if not enabled or store is None:
        return {}
    locations = invocation.locations
    primary_name = catalog.graph.nodes[invocation.node].node.primary_input
    primary = locations.get(primary_name or "")
    retained = LocationInputs(store)
    shared: dict[str, tuple[str, ...]] = {}
    grouped: dict[str, dict[str, tuple[str, ...]]] = {}
    names = []
    for name, value in locations.items():
        dataset = invocation.location_contract(name).provider_scope
        names.append(name)
        provider = catalog.provider_registry[value.contract.provider_id]
        ProviderRecords(store).publish(
            provider,
            value.frame.drop(
                columns=["source_location_id"], errors="ignore"
            ).drop_duplicates("location_id"),
            references=RecordReferences(value.contract.provider_scope, value.added_ids),
        )
        registration = provider_registration(provider)
        description = LocationInputDescription(
            kind="provider",
            provider_scope=dataset,
            table_schema=provider.schema.metadata,
            availability="available",
        )

        if value.source_scope is not None:
            if registration.key not in store.layout.descriptions:
                store.initialize((registration,))
            if value.description is None:
                msg = "Missing forwarding description"
                raise DamagedDataError(msg)
            description = LocationInputDescription(
                kind="correspondence",
                provider_scope=dataset,
                source_scope=value.source_scope.provider_scope,
                table_schema=value.description,
                availability="available",
            )
            registration = registration.forwarded()
        selection = InputSelection(description=description, registration=registration)

        bound = invocation.bound_use
        related = (
            bound.locations[name].related(bound.locations[primary_name]) is not None
            if bound is not None and primary_name is not None
            else primary is not None and value.related_selection(primary) is not None
        )
        if related and primary is not None and primary.ids:
            for group in dict.fromkeys(
                group for _, partitions in computations for group in partitions
            ):
                grouped.setdefault(group, {})[name] = (
                    _capture(
                        retained,
                        selection,
                        invocation,
                        name,
                        (primary_name, group),
                    ),
                )
        else:
            shared[name] = (
                _capture(retained, selection, invocation, name, (primary_name, None)),
            )
    return {
        header.value_key: InputBindings(
            names=tuple(names),
            selections=shared,
            groups={group: grouped[group] for group in partitions if group in grouped},
        )
        for header, partitions in computations
    }


def _related_ids(
    invocation: "PreparedResolvedInvocation",
    name: str,
    primary_name: str | None,
    group: str,
) -> tuple[str, ...]:
    """Select related direct records through runtime lineage.

    Returns:
        Provider IDs associated with the selected primary partition.
    """
    bound = invocation.bound_use
    if bound is not None:
        selected = bound.locations[name].related(
            bound.locations[primary_name or ""].select(frozenset({group}))
        )
        return tuple(sorted(selected.ids or ())) if selected is not None else ()
    native = invocation.locations[name].related_selection(
        invocation.locations[primary_name or ""].select_ids(frozenset({group}))
    )
    return tuple(sorted(native.ids)) if native is not None else ()


def _capture(
    retained: LocationInputs,
    selection: InputSelection,
    invocation: "PreparedResolvedInvocation",
    name: str,
    partition: tuple[str | None, str | None],
) -> str:
    """Return a durable reference to selected immediate membership."""
    primary_name, group = partition
    value = invocation.locations[name]
    if value.source_scope is None:
        records = (
            _related_ids(invocation, name, primary_name, group)
            if group is not None
            else tuple(sorted(value.ids))
        )
        return retained.publish(
            selection.model_copy(
                update={
                    "records": records,
                    "added_ids": tuple(sorted(value.added_ids.intersection(records))),
                }
            )
        )
    primary = invocation.locations.get(primary_name or "")
    selected = value
    if group is not None and primary is not None:
        selected = (
            value.related_selection(primary.select_ids(frozenset({group}))) or value
        )
    computations = selected.computations
    targets = tuple(sorted(selected.ids))
    selected_sources = set(selected.frame.source_location_id)
    bound = invocation.bound_use
    if bound is not None and group is not None and primary_name is not None:
        related = bound.locations[name].related(
            bound.locations[primary_name].select(frozenset({group}))
        )
        if related is not None:
            selected_sources = {source for source, _ in related.pairs}
            computations = related.computations
            targets = tuple(sorted(set(related.ids or ()) & set(value.ids)))
    return retained.publish(
        selection.model_copy(
            update={
                "operations": _operation_selections(
                    retained.store, computations, selected_sources, targets
                ),
                "added_ids": tuple(sorted(value.added_ids.intersection(targets))),
            }
        )
    )


def _operation_selections(
    store: "SQLiteStore",
    computations: tuple[tuple[str, str | None], ...],
    selected_sources: set[str],
    targets: tuple[str, ...],
) -> tuple[OperationSelection, ...]:
    references = {}
    for key, source in computations:
        references.setdefault(key, []).append(source)
    operations = []
    with store.engine.connect() as connection:
        for key, sources in references.items():
            header = store.header(connection, key, required=True)
            counts = header.output_rows["pairs"] if header.output_rows else {}
            if not isinstance(counts, dict):
                msg = "Missing operation completion"
                raise DamagedDataError(msg)
            operations.append(
                OperationSelection(
                    header=header,
                    sources=(
                        tuple(source for source in counts if source in selected_sources)
                        if None in sources and header.output_rows
                        else tuple(source for source in sources if source is not None)
                    ),
                    targets=targets,
                )
            )
    return tuple(operations)


def capture_skipped_inputs(
    store: "SQLiteStore",
    catalog: "CompiledCatalog",
    bound: "BoundInvocation",
    matching: dict[str, tuple[str, ...]],
) -> InputBindings:
    """Retain declaration-only inputs when no metric computation was produced.

    Returns:
        Durable known-empty or explicitly unavailable selections.
    """
    retained = LocationInputs(store)
    references = {}
    for name, value in bound.locations.items():
        if value.identity in matching:
            references[name] = matching[value.identity]
            continue
        provider = catalog.provider_registry[value.provider.provider_id]
        registration = provider_registration(provider)
        schema = provider.schema.metadata.model_copy(deep=True)
        source = value.source
        if source is None and value.upstream is not None:
            source = next(iter(value.upstream.locations.values()))
        derived = source is not None
        if derived:
            if registration.key not in store.layout.descriptions:
                store.initialize((registration,))
            registration = registration.forwarded()
            schema = forwarding_schema(schema)
        operations = []
        if value.computations:
            with store.engine.connect() as connection:
                for key, group in value.computations:
                    header = store.header(connection, key, required=True)
                    counts = header.output_rows["pairs"] if header.output_rows else {}
                    operations.append(
                        OperationSelection(
                            header=header,
                            sources=(group,)
                            if group is not None
                            else tuple(counts)
                            if isinstance(counts, dict)
                            else (),
                            targets=value.ids,
                        )
                    )
        # None is unresolved. A resolved empty membership can have no operation
        # computations when there were zero source partitions.
        available = value.ids == ()
        description = LocationInputDescription(
            kind="correspondence" if derived else "provider",
            provider_scope=value.provider.provider_scope,
            source_scope=source.provider.provider_scope if source else None,
            table_schema=schema,
            availability="available" if available else "not_materialized",
        )
        references[name] = (
            retained.publish(
                InputSelection(
                    description=description,
                    registration=registration,
                    operations=tuple(operations) if available else (),
                )
            ),
        )
    return InputBindings(names=tuple(references), selections=references)


def remember_inputs(
    invocation: "PreparedResolvedInvocation",
    captured: dict[str, InputBindings],
    matching: dict[str, tuple[str, ...]],
) -> None:
    """Remember only complete execution-owned bindings for skipped consumers."""
    if invocation.bound_use is None:
        return
    for name, binding in invocation.bound_use.locations.items():
        if set(binding.ids or ()) != set(invocation.locations[name].ids):
            continue
        native = invocation.locations[name]
        if native.source_scope is not None and set(binding.pairs) != set(
            native.frame[["source_location_id", "location_id"]].itertuples(
                index=False, name=None
            )
        ):
            continue
        references = tuple(
            dict.fromkeys(
                reference
                for inventory in captured.values()
                for reference in inventory.selected().selections.get(name, ())
            )
        )
        if references:
            matching[binding.identity] = references
