"""Transport-independent metric discovery over the live catalog."""

import hashlib
import json
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from lyra.contracts import discovery
from lyra.contracts.dependencies import describe_dependency
from lyra.contracts.metrics import normalize_metric_search_tokens
from lyra.contracts.outputs import forwarding_schema
from lyra.runtime.planning.catalog import CompiledCatalog
from lyra.runtime.planning.graph import parameter_contract

if TYPE_CHECKING:
    from lyra.contracts.plugins import MetricManifest


class Discovery:
    """Reuse deterministic projections without acquiring resources.

    Returned models are detached copies with schemas and source selections.
    """

    def __init__(self, catalog: CompiledCatalog) -> None:
        """Index public metrics from the authoritative catalog."""
        self.catalog = catalog
        self._summaries: dict[str, discovery.MetricSummary] = {}
        self._providers = {}
        for name, selected in catalog.providers.items():
            declared = selected.declaration
            schema = declared.contract.structure.output
            registered = catalog.provider_registry[name]
            self._providers[name] = discovery.ProviderDescription(
                scope=declared.contract.provider_scope,
                resource=declared.resource,
                record_schema=schema,
                crs=str(registered.schema.schema.columns["geometry"].dtype.crs),
                description=declared.description,
                cache=declared.cache,
                query_schema=declared.query_schema,
                query_capable=declared.query_capable,
                membership_capable=declared.membership_capable,
                accepted_sources=list(catalog.sources_by_target[name]),
            ).model_copy(deep=True)
        for name, entry in sorted(catalog.graph.nodes.items()):
            node = entry.node
            if node.kind != "metric" or not node.is_public_metric:
                continue
            self._summaries[name] = discovery.MetricSummary(
                name=name,
                description=node.description.strip(),
                output_granularity={
                    key: output.granularity for key, output in node.outputs.items()
                },
                accepted_selections={
                    key: list(sources)
                    for key, sources in catalog.accepted_sources[name].items()
                },
            )
        self._ordered = tuple(self._summaries.values())
        self._tokens = {
            summary.name: _summary_tokens(summary) for summary in self._ordered
        }
        self._details = {name: self._describe(name) for name in self._summaries}
        payload = {
            "providers": {
                name: value.model_dump(mode="json")
                for name, value in self._providers.items()
            },
            "metrics": [
                item.model_dump(mode="json", exclude={"providers"})
                for item in self._details.values()
            ],
        }
        self.fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    def search(
        self, query: str = "", *, offset: int = 0, limit: int = 50
    ) -> list[discovery.MetricSummary]:
        """Return detached summaries from the requested page."""
        return self.page(query, offset=offset, limit=limit)[0]

    def page(
        self, query: str = "", *, offset: int = 0, limit: int = 50
    ) -> tuple[list[discovery.MetricSummary], int]:
        """Return a detached page and the full public match count.

        Raises:
            ValueError: If pagination bounds are negative.
        """
        if offset < 0 or limit < 0:
            message = "Pagination bounds must be nonnegative"
            raise ValueError(message)
        tokens = set(normalize_metric_search_tokens(query))
        if not tokens:
            summaries = self._ordered[offset : offset + limit]
            return [item.model_copy(deep=True) for item in summaries], len(
                self._ordered
            )
        page = []
        count = 0
        for summary in self._ordered:
            if not tokens <= self._tokens[summary.name]:
                continue
            if offset <= count < offset + limit:
                page.append(summary.model_copy(deep=True))
            count += 1
        return page, count

    def inspect(self, name: str) -> discovery.MetricInspection:
        """Return a detached public metric description."""
        detail = self._details[name].model_copy(deep=True)
        detail.providers = deepcopy(self._providers)
        return detail

    def _describe(self, name: str) -> discovery.MetricInspection:
        summary = self._summaries[name]
        node = cast("MetricManifest", self.catalog.graph.nodes[name].node)
        inputs: dict[str, discovery.LocationInput | discovery.OperationInput] = {}
        computed = {
            key: describe_dependency(value) for key, value in node.inputs.items()
        }
        for key, value in node.location_inputs.items():
            target = value.contract.provider_scope.provider_id
            dependency = value.dependency
            if dependency is None or dependency.operation is None:
                inputs[key] = discovery.LocationInput(target_provider=target)
                continue
            computed[key] = describe_dependency(dependency)
            operation = self.catalog.graph.nodes[dependency.operation].node
            source = next(iter(operation.location_inputs.values()))
            inputs[key] = discovery.OperationInput(
                target_provider=target,
                operation=operation.name,
                source_provider=source.contract.provider_scope.provider_id,
                record_schema=forwarding_schema(self._providers[target].record_schema),
            )
        return discovery.MetricInspection(
            metric=summary,
            node=name,
            location_inputs=inputs,
            parameters=deepcopy(parameter_contract(node)),
            outputs=deepcopy(node.outputs),
            computed_inputs=computed,
            providers={},
            origins=[
                origin.model_copy(deep=True)
                for origin in sorted(
                    node.origins,
                    key=lambda origin: (
                        origin.id,
                        origin.revision is not None,
                        origin.revision or "",
                    ),
                )
            ],
        )


def _summary_tokens(summary: discovery.MetricSummary) -> frozenset[str]:
    fields = [summary.name, summary.description]
    fields.extend(value for value in summary.output_granularity.values() if value)
    for sources in summary.accepted_selections.values():
        fields.extend(sources)
    return frozenset(normalize_metric_search_tokens(" ".join(fields)))
