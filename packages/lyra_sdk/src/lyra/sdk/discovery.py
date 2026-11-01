"""Transport-independent family discovery over a captured compiled catalog."""

import hashlib
import json
from copy import deepcopy

from lyra.sdk.catalog import CompiledCatalog
from lyra.sdk.graph import parameter_contract
from lyra.sdk.location_contracts import LocationContract, LocationFamily
from lyra.sdk.models.metric import normalize_metric_search_tokens
from lyra.sdk.models.output import OutputMetadata
from lyra.sdk.models.strict import StrictBaseModel
from lyra.sdk.types import JsonObject


class EditionChoice(StrictBaseModel):
    """Public edition and its input-specific accepted selection types."""

    edition: str | None
    accepted_selections: dict[str, list[tuple[str, str]]]


class FamilySummary(StrictBaseModel):
    """Compact discovery entry without schemas or execution graphs."""

    name: str
    description: str
    output_granularity: dict[str, str | None]
    requires_edition: bool
    editions: list[EditionChoice]


class MetricInspection(StrictBaseModel):
    """Shared family contract, optionally bound to a concrete public edition."""

    family: FamilySummary
    edition: str | None
    node: str | None
    edition_metadata: JsonObject
    native_inputs: dict[str, LocationFamily]
    parameters: JsonObject
    outputs: dict[str, OutputMetadata]
    bindings: dict[str, LocationContract]
    accepted_selections: dict[str, list[LocationContract]]


class Discovery:
    """Reuse deterministic projections without acquiring resources.

    Returned models are detached copies. Named-family inspection exposes shared
    schemas; concrete inspection adds source bindings and accepted selections.
    """

    def __init__(self, catalog: CompiledCatalog) -> None:
        """Index only public variants from the authoritative catalog."""
        self.catalog = catalog
        self._summaries: dict[str, FamilySummary] = {}
        self._variants: dict[str, dict[str | None, str]] = {}
        self._contracts = {
            contract.reference: contract
            for entry in catalog.graph.nodes.values()
            for contract in (
                *entry.node.location_contracts.values(),
                *((entry.node.output_contract,) if entry.node.output_contract else ()),
            )
        }
        if catalog.location_provider:
            self._contracts.update(
                {
                    c.reference: c
                    for c in catalog.location_provider.declaration.contracts
                }
            )
        for family, variants in sorted(catalog.editions.families.items()):
            public = {
                edition: name
                for edition, name in sorted(
                    variants.items(), key=lambda pair: pair[0] or ""
                )
                if catalog.graph.nodes[name].node.is_public_metric
            }
            if not public:
                continue
            self._variants[family] = public
            node = catalog.graph.nodes[next(iter(public.values()))].node
            self._summaries[family] = FamilySummary(
                name=family,
                description=node.description.strip(),
                output_granularity={
                    name: output.granularity for name, output in node.outputs.items()
                },
                requires_edition=None not in public,
                editions=[
                    EditionChoice(
                        edition=edition,
                        accepted_selections={
                            input_name: sorted(sources)
                            for input_name, sources in catalog.accepted_sources[
                                name
                            ].items()
                        },
                    )
                    for edition, name in public.items()
                ],
            )
        payload = [
            self.inspect(family, edition=edition).model_dump(mode="json")
            for family, variants in self._variants.items()
            for edition in variants
        ]
        self.fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    def search(
        self, query: str = "", *, offset: int = 0, limit: int = 50
    ) -> list[FamilySummary]:
        """Return name-ordered families with only editions matching all tokens.

        Raises:
            ValueError: If pagination bounds are negative.
        """
        if offset < 0 or limit < 0:
            message = "Pagination bounds must be nonnegative"
            raise ValueError(message)
        tokens = set(normalize_metric_search_tokens(query))
        matches = []
        for summary in self._summaries.values():
            choices = []
            for choice in summary.editions:
                node = self.catalog.graph.nodes[
                    self._variants[summary.name][choice.edition]
                ].node
                text = " ".join(
                    [
                        summary.name,
                        node.description,
                        choice.edition or "",
                        *[value or "" for value in summary.output_granularity.values()],
                        *[
                            part
                            for pairs in choice.accepted_selections.values()
                            for pair in pairs
                            for part in pair
                        ],
                    ]
                )
                if tokens <= set(normalize_metric_search_tokens(text)):
                    choices.append(choice)
            if choices:
                matches.append(
                    summary.model_copy(update={"editions": choices}, deep=True)
                )
        return matches[offset : offset + limit]

    def inspect(self, family: str, *, edition: str | None = None) -> MetricInspection:
        """Return shared or executable contracts without dependency expansion."""
        variants = self._variants[family]
        shared = edition is None and None not in variants
        name = next(iter(variants.values())) if shared else variants[edition]
        node = self.catalog.graph.nodes[name].node
        parameters = deepcopy(parameter_contract(node))
        if "$defs" in node.request_schema:
            parameters["$defs"] = deepcopy(node.request_schema["$defs"])
        return MetricInspection(
            family=self._summaries[family].model_copy(deep=True),
            edition=edition,
            node=None if shared else name,
            edition_metadata={} if shared else deepcopy(node.edition_metadata),
            native_inputs={
                key: value.family.model_copy(deep=True)
                for key, value in node.location_contracts.items()
            },
            parameters=parameters,
            outputs=deepcopy(node.outputs),
            bindings={} if shared else deepcopy(node.location_contracts),
            accepted_selections={}
            if shared
            else {
                key: [
                    self._contracts[binding.source].model_copy(deep=True)
                    for _, binding in sorted(sources.items())
                ]
                for key, sources in self.catalog.accepted_sources[name].items()
            },
        )
