"""Job validation, preparation, and submission workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, Unpack
from uuid import uuid4

from lyra.sdk.arguments import PreparedResolvedInvocation, serialize_resolved_input
from lyra.sdk.errors import (
    LocationAcquisitionError,
    LocationProviderContractError,
    LocationSelectionError,
    MetricInputError,
    UnresolvedLocationIDsError,
)
from lyra.sdk.location_resolution import resolve_locations
from lyra.sdk.models.job import (
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobLinks,
    JobRunProvenance,
)
from lyra.sdk.models.location import LocationSelection
from lyra.sdk.models.plugin import PluginInfo
from lyra.sdk.plugin_loader import load_validated_plugin_definition
from lyra.sdk.resources import ResourceConfig, ResourceConfigurationError
from redis.exceptions import RedisError

from lyra_app.registry import (
    Catalog,
    MetricPayloadValidationError,
    validate_metric_entry_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lyra.sdk.types import JsonObject

    from lyra_app.job_store import JobStore
    from lyra_app.registry import MetricRegistryEntry


class BuildSubmissionOptions(TypedDict):
    """Identifier for persisted job records."""

    job_id: str


class UnknownMetricError(Exception):
    """Indicate that a submission names no metric in the active catalog."""

    def __init__(self, metric: str) -> None:
        """Initialize the error with the unknown metric name."""
        self.metric = metric
        super().__init__(f"Unknown metric: {metric}")


class SubmissionUnavailableError(Exception):
    """Indicate that submission cannot proceed because Redis is unavailable."""

    def __init__(self) -> None:
        """Initialize the standard temporary-unavailability error."""
        super().__init__(
            "Submission could not be confirmed. "
            "The job may have been accepted; "
            "another submission may duplicate it."
        )


def job_links(job_id: str) -> JobLinks:
    """Build relative API links for a job and its event and result resources.

    Returns:
        The canonical link set rooted at the job resource.
    """
    base = f"/jobs/{job_id}"
    return JobLinks(self=base, result=f"{base}/result")


def _build_submission_records(
    request: JobCreateRequest,
    entry: MetricRegistryEntry,
    validated_input: JsonObject,
    resolution: JsonObject,
    **options: Unpack[BuildSubmissionOptions],
) -> tuple[JobEnvelope, JobRunProvenance]:
    job_id = options["job_id"]
    envelope = JobEnvelope(
        job_id=job_id,
        metric=request.metric,
        input=resolution,
    )
    provenance = JobRunProvenance(
        metric=request.metric,
        catalog_fingerprint=entry.catalog_fingerprint,
        plugin=PluginInfo(name=entry.plugin_name, version=entry.plugin_version),
        input=validated_input,
        outputs=entry.metric.outputs,
        created_at=datetime.now(UTC),
    )
    return envelope, provenance


async def submit_job(
    request: JobCreateRequest,
    *,
    catalog: Catalog,
    jobs: JobStore,
    resource_config: Mapping[str, ResourceConfig] | None = None,
    job_id_factory: Callable[[], str] | None = None,
) -> JobCreateResponse:
    """Validate and resolve inputs before independently enqueueing one job.

    Returns:
        The accepted job identifier and public links.

    Raises:
        MetricPayloadValidationError: If selections or parameters are invalid.
        LocationResolutionUnavailableError: If the provider cannot resolve inputs.
        UnknownMetricError: If the metric is absent from the installed catalog.
        SubmissionUnavailableError: If enqueueing fails or its outcome is uncertain.
    """
    entry = catalog.get_metric_entry(request.metric)
    if entry is None:
        raise UnknownMetricError(request.metric)
    validated_input = validate_metric_entry_payload(entry, request.input)
    try:
        resolution = await asyncio.to_thread(
            _resolve_submission, catalog, entry, validated_input, resource_config
        )
    except (LocationSelectionError, UnresolvedLocationIDsError) as exc:
        raise MetricPayloadValidationError(
            [
                {
                    "loc": [exc.input_name],
                    "msg": str(exc),
                    "type": "location_selection",
                    "ids": list(exc.ids),
                }
            ]
        ) from exc
    except MetricInputError as exc:
        raise MetricPayloadValidationError(
            [{"loc": [exc.path], "msg": str(exc), "type": "value_error"}]
        ) from exc
    except (
        LocationAcquisitionError,
        LocationProviderContractError,
        ResourceConfigurationError,
    ) as exc:
        raise LocationResolutionUnavailableError(
            getattr(exc, "input_name", None)
        ) from exc
    factory = job_id_factory or (lambda: uuid4().hex)
    job_id = factory()
    envelope, provenance = _build_submission_records(
        request,
        entry,
        validated_input,
        resolution,
        job_id=job_id,
    )
    try:
        await asyncio.to_thread(jobs.enqueue, envelope, provenance, entry)
    except RedisError as exc:
        raise SubmissionUnavailableError from exc
    return JobCreateResponse(
        job_id=job_id, metric=request.metric, status="queued", links=job_links(job_id)
    )


class LocationResolutionUnavailableError(Exception):
    """Location provider availability or contract failure prevents acceptance."""

    def __init__(self, input_name: str | None = None) -> None:
        """Provide a safe provider-independent service diagnostic."""
        context = f" for input {input_name!r}" if input_name else ""
        super().__init__(
            f"Location provider resolution{context} is unavailable; "
            "no job was enqueued."
        )


def _resolve_submission(
    catalog: Catalog,
    entry: MetricRegistryEntry,
    payload: JsonObject,
    configuration: Mapping[str, ResourceConfig] | None,
) -> JsonObject:
    selections = {
        name: LocationSelection.model_validate(payload[name])
        for name in entry.metric.spatial_inputs
    }
    selected = catalog.location_provider
    provider_definition = None
    resources = {}
    if selections and selected is not None:
        try:
            provider_definition = load_validated_plugin_definition(
                catalog.manifests[selected.distribution]
            )
        except RuntimeError as exc:
            message = "Location provider implementation is unavailable"
            raise LocationAcquisitionError(message) from exc
        resources = provider_definition.resources
    definition = (
        provider_definition
        if provider_definition is not None
        and selected is not None
        and selected.distribution == entry.distribution
        else load_validated_plugin_definition(catalog.manifests[entry.distribution])
    )
    # Validate author parameter rules before acquiring any provider resources.
    parameters = (
        definition.prepare_parameters(entry.metric.name, payload["parameters"])
        if "parameters" in payload
        else None
    )
    resolved = resolve_locations(
        selections, selected=selected, resources=resources, configuration=configuration
    )
    return serialize_resolved_input(
        PreparedResolvedInvocation(entry.metric.name, parameters, resolved)
    )
