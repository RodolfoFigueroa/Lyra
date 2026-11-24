"""Managed operations retain their contract across local and queued execution."""

import asyncio
from collections.abc import Callable
from copy import deepcopy
from itertools import product
from pathlib import Path
from types import FunctionType
from typing import Literal, cast
from unittest.mock import Mock

import geopandas as gpd
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest
from pydantic import Field

from lyra.contracts.jobs import JobEnvelope
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.bindings import BoundInvocation, bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk import (
    Input,
    LocationDependency,
    MetricParameters,
    PluginDefinition,
    PostgresIO,
    location_operation,
    metric,
)
from lyra.server import worker
from lyra.server.runtime import ApplicationRuntime
from test_support.location_operations import Places as Sources
from tests.managed_backend_helpers import PostGISTargets, managed_backend
from tests.queue_helpers import read_retained
from tests.submission_execution_helpers import submit
from tests.worker_graph_integration_helpers import (
    VALUES,
    install_definitions,
    native_job,
)

pytestmark = pytest.mark.usefixtures("worker_state")


class Parameters(MetricParameters):
    limits: dict[str, list[int]] = Field(
        default_factory=lambda: {"minimum": [0]}, description="Selection limits"
    )


def workload(
    ids: list[str],
    values: dict,
    policy: Literal["full", "off"],
    seen: list[str],
    *,
    selected: bool,
) -> dict[str, PluginDefinition]:
    expected = values.get("limits", {"minimum": [0]})

    @location_operation(
        name="expand",
        description="Preserve overlapping source correspondences",
        code_revision="1",
        cache="off",
        inputs={"original": LocationDependency(provider=Sources.id)},
        target_provider=Sources.id,
    )
    def expand(records: gpd.GeoDataFrame) -> dict[str, tuple[str, ...]]:
        return {
            key: (("a", "b") if key == "b" else (key,)) for key in records.location_id
        }

    def selector(
        origins: gpd.GeoDataFrame, relation: ir.Table, *, parameters: Parameters
    ) -> ir.Table:
        assert set(origins.location_id) == set(ids)
        assert len(origins) == len(set(ids)) + ("b" in ids)
        assert parameters.limits == expected
        seen.append("selector")
        threshold = parameters.limits["minimum"][0]
        parameters.limits["minimum"].append(999)
        origins.loc[:, "location_id"] = "mutated"
        return relation.filter(
            ops.IsNull(relation["count"].op()).to_expr()
            | (relation["count"] > threshold)
        )

    def mapping(
        origins: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> dict[str, tuple[str, ...]]:
        assert set(origins.location_id) == set(ids)
        assert parameters.limits == expected
        assert len(origins) == len(set(ids)) + ("b" in ids)
        assert set(origins.source_location_id) == set(ids)
        seen.append("mapping")
        # Shared targets are independent of the physical source batch.
        return dict.fromkeys(origins.location_id, ("a", "b"))

    def selected_mapping(
        origins: gpd.GeoDataFrame,
        candidates: gpd.GeoDataFrame,
        *,
        parameters: Parameters,
    ) -> dict[str, tuple[str, ...]]:
        assert set(candidates.location_id) == {"a", "b"}
        candidates.loc[:, "count"] = 999
        return mapping(origins, parameters=parameters)

    operation = location_operation(
        name="choose",
        description="Choose targets",
        code_revision="1",
        cache=policy,
        inputs={"declared_source": LocationDependency(provider=Sources.id)},
        target_provider="targets",
        target_selector=selector if selected else None,
    )(selected_mapping if selected else mapping)

    @metric(
        name="root",
        description="Consume correspondences",
        code_revision="1",
        cache="off",
        inputs={
            "seed": LocationDependency(provider=Sources.id),
            "chosen": LocationDependency(
                "choose", parameters={"limits": Input("parameters.limits")}
            ).bind(
                declared_source=LocationDependency("expand").bind(
                    original=Input("seed")
                )
            ),
        },
        outputs={"data": VALUES},
    )
    def root(
        seed: gpd.GeoDataFrame, chosen: gpd.GeoDataFrame, *, parameters: Parameters
    ) -> pd.DataFrame:
        assert parameters.limits == expected
        assert set(seed.location_id) == set(ids)
        assert seed["count"].tolist() == [4] * len(set(ids))
        assert set(zip(chosen.source_location_id, chosen.location_id, strict=True)) == {
            (source, target) for source in ids for target in ("a", "b")
        }
        assert chosen.loc[chosen.location_id == "a", "count"].tolist() == [10] * len(
            set(ids)
        )
        assert chosen.loc[chosen.location_id == "b", "count"].isna().all()
        return pd.DataFrame({"value": [len(chosen)]})

    return {
        "operations": PluginDefinition(location_operations=[expand, operation]),
        "consumer": PluginDefinition(metrics=[root]),
    }


@pytest.mark.parametrize("adapter", ["postgres", "parquet"])
@pytest.mark.parametrize(
    "case",
    list(
        product(
            [False, True],
            [[], ["a"], ["b", "a", "a"]],
            [{}, {"limits": {"minimum": [7]}}],
            ["full", "off"],
        )
    ),
)
def test_managed_operations_round_trip(
    adapter: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple
) -> None:
    selected, ids, values, policy = case
    native, resources, config = managed_backend(adapter, tmp_path, monkeypatch)
    seen: list[str] = []
    definitions = workload(ids, values, policy, seen, selected=selected)
    definitions["providers"] = PluginDefinition(
        resources={"sources": Sources, **resources}
    )
    runner = LocalRunner.from_definitions(definitions, resource_config=config)
    inputs = {"seed": {"provider": Sources.id, "ids": ids}}
    for run in (runner.run, runner.run_development):
        result = run("root", inputs=inputs, parameters=values)
        assert result.tables["data"].value.tolist() == [2 * len(set(ids))]
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    runtime.config.resources = config
    worker.initialize_worker("heavy", config=runtime.config)
    runtime.jobs.enqueue = Mock()
    calls_before_submission = len(native.mock_calls)
    assert asyncio.run(
        submit(
            runtime, "http", {"metric": "root", "inputs": inputs, "parameters": values}
        )
    )
    assert len(native.mock_calls) == calls_before_submission
    execute_queued(runtime, monkeypatch, values, len(set(ids)))
    execute_queued(runtime, monkeypatch, values, len(set(ids)))
    assert seen == (
        (["selector", "mapping"] if selected else ["mapping"])
        * (2 if policy == "full" else 4)
        if ids
        else []
    )
    assert native.to_pyarrow_batches.call_count == (4 if ids else 0)
    assert native.disconnect.call_count == (4 if ids else 0)


def execute_queued(
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    values: dict,
    count: int,
) -> None:
    envelope = JobEnvelope.model_validate_json(
        cast("Mock", runtime.jobs.enqueue).call_args.args[0].model_dump_json()
    )
    assert envelope.accepted.plan.parameters == {
        "limits": values.get("limits", {"minimum": [0]})
    }
    payload = envelope.model_dump(mode="json")
    original = deepcopy(payload)
    job = native_job(monkeypatch)
    job.id = envelope.job_id
    artifact = worker.run_metric_task(payload)
    tables = read_retained(str(artifact["result_ref"])).tables
    assert tables["data"].value.tolist() == [2 * count]
    assert payload == original


@pytest.mark.parametrize(
    "change", ["source", "parameters", "target", "choose", "expand"]
)
def test_selector_identity_precedes_acquisition(change: str) -> None:
    def bound(*, changed: bool) -> BoundInvocation:
        definitions = workload(["a", "b"], {}, "full", [], selected=True)
        provider = type(
            "Targets",
            (PostGISTargets,),
            {"code_revision": "2" if changed and change == "target" else "1"},
        )
        definitions["providers"] = PluginDefinition(
            resources={
                "source_alias": Sources,
                "target_alias": provider,
                "database": PostgresIO,
            }
        )
        if changed and change in {"choose", "expand"}:
            handlers = [
                location_operation(
                    name=node.name,
                    description=node.description,
                    code_revision="2" if node.name == change else "1",
                    inputs={
                        key: cast("LocationDependency", value)
                        for key, value in node.inputs.items()
                    },
                    target_provider=str(node.target),
                    target_selector=node.target_selector,
                    cache=node.cache,
                )(undecorated(node.function))
                for node in definitions["operations"].declarations
            ]
            definitions["operations"] = PluginDefinition(location_operations=handlers)
        catalog = compile_live_catalog(definitions)
        prepared = prepare_execution(
            catalog,
            {
                "metric": "root",
                "inputs": {
                    "seed": {
                        "provider": Sources.id,
                        "ids": ["a"] if changed and change == "source" else ["a", "b"],
                    }
                },
                "parameters": {"limits": {"minimum": [7]}}
                if changed and change == "parameters"
                else {},
            },
        )
        root = bind_execution(catalog, prepared)
        operation = root.locations["chosen"].upstream
        assert operation is not None
        assert root.locations["chosen"].ids is None
        assert root.locations["chosen"].provider.collection == "targets"
        assert set(operation.locations) == {"declared_source"}
        upstream = operation.locations["declared_source"].upstream
        assert upstream is not None
        assert upstream.node == "expand"
        assert operation.parameter_values() == prepared.parameters
        return operation

    original = bound(changed=False)
    assert original.identity == bound(changed=False).identity
    assert original.identity != bound(changed=True).identity


def undecorated(function: Callable) -> Callable:
    assert isinstance(function, FunctionType)
    copy = FunctionType(
        function.__code__, function.__globals__, closure=function.__closure__
    )
    copy.__annotations__ = dict(function.__annotations__)
    return copy
