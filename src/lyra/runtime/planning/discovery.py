"""Transport-independent metric discovery over the live catalog."""

import hashlib
import json
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from lyra.contracts import discovery
from lyra.contracts.dependencies import describe_dependency
from lyra.contracts.metrics import normalize_metric_search_tokens
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
        self._contracts = {
            contract.reference: contract
            for entry in catalog.graph.nodes.values()
            for contract in (
                *(value.contract for value in entry.node.location_inputs.values()),
                *(
                    (entry.node.output_contract,)
                    if entry.node.kind == "location_operation"
                    else ()
                ),
            )
        }
        if catalog.providers:
            self._contracts.update(
                {
                    p.declaration.contract.reference: p.declaration.contract
                    for p in catalog.providers.values()
                }
            )
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
                    key: sorted(sources)
                    for key, sources in catalog.accepted_sources[name].items()
                },
            )
        payload = [
            self.inspect(name).model_dump(mode="json") for name in self._summaries
        ]
        self.fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    def search(
        self, query: str = "", *, offset: int = 0, limit: int = 50
    ) -> list[discovery.MetricSummary]:
        """Return name-ordered metrics matching all tokens.

        Raises:
            ValueError: If pagination bounds are negative.
        """
        if offset < 0 or limit < 0:
            message = "Pagination bounds must be nonnegative"
            raise ValueError(message)
        tokens = set(normalize_metric_search_tokens(query))
        matches = []
        for summary in self._summaries.values():
            text = " ".join(
                [
                    summary.name,
                    summary.description,
                    *[value or "" for value in summary.output_granularity.values()],
                    *[
                        provider
                        for providers in summary.accepted_selections.values()
                        for provider in providers
                    ],
                ]
            )
            if tokens <= set(normalize_metric_search_tokens(text)):
                matches.append(summary.model_copy(deep=True))
        return matches[offset : offset + limit]

    def inspect(self, name: str) -> discovery.MetricInspection:
        """Return a detached public metric description."""
        summary = self._summaries[name]
        node = cast("MetricManifest", self.catalog.graph.nodes[name].node)
        return discovery.MetricInspection(
            metric=summary.model_copy(deep=True),
            node=name,
            native_inputs={
                key: value.contract.family.model_copy(deep=True)
                for key, value in node.location_inputs.items()
            },
            parameters=deepcopy(parameter_contract(node)),
            outputs=deepcopy(node.outputs),
            bindings=deepcopy(
                {key: value.contract for key, value in node.location_inputs.items()}
            ),
            accepted_selections={
                key: [
                    self._contracts[binding.source].model_copy(deep=True)
                    for _, binding in sorted(sources.items())
                ]
                for key, sources in self.catalog.accepted_sources[name].items()
            },
            computed_inputs={
                **{
                    key: describe_dependency(value)
                    for key, value in node.inputs.items()
                },
                **{
                    key: describe_dependency(value.dependency)
                    for key, value in node.location_inputs.items()
                    if value.dependency is not None
                    and value.dependency.operation is not None
                },
            },
            providers={
                key: value.declaration.model_copy(deep=True)
                for key, value in self.catalog.providers.items()
            },
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
