"""Metric registry construction and routing metadata management."""

import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from operator import itemgetter
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from lyra.sdk.client_contract import CLIENT_SCHEMA_VERSION, JSON_SCHEMA_DIALECT
from lyra.sdk.graph import PluginGraph, build_graph
from lyra.sdk.locations import (
    LocationCollection,
    SelectedLocationProvider,
    select_location_provider,
)
from lyra.sdk.models.metric import (
    MetricCatalogResponse,
    MetricInfo,
    build_metric_search_text,
)
from lyra.sdk.models.plugin import (
    NodeManifest,
    PluginManifest,
)
from lyra.sdk.types import JsonObject, JsonValue

from lyra_app.config import PluginsConfig
from lyra_app.plugins import InstalledPlugin, load_installed_plugins

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricRegistryEntry:
    """Bundle a canonical metric with validation, routing, and catalog metadata."""

    metric: NodeManifest
    plugin_name: str
    plugin_version: str
    request_schema: JsonObject
    request_validator: Validator
    queue: str
    distribution: str
    catalog_fingerprint: str


class MetricPayloadValidationError(Exception):
    """Report structured validation failures for a metric request payload."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        """Initialize the exception with API-compatible validation errors."""
        self.errors = errors
        super().__init__("metric payload validation failed")


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _normalised_manifest_payload(
    plugins: list[InstalledPlugin],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for plugin in plugins:
        data = plugin.manifest.model_dump(mode="json")
        data["distribution"] = plugin.distribution
        for metric in data["nodes"]:
            metric["queue"] = plugin.metric_queues[metric["name"]]
        data["nodes"] = sorted(data["nodes"], key=itemgetter("name"))
        payload.append(data)
    return sorted(payload, key=itemgetter("distribution"))


def _build_request_validator(metric_name: str, schema: JsonObject) -> Validator:
    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        msg = f"Metric {metric_name!r} has an invalid request_schema: {exc}"
        raise RuntimeError(msg) from exc
    return validator_class(schema)


def _build_registry(
    plugins: list[InstalledPlugin],
) -> dict[str, MetricRegistryEntry]:
    selected = select_location_provider(
        {p.distribution: p.manifest.location_provider for p in plugins}
    )
    catalog_fingerprint = public_catalog_fingerprint(
        [
            _metric_info_from_manifest(metric)
            for plugin in plugins
            for metric in plugin.manifest.nodes
        ],
        selected.declaration.collections if selected else (),
    )
    registry: dict[str, MetricRegistryEntry] = {}
    for plugin in plugins:
        manifest = plugin.manifest
        for metric in manifest.nodes:
            if metric.name in registry:
                msg = f"Duplicate metric name in plugin manifests: {metric.name!r}"
                raise RuntimeError(msg)
            try:
                queue = plugin.metric_queues[metric.name]
            except KeyError as exc:
                msg = f"Metric {metric.name!r} does not have a queue assignment."
                raise RuntimeError(msg) from exc
            request_schema = metric.request_schema
            registry[metric.name] = MetricRegistryEntry(
                metric=metric,
                plugin_name=manifest.plugin.name,
                plugin_version=manifest.plugin.version,
                request_schema=request_schema,
                request_validator=_build_request_validator(metric.name, request_schema),
                queue=queue,
                distribution=plugin.distribution,
                catalog_fingerprint=catalog_fingerprint,
            )
    return registry


def _public_metric_payload(metrics: list[MetricInfo]) -> list[dict[str, Any]]:
    return [
        metric.model_dump(mode="json")
        for metric in sorted(metrics, key=lambda item: item.name)
    ]


def public_catalog_fingerprint(
    metrics: list[MetricInfo], collections: tuple[LocationCollection, ...] = ()
) -> str:
    """Compute a stable fingerprint of the public metric catalog contract.

    Returns:
        The SHA-256 digest of the sorted public catalog payload.
    """
    return _fingerprint_payload(
        {
            "client_schema_version": CLIENT_SCHEMA_VERSION,
            "json_schema_dialect": JSON_SCHEMA_DIALECT,
            "metrics": _public_metric_payload(metrics),
            "location_collections": [
                c.model_dump(mode="json")
                for c in sorted(collections, key=lambda c: c.name)
            ],
        }
    )


def validate_metric_entry_payload(
    entry: MetricRegistryEntry,
    payload: JsonValue,
) -> JsonObject:
    """Validate a payload against one captured registry contract.

    Returns:
        A deep copy of the validated JSON object.

    Raises:
        MetricPayloadValidationError: If the value is not an object, violates the
            request schema.
    """
    if not isinstance(payload, dict):
        raise MetricPayloadValidationError(
            [{"loc": [], "msg": "Input must be a JSON object.", "type": "type"}],
        )

    errors = sorted(
        entry.request_validator.iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        raise MetricPayloadValidationError(
            [_format_validation_error(error) for error in errors]
        )
    return deepcopy(payload)


def _format_validation_error(error: JsonSchemaValidationError) -> dict[str, Any]:
    return {
        "loc": list(error.path),
        "msg": error.message,
        "type": str(error.validator),
    }


def _metric_info_from_entry(entry: MetricRegistryEntry) -> MetricInfo:
    return _metric_info_from_manifest(entry.metric)


def _metric_info_from_manifest(metric: NodeManifest) -> MetricInfo:
    return MetricInfo(
        name=metric.name,
        description=metric.description.strip(),
        request_schema=metric.request_schema,
        spatial_inputs=metric.spatial_inputs,
        outputs=metric.outputs,
    )


@dataclass
class Catalog:
    """Validated application-owned catalog and routing metadata."""

    entries: dict[str, MetricRegistryEntry]
    graph: PluginGraph
    fingerprint: str
    public_fingerprint: str
    location_provider: SelectedLocationProvider | None = None
    manifests: dict[str, PluginManifest] = field(default_factory=dict)

    @classmethod
    def load(cls, plugins: PluginsConfig) -> "Catalog":
        """Load manifests and validate the complete catalog before service startup.

        Returns:
            The validated catalog.
        """
        installed = load_installed_plugins(plugins)
        graph = build_graph({p.distribution: p.manifest.nodes for p in installed})
        entries = _build_registry(installed)
        selected = select_location_provider(
            {p.distribution: p.manifest.location_provider for p in installed}
        )
        return cls(
            entries,
            graph,
            _fingerprint_payload(_normalised_manifest_payload(installed)),
            public_catalog_fingerprint(
                [_metric_info_from_entry(entry) for entry in entries.values()],
                selected.declaration.collections if selected else (),
            ),
            selected,
            {p.distribution: p.manifest for p in installed},
        )

    def get_loaded_catalog_fingerprint(self) -> str:
        """Return the current internal fingerprint without loading the catalog."""
        return self.fingerprint

    def get_public_catalog_fingerprint(self) -> str:
        """Return the fingerprint of the currently exposed public metric catalog."""
        return self.public_fingerprint

    def get_loaded_metric_names(self) -> list[str]:
        """Return sorted metric names without triggering catalog initialization."""
        return sorted(self.entries)

    def get_loaded_metric_queues(self) -> dict[str, str]:
        """Return loaded metric-to-queue assignments without initializing state."""
        return {
            metric_name: entry.queue
            for metric_name, entry in sorted(self.entries.items())
        }

    def get_metric_entry(self, name: str) -> MetricRegistryEntry | None:
        """Return a metric entry from the validated catalog."""
        return self.entries.get(name)

    def get_metric_info(self, name: str) -> MetricInfo | None:
        """Return public metadata for a named metric when it exists."""
        entry = self.get_metric_entry(name)
        if entry is None:
            return None
        return _metric_info_from_entry(entry)

    def get_metrics_info(self) -> list[MetricInfo]:
        """Return public metadata for all registered metrics in name order."""
        return [
            _metric_info_from_entry(entry)
            for _name, entry in sorted(self.entries.items())
        ]

    def get_metric_search_text(self, name: str) -> str | None:
        """Build normalized discovery text for a registered metric.

        Returns:
            Searchable text for the metric, or ``None`` when it is unknown.
        """
        info = self.get_metric_info(name)
        if info is None:
            return None
        return build_metric_search_text(info)

    def get_metric_catalog(self) -> MetricCatalogResponse:
        """Build the complete versioned public metric catalog response.

        Returns:
            Catalog schema metadata, fingerprint, and all public metrics.
        """
        metrics = self.get_metrics_info()
        return MetricCatalogResponse(
            client_schema_version=CLIENT_SCHEMA_VERSION,
            json_schema_dialect=JSON_SCHEMA_DIALECT,
            catalog_fingerprint=self.public_fingerprint,
            metrics=metrics,
            location_collections=list(self.location_provider.declaration.collections)
            if self.location_provider
            else [],
        )

    def validate_metric_payload(
        self, metric_name: str, payload: JsonValue
    ) -> JsonObject:
        """Validate and defensively copy a request for a named metric.

        Returns:
            A deep copy of the validated JSON object.

        Raises:
            KeyError: If no registered metric has the requested name.
        """
        entry = self.get_metric_entry(metric_name)
        if entry is None:
            msg = f"Unknown metric: {metric_name!r}"
            raise KeyError(msg)
        return validate_metric_entry_payload(entry, payload)

    def get_plugin_graph(self) -> PluginGraph:
        """Return the validated plugin graph."""
        return self.graph
