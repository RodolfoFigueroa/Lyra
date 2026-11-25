"""Published reports preserve selected graphs across actual cache boundaries."""

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from lyra.contracts.reports import ExecutionReport, ReportNode
from lyra.data.interventions import Intervention
from lyra.runtime.local import LocalRunner
from lyra.runtime.results import ResultStore
from lyra.sdk import Dependency, Input, PluginDefinition, metric
from tests.cache_helpers import SCHEMA
from tests.receiving_route_helpers import Trace, definitions, request
from tests.report_parameter_helpers import (
    LeafParameters,
    MiddleParameters,
    RootParameters,
)
from tests.selector_reuse_helpers import selector_definitions
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def graph(report: ExecutionReport) -> dict[str, ReportNode]:
    nodes = {node.use_id: node for node in report.nodes}
    assert len(nodes) == len(report.nodes)
    assert nodes[report.root_use_id].node_id == report.request.metric
    for node in nodes.values():
        for binding in node.inputs.values():
            producer = nodes[binding.source_use_id]
            if producer.kind == "metric":
                assert binding.output_name in producer.outputs
    return nodes


def test_shared_and_distinct_uses_with_cached_consumer(
    tmp_path: Path, database: Database
) -> None:
    events = []

    @metric(name="leaf", description="Unstored leaf", outputs={"rows": SCHEMA})
    def leaf(parameters: LeafParameters) -> pd.DataFrame:
        events.append(("leaf", parameters.distance))
        return pd.DataFrame({"value": [parameters.distance]})

    @metric(
        name="middle",
        description="Cached consumer",
        cache="full",
        code_revision="middle-1",
        inputs={
            "rows": Dependency(
                "rows",
                producer="leaf",
                parameters={"distance": Input("parameters.radius")},
            )
        },
        outputs={"rows": SCHEMA},
    )
    def middle(rows: pd.DataFrame, parameters: MiddleParameters) -> pd.DataFrame:
        events.append(("middle", parameters.radius))
        return rows

    @metric(
        name="root",
        description="Shared and distinct dependencies",
        private=True,
        inputs={
            "first": Dependency(
                "rows",
                producer="middle",
                parameters={"radius": Input("parameters.search_radius")},
            ),
            "same": Dependency("rows", producer="middle", parameters={"radius": 5}),
            "different": Dependency(
                "rows", producer="middle", parameters={"radius": 7}
            ),
            "direct": Dependency(
                "rows",
                producer="leaf",
                parameters={"distance": Input("parameters.search_radius")},
            ),
        },
        outputs={"result": SCHEMA},
    )
    def root(
        first: pd.DataFrame,
        same: pd.DataFrame,
        different: pd.DataFrame,
        direct: pd.DataFrame,
        parameters: RootParameters,
    ) -> pd.DataFrame:
        assert first.equals(same)
        assert first.value.tolist() == direct.value.tolist()
        parameters.options["items"].append(999)
        return pd.DataFrame({"value": [int(first.value.sum() + different.value.sum())]})

    runner = LocalRunner.from_definitions(
        {"test": PluginDefinition(metrics=[root, middle, leaf])},
        result_store_path=tmp_path / "results.sqlite",
    )
    for warm in (False, True):
        events.clear()
        result = runner.run_development("root")
        report = result.report
        nodes = graph(report)
        root_node = nodes[report.root_use_id]
        first = nodes[root_node.inputs["first"].source_use_id]
        different = nodes[root_node.inputs["different"].source_use_id]
        direct = nodes[root_node.inputs["direct"].source_use_id]
        assert root_node.inputs["same"].source_use_id == first.use_id
        assert different.use_id != first.use_id
        assert first.inputs["rows"].source_use_id == direct.use_id
        assert len(nodes) == 5
        assert (
            direct.outcome == "executed"
        )  # A hit in middle does not skip another consumer's work.
        assert first.outcome == different.outcome == ("reused" if warm else "executed")
        assert nodes[different.inputs["rows"].source_use_id].outcome == (
            "skipped" if warm else "executed"
        )
        assert sorted(events) == (
            [("leaf", 5)]
            if warm
            else [("leaf", 5), ("leaf", 7), ("middle", 5), ("middle", 7)]
        )
        assert (
            report.request.parameters
            == root_node.parameters
            == result.metadata.parameters
        )
        assert root_node.parameters["options"] == {"items": [1, None]}
        assert first.parameters["mode"] == "default"
        assert first.code_revision == different.code_revision == "middle-1"
        connections = {
            (b.source_use_id, b.source_parameter, b.target_use_id, b.target_parameter)
            for b in report.parameter_bindings
        }
        assert connections == {
            (root_node.use_id, "search_radius", first.use_id, "radius"),
            (root_node.use_id, "search_radius", direct.use_id, "distance"),
            *(
                (root_node.use_id, name, target.use_id, name)
                for name in ("options", "tags", "label")
                for target in (first, different)
            ),
            (first.use_id, "radius", direct.use_id, "distance"),
            (
                different.use_id,
                "radius",
                different.inputs["rows"].source_use_id,
                "distance",
            ),
        }
        assert result.tables["result"].value.tolist() == [12]
    # Explanation must not add a retention obligation for uncached intermediates.
    assert "leaf" not in {row["node_id"] for row in database.rows["stored_results"]}


