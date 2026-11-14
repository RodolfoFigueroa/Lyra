"""Logical partition references remain independent of physical handler batches."""

from dataclasses import replace
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pydantic import Field

from lyra.contracts.metric_scope import CachePolicy
from lyra.contracts.origins import Origin
from lyra.runtime.arguments import PreparedResolvedInvocation
from lyra.runtime.native_inputs import NativeLocationInput
from lyra.runtime.planning.bindings import BoundLocation, bind_execution
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import Dependency, Input, LocationDependency, metric
from lyra.sdk.parameters import MetricParameters
from lyra.sdk.storage_schema import empty_frame
from tests.metric_execution_helpers import schema, setup


def bound_invocation(
    ids: tuple[str, ...], policy: CachePolicy = "per_location"
) -> PreparedResolvedInvocation:
    @metric(
        name="findings",
        description="Partitioned values",
        cache=policy,
        inputs={
            "main": LocationDependency(provider="tracts", primary=True),
            "reference": LocationDependency(provider="tracts"),
        },
        outputs={"data": schema()},
    )
    def findings(main: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> pd.DataFrame:
        del main, reference
        pytest.fail("Constructing keys must not invoke handlers")

    executor, invocation = setup(findings, ids=ids)
    bound = bind_execution(
        executor.catalog,
        prepare_execution(
            executor.catalog,
            {
                "metric": "findings",
                "inputs": {
                    "main": {"provider": "tracts", "ids": list(ids)},
                    "reference": {"provider": "tracts", "ids": ["reference"]},
                },
            },
        ),
    )
    return replace(invocation, parameters=None, bound_use=bound)


def test_partition_keys_ignore_batch_but_include_shared_context() -> None:
    ab = bound_invocation(("A", "B"))
    ac = bound_invocation(("A", "C"))
    assert ab.computation_keys["A"] == ac.computation_keys["A"]
    assert ab.select_primary("main", frozenset({"A"})).computation_keys == {
        "A": ab.computation_keys["A"]
    }
    assert ab.reference("data", frozenset({"A"})) == ac.reference(
        "data", frozenset({"A"})
    )
    assert ab.reference("data") != ab.reference("other")
    shared = ab.locations["reference"]
    changed = replace(shared, frame=shared.frame.assign(location_id="changed"))
    revised = replace(
        ab, parameters=None, locations=dict(ab.locations) | {"reference": changed}
    )
    assert revised.computation_keys["A"] != ab.computation_keys["A"]
    # Independent bindings stay complete even when their bare IDs overlap.
    overlap = replace(shared, frame=ab.locations["main"].frame)
    independent = replace(
        ab, parameters=None, locations=dict(ab.locations) | {"reference": overlap}
    )
    assert independent.select_primary("main", frozenset({"A"})).locations[
        "reference"
    ].ids == ("A", "B")


@pytest.mark.parametrize("policy", ["off", "full"])
def test_selected_group_references_original_complete_computation(
    policy: CachePolicy,
) -> None:
    original = bound_invocation(("A", "B", "C"), policy)
    selected = original.select_primary("main", frozenset({"A"}))
    assert selected.computation_keys == original.computation_keys
    assert selected.reference("data", frozenset({"A"})) == {
        "computation": original.computation_keys[None],
        "output": "data",
        "groups": ["A"],
    }
    assert (
        bound_invocation(("A", "B", "D"), policy).computation_keys
        != original.computation_keys
    )


def test_empty_partition_keys_and_qualified_empty_complete_keys() -> None:
    assert bound_invocation(()).computation_keys == {}
    complete = bound_invocation((), "full")
    assert len(complete.computation_keys) == 1
    primary = complete.locations["main"]
    scope = primary.contract.provider_scope
    assert scope is not None
    other = replace(
        primary,
        contract=primary.contract.model_copy(
            update={
                "provider_scope": scope.model_copy(update={"provider_id": "other"}),
            }
        ),
    )
    revised = replace(
        complete, parameters=None, locations=dict(complete.locations) | {"main": other}
    )
    assert revised.computation_keys != complete.computation_keys


def runtime_keys(policy: CachePolicy, *, explicit: bool) -> list[str]:
    @metric(
        name="producer",
        description="Producer",
        cache=policy,
        inputs={"main": LocationDependency(provider="tracts", primary=True)},
        outputs={"data": schema()},
    )
    def producer(main: gpd.GeoDataFrame) -> pd.DataFrame:
        del main
        return empty_frame(schema())

    dependency = Dependency("data", producer="producer")
    if explicit:
        dependency = dependency.bind(main=Input("main"))

    @metric(
        name="findings",
        description="Consumer",
        cache="per_location",
        inputs={
            "main": LocationDependency(provider="tracts", primary=True),
            "data": dependency,
        },
        outputs={"data": schema()},
    )
    def findings(main: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        del main
        return data

    keys = []
    for ids in (("A", "B"), ("A", "C")):
        executor, invocation = setup(findings, producer, ids=ids)
        prepared = prepare_execution(
            executor.catalog,
            {
                "metric": "findings",
                "inputs": {"main": {"provider": "tracts", "ids": list(ids)}},
            },
        )
        bound = bind_execution(executor.catalog, prepared)
        outcome = executor.execute(invocation)
        completion = next(
            item for item in outcome.completions if item.node == "findings"
        )
        keys.append(dict(completion.computations)["A"])
        assert dict(completion.computations) == bound.demand().computation_keys
    assert (keys[0] == keys[1]) == (policy == "per_location")
    return keys


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_runtime_keys_retain_scope_and_equivalent_bindings(policy: CachePolicy) -> None:
    assert runtime_keys(policy, explicit=False) == runtime_keys(policy, explicit=True)


def test_correspondence_provenance_and_repeated_target_partitions() -> None:
    invocation = bound_invocation(("A", "B"))
    primary = invocation.locations["main"]
    frame = primary.frame.assign(location_id="target", source_location_id=["A", "B"])
    forwarded = NativeLocationInput(
        frame,
        primary.contract,
        source_scope=primary.contract,
        source=primary,
        computations=(("operation-A", "A"), ("operation-B", "B")),
    )
    invocation = replace(
        invocation,
        parameters=None,
        locations=dict(invocation.locations) | {"main": forwarded},
    )
    assert set(invocation.computation_keys) == {"target"}
    selected = invocation.select_primary("main", frozenset({"target"}))
    assert len(selected.locations["main"].frame) == 2
    revised = replace(
        invocation,
        parameters=None,
        locations=dict(invocation.locations)
        | {
            "main": replace(
                forwarded, computations=(("new-operation-A", "A"), ("operation-B", "B"))
            ),
        },
    )
    assert revised.computation_keys != invocation.computation_keys


def test_empty_correspondences_keep_completed_source_dependencies() -> None:
    invocation = bound_invocation(("A",))
    primary = invocation.locations["main"]
    frame = primary.frame.iloc[:0].assign(source_location_id=pd.Series(dtype=str))
    empty = NativeLocationInput(
        frame,
        primary.contract,
        source_scope=primary.contract,
        source=primary,
        computations=(("operation-A", "A"),),
    )
    shared = replace(
        invocation,
        parameters=None,
        locations=dict(invocation.locations) | {"reference": empty},
    )
    changed = replace(
        shared,
        parameters=None,
        locations=dict(shared.locations)
        | {
            "reference": replace(empty, computations=(("revised-operation-A", "A"),)),
        },
    )
    assert shared.computation_keys["A"] != changed.computation_keys["A"]
    assert empty.identity_payload["upstream"] == [
        {"computation": "operation-A", "group": "A"}
    ]


def test_unpartitioned_dependency_keeps_whole_reference_context() -> None:
    @metric(
        name="summary",
        description="Whole selection summary",
        inputs={"main": LocationDependency(provider="tracts")},
        outputs={"data": pa.DataFrameSchema(strict=True)},
    )
    def summary(main: gpd.GeoDataFrame) -> pd.DataFrame:
        del main
        return pd.DataFrame()

    @metric(
        name="findings",
        description="Partition with shared summary",
        cache="per_location",
        inputs={
            "main": LocationDependency(provider="tracts", primary=True),
            "summary": Dependency("data", producer="summary"),
        },
        outputs={"data": schema()},
    )
    def findings(main: gpd.GeoDataFrame, summary: pd.DataFrame) -> pd.DataFrame:
        del main, summary
        return empty_frame(schema())

    keys = []
    for ids in (("A", "B"), ("A", "C")):
        executor, invocation = setup(findings, summary, ids=ids)
        outcome = executor.execute(invocation)
        completion = next(
            item for item in outcome.completions if item.node == "findings"
        )
        keys.append(dict(completion.computations)["A"])
    assert keys[0] != keys[1]


@pytest.mark.parametrize("ids", [(), ("A", "B")])
@pytest.mark.parametrize(
    "change", ["revision", "origin", "parameters", "provider", "shared"]
)
def test_empty_references_keep_effective_context(
    ids: tuple[str, ...], change: str
) -> None:
    original = bound_invocation(ids)
    bound = original.bound_use
    assert bound is not None
    revised = original
    if change in {"revision", "origin", "parameters"}:
        options = {
            "revision": {"code_revision": "changed"},
            "origin": {"origins": frozenset({Origin(id="survey", revision="2")})},
            "parameters": {"parameters": {"threshold": 2}},
        }
        revised = replace(
            original,
            parameters=None,
            bound_use=replace(
                bound, descriptor=replace(bound.descriptor, **options[change])
            ),
        )
    else:
        name = "main" if change == "provider" else "reference"
        value = original.locations[name]
        if change == "provider":
            scope = value.contract.provider_scope
            assert scope is not None
            value = replace(
                value,
                contract=value.contract.model_copy(
                    update={
                        "provider_scope": scope.model_copy(
                            update={"code_revision": "changed"}
                        )
                    }
                ),
            )
        else:
            value = replace(value, frame=value.frame.assign(location_id="changed"))
        revised = replace(
            original,
            parameters=None,
            locations=dict(original.locations) | {name: value},
        )
    assert original.reference("data", frozenset()) != revised.reference(
        "data", frozenset()
    )
    assert original.select_primary("main", frozenset()).computation_keys == {}


def test_empty_reference_ignores_unrelated_batch_members() -> None:
    ab = bound_invocation(("A", "B"))
    ac = bound_invocation(("A", "C"))
    assert ab.reference("data", frozenset()) == ac.reference("data", frozenset())


@pytest.mark.parametrize("nested", [False, True])
def test_empty_producer_revision_reaches_consuming_computation(*, nested: bool) -> None:
    keys = []
    for revision in ("1", "2"):

        @metric(
            name="producer",
            description="Partition producer",
            code_revision=revision,
            cache="per_location",
            inputs={"main": LocationDependency(provider="tracts", primary=True)},
            outputs={"data": schema()},
        )
        def producer(main: gpd.GeoDataFrame) -> pd.DataFrame:
            del main
            pytest.fail("An empty partitioned producer has no handler work")

        @metric(
            name="middle",
            description="Partition forwarding",
            cache="per_location",
            inputs={
                "main": LocationDependency(provider="tracts", primary=True),
                "data": Dependency("data", producer="producer"),
            },
            outputs={"data": schema()},
        )
        def middle(main: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
            del main
            return data

        @metric(
            name="findings",
            description="Consume empty output",
            cache="full",
            inputs={
                "main": LocationDependency(provider="tracts"),
                "data": Dependency("data", producer="middle" if nested else "producer"),
            },
            outputs={"data": pa.DataFrameSchema(strict=True)},
        )
        def findings(main: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
            assert main.empty
            assert data.empty
            return pd.DataFrame()

        executor, invocation = setup(findings, producer, middle, ids=())
        outcome = executor.execute(invocation)
        keys.append(
            next(
                item.computations
                for item in outcome.completions
                if item.node == "findings"
            )
        )
    assert keys[0] != keys[1]


@pytest.mark.parametrize("policy", ["per_location", "full"])
def test_empty_operation_context_preserves_revisions_and_scope(
    policy: CachePolicy,
) -> None:
    references = []
    for ids in (("A", "B"), ("A", "C")):
        invocation = bound_invocation(ids)
        bound = invocation.bound_use
        assert bound is not None
        source = bound.locations["main"]
        operation = replace(
            bound,
            locations={"source": source},
            dependencies={},
            bindings={},
            per_location=policy == "per_location",
            descriptor=replace(
                bound.descriptor,
                kind="operation",
                inputs={"source": source.identity},
                target=source.provider.provider_scope,
            ),
        )
        operation = replace(operation, identity=operation.descriptor.key)
        target = replace(
            invocation.locations["main"],
            frame=invocation.locations["main"].frame.iloc[:0],
        )
        current = replace(
            invocation,
            parameters=None,
            bound_use=replace(
                bound,
                locations=dict(bound.locations)
                | {"main": BoundLocation(source.provider, None, operation)},
            ),
            locations=dict(invocation.locations) | {"main": target},
        )
        original = current.reference("data")
        revised_operation = replace(
            operation, descriptor=replace(operation.descriptor, code_revision="changed")
        )
        revised_operation = replace(
            revised_operation, identity=revised_operation.descriptor.key
        )
        revised = replace(
            current,
            parameters=None,
            bound_use=replace(
                bound,
                locations=dict(bound.locations)
                | {"main": BoundLocation(source.provider, None, revised_operation)},
            ),
        )
        assert original != revised.reference("data")
        references.append(original)
    assert (references[0] == references[1]) == (policy == "per_location")


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("ids", [(), ("A", "B", "C")])
def test_bound_demand_matches_materialized_keys_without_acquisition(
    policy: CachePolicy, ids: tuple[str, ...]
) -> None:
    # The provider and handler are forbidden at the identity boundary.
    with patch(
        "tests.node_declaration_helpers.Tracts.resolve",
        autospec=True,
        side_effect=AssertionError("Identity construction acquired records"),
    ):
        invocation = bound_invocation(ids, policy)
        bound = invocation.bound_use
        assert bound is not None
        demand = bound.demand()
        assert demand.computation_keys == invocation.computation_keys
        assert demand.identity == invocation.identity
        assert demand.reference("data") == invocation.reference("data")
        groups = frozenset({"A"}) if ids else frozenset()
        selected = bound.demand(groups)
        assert selected.invocation is bound
        assert bound.locations["main"].ids == ids
        assert (
            selected.computation_keys
            == invocation.select_primary("main", groups).computation_keys
        )
        assert selected.reference("data") == invocation.reference("data", groups)
        with pytest.raises(ValueError, match="exceeds"):
            bound.demand(frozenset({"outside"}))


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
def test_bound_selection_permutations_and_shared_context(policy: CachePolicy) -> None:
    original = bound_invocation(("A", "B"), policy).bound_use
    permuted = bound_invocation(("B", "A"), policy).bound_use
    assert original is not None
    assert permuted is not None
    assert original.demand().identity == permuted.demand().identity
    changed = replace(
        original,
        locations=dict(original.locations)
        | {"reference": replace(original.locations["reference"], ids=("changed",))},
    )
    assert original.demand().computation_keys != changed.demand().computation_keys


def test_execution_reuse_distinguishes_selection_and_parameters() -> None:
    class Parameters(MetricParameters):
        amount: int = Field(default=1, description="Value to return")

    calls = []

    @metric(
        name="producer",
        description="One node with multiple bound uses",
        inputs={"main": LocationDependency(provider="tracts", primary=True)},
        outputs={"data": schema()},
    )
    def producer(main: gpd.GeoDataFrame, parameters: Parameters) -> pd.DataFrame:
        calls.append((frozenset(main.location_id), parameters.amount))
        return pd.DataFrame(
            {
                "location_id": main.location_id,
                "row_key": "value",
                "value": parameters.amount,
            }
        )

    first = Dependency("data", producer="producer")

    @metric(
        name="findings",
        description="Independent uses must not collide",
        inputs={
            "main": LocationDependency(provider="tracts", primary=True),
            "reference": LocationDependency(provider="tracts"),
            "first": first,
            "changed": Dependency(
                "data", producer="producer", parameters={"amount": 2}
            ),
            "other": first.bind(main=Input("reference")),
        },
        outputs={"data": schema()},
    )
    def findings(
        main: gpd.GeoDataFrame,
        reference: gpd.GeoDataFrame,
        first: pd.DataFrame,
        changed: pd.DataFrame,
        other: pd.DataFrame,
    ) -> pd.DataFrame:
        assert set(first.location_id) == set(main.location_id)
        assert set(other.location_id) == set(reference.location_id)
        assert set(changed.value) == {2}
        return first

    executor, invocation = setup(findings, producer, ids=("A", "B"))
    executor.execute(invocation)
    assert sorted(calls, key=lambda item: (sorted(item[0]), item[1])) == [
        (frozenset({"A", "B"}), 1),
        (frozenset({"A", "B"}), 2),
        (frozenset({"reference"}), 1),
    ]
