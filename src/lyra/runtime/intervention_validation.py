"""Complete intervention checks against the original provider datasets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.contracts.errors import LocationSelectionError
from lyra.contracts.reports import InterventionValidation
from lyra.runtime.persistence import persistence_boundary
from lyra.runtime.provider_acquisition import acquire_provider_membership

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from lyra.contracts.interventions import AcceptedIntervention
    from lyra.runtime.planning.provider_registry import RegisteredProvider
    from lyra.runtime.report_collection import ReportCollection
    from lyra.runtime.resources import ResourceScope
    from lyra.runtime.sqlite_store import SQLiteStore


def validate_interventions(
    interventions: Sequence[AcceptedIntervention],
    providers: Mapping[str, RegisteredProvider],
    resources: ResourceScope,
    store: SQLiteStore,
    report: ReportCollection,
) -> None:
    """Establish complete membership for every set before the graph can run.

    Raises:
        LocationSelectionError: If an addition exists or a removal is missing.
    """
    for change in interventions:
        provider = providers[change.catalog]
        scope = change.effective_scope(provider.scope)
        key = scope.validation_key
        with persistence_boundary(report.execution_id):
            validated = store.has_intervention_validation(key)
        if validated:
            report.intervention_validations.append(
                InterventionValidation(scope=scope, membership_lookup="skipped")
            )
            continue

        added = change.added_ids
        removed = set(change.remove)
        existing = acquire_provider_membership(
            provider, sorted(added | removed), resources
        )
        if collisions := added & existing:
            raise LocationSelectionError(
                change.catalog,
                "Added IDs already exist in the base dataset",
                sorted(collisions),
            )
        if missing := removed - existing:
            raise LocationSelectionError(
                change.catalog,
                "Removed IDs are missing from the base dataset",
                sorted(missing),
            )
        with persistence_boundary(report.execution_id):
            store.publish_intervention_validation(key)
        report.intervention_validations.append(
            InterventionValidation(scope=scope, membership_lookup="executed")
        )