@pytest.mark.parametrize("ids", [["a", "b"], ["empty"], []])
@pytest.mark.parametrize("intervened", [False, True])
def test_published_selector_stages_and_partial_reuse(
    tmp_path: Path, ids: list[str], *, intervened: bool
) -> None:
    plugins, events, _ = selector_definitions(target_cache=False)
    runner = LocalRunner.from_definitions(
        plugins, result_store_path=tmp_path / "results.sqlite"
    )
    changes = [
        Intervention(catalog=name, id=f"{name}-plan", revision="1", remove=["outside"])
        for name in ("sources", "targets")
        if intervened
    ]
    for warm in (False, True):
        events.clear()
        result = runner.run(
            "consumer",
            inputs={"source": {"provider": "sources", "ids": ids}},
            interventions=changes,
        )
        nodes = graph(result.report)
        operation = next(n for n in nodes.values() if n.node_id == "mapping")
        assert operation.operation is not None
        expected = "executed" if ids and not warm else "skipped"
        assert (
            operation.operation.selector,
            operation.operation.target_query,
            operation.operation.mapping,
        ) == (expected,) * 3
        assert operation.origins == []
        assert operation.operation.target_scope.provider_id == "targets"
        if intervened:
            validations = {
                v.scope.provider_id: v for v in result.report.intervention_validations
            }
            source, target = validations["sources"], validations["targets"]
            assert (
                source.membership_lookup
                == target.membership_lookup
                == ("skipped" if warm else "executed")
            )
            assert operation.inputs["source"].provider_scope == source.scope
            assert operation.operation.target_scope == target.scope
            assert (
                nodes[result.report.root_use_id].inputs["places"].provider_scope
                == target.scope
            )
            assert (
                nodes[result.report.root_use_id].outputs["result"].owner_scope is None
            )
            result.metadata.store_path.touch()
            with ResultStore.open(result.metadata.store_path) as reader:
                saved = reader.describe_result(result.report.job_id).location_inputs[
                    "places"
                ]
                assert saved.source_scope == source.scope
                assert saved.provider_scope == target.scope
                assert reader.read_report(result.report.job_id) == result.report
        assert set(result.report.request.inputs) == {"source"}
        assert result.report.request.inputs["source"].ids == ids
        assert operation.outcome == ("reused" if warm and ids else expected)
        if warm and ids == ["a", "b"]:
            assert ("exact", frozenset({"a-2", "b-2"})) in events
            assert (
                next(n for n in nodes.values() if n.node_id == "targets").outcome
                == "executed"
            )
    if ids == ["a", "b"]:
        result = runner.run(
            "consumer",
            inputs={"source": {"provider": "sources", "ids": ["b", "c"]}},
            interventions=changes,
        )
        operation = next(n for n in result.report.nodes if n.node_id == "mapping")
        assert operation.outcome == "mixed"
        assert operation.operation is not None
        assert (
            operation.operation.mapping
            == operation.operation.selector
            == operation.operation.target_query
            == "executed"
        )
        assert result.report.request.inputs["source"].ids == ["b", "c"]


@pytest.mark.parametrize("ids", [(), ("a", "b")])
def test_routed_hybrid_report_survives_frame_and_caller_mutation(
    tmp_path: Path, ids: tuple[str, ...]
) -> None:
    trace = Trace()
    runner = LocalRunner.from_definitions(
        definitions(trace, policy="full"), result_store_path=tmp_path / "results.sqlite"
    )
    payload = request(ids)
    original = deepcopy(payload)
    cold = runner.run(payload.pop("metric"), **payload)
    snapshot = cold.report.model_dump()
    payload["inputs"]["places"]["ids"].append("edited")
    cold.tables["total"].attrs["owner_scope"] = "edited"
    cold.tables["total"]["total"] = -1
    assert cold.report.model_dump() == snapshot
    for result in (cold, runner.run(original.pop("metric"), **original)):
        nodes = graph(result.report)
        root = nodes[result.report.root_use_id]
        assert result.report.request.inputs["places"].ids == list(ids)
        assert (
            result.report.request.parameters
            == root.parameters
            == result.metadata.parameters
        )
        assert root.outputs["total"].scope == "unpartitioned"
        producer = next(n for n in nodes.values() if n.node_id == "producer")
        assert producer.outputs["rows"].owner_scope is not None
        assert producer.outputs["rows"].owner_scope.provider_id == "target"
        assert producer.inputs["places"].provider_scope is not None
        assert producer.inputs["places"].provider_scope.provider_id == "target"
        operations = [n for n in nodes.values() if n.kind == "operation"]
        assert {n.node_id for n in operations} == {"first", "second"}
        for node in operations:
            assert node.operation is not None
            assert (
                node.operation.selector
                == node.operation.target_query
                == "not_applicable"
            )
            assert node.origins == []
