"""Static report descriptions preserve ownership without resource acquisition."""

from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest

from lyra.contracts.dependencies import Dependency, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.execution import GraphExecutor
from lyra.runtime.planning.bindings import bind_execution
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from lyra.runtime.report_collection import ReportCollection
from lyra.sdk.parquet import ParquetLocationProvider
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.postgres import PostgresIO
from test_support.table_fixtures import value_schema
from tests.live_graph_helpers import plugins
from tests.metric_scope_helpers import identity_schema
from tests.node_declaration_helpers import CENSUS, Tracts, provider
from tests.postgis_provider_helpers import Places


@pytest.mark.parametrize("ids", [[], ["a", "b"]])
@pytest.mark.parametrize("revision", [None, "provider-2"])
@pytest.mark.parametrize("root_name", ["measure", "summary"])
def test_capture_preserves_empty_ownership_and_detaches_declarations(
    ids: list[str],
    revision: str | None,
    root_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = Origin(id="shared", revision=revision, description="Declared dataset")
    records = provider(id="records", code_revision=revision, origins=[origin, origin])
    blocked = Mock(side_effect=AssertionError("reporting acquired a resource"))
    monkeypatch.setattr(records, "__init__", blocked)
    monkeypatch.setattr(records, "setup", blocked)
    schema = identity_schema()

    @metric(
        name="measure",
        description="Measure records",
        inputs={"places": LocationDependency(provider="records", primary=True)},
        outputs={"first": schema, "second": schema},
        origins=[origin, origin],
    )
    def measure(places: gpd.GeoDataFrame) -> pd.DataFrame:
        del places
        pytest.fail("reporting invoked handler")

    @metric(
        name="summary",
        description="Summarize measurements",
        inputs={
            "places": LocationDependency(provider="records"),
            "data": Dependency("first", producer="measure"),
        },
        outputs={"data": value_schema()},
    )
    def summary(places: gpd.GeoDataFrame, data: pd.DataFrame) -> pd.DataFrame:
        del places, data
        pytest.fail("reporting invoked summary")

    catalog = compile_live_catalog(
        {
            "test": PluginDefinition(
                metrics=[measure, summary], resources={"records": records}
            )
        }
    )
    plan = prepare_execution(
        catalog,
        {
            "metric": root_name,
            "inputs": {"places": {"provider": "records", "ids": ids}},
        },
    )
    executor = GraphExecutor(catalog, Mock())
    root = executor.validate_plan(plan)
    report = ReportCollection()
    report.capture(root, catalog, plan)
    assert report.root_use_id is not None
    description = report.descriptions[report.root_use_id]
    if root_name == "summary":
        assert description.primary_input is None
        assert description.outputs["data"].owner_scope is None
        assert description.inputs["data"].output_name == "first"
        description = report.descriptions[description.inputs["data"].source_use_id]
    assert description.primary_input == "places"
    assert description.code_revision is None
    assert description.origins == [origin]
    scope = description.inputs["places"].provider_scope
    assert scope is not None
    assert scope.code_revision == revision
    assert [(o.id, o.revision, o.description) for o in scope.origins] == [
        ("shared", revision, "Declared dataset")
    ]
    assert set(description.outputs) == {"first", "second"}
    for output in description.outputs.values():
        assert output.scope == "location"
        assert output.owner_scope == scope
        assert {column.name for column in output.table_schema.columns} == {
            "location_id",
            "row_key",
        }
    node = catalog.graph.nodes["measure"].node
    assert node.kind == "metric"
    node.outputs["first"].columns.clear()
    assert len(description.outputs["first"].table_schema.columns) == 2
    assert report.request is not None
    assert report.request.inputs["places"].ids == ids
    blocked.assert_not_called()


@pytest.mark.parametrize("adapter", ["parquet", "postgis"])
def test_target_adapter_is_described_without_initialization(
    adapter: str, monkeypatch: pytest.MonkeyPatch
) -> None:

    base = ParquetLocationProvider if adapter == "parquet" else Places
    target = type(
        "Counties",
        (base,),
        {
            "id": "counties",
            "schema": Tracts.schema,
            "origins": (CENSUS,),
        },
    )
    blocked = Mock(side_effect=AssertionError("initialized target"))
    monkeypatch.setattr(target, "__init__", blocked)
    monkeypatch.setattr(target, "setup", blocked)
    definitions = plugins()
    definitions["providers"] = PluginDefinition(
        resources={
            "tracts": Tracts,
            "counties": target,
            "database": PostgresIO,
        }
    )
    catalog = compile_live_catalog(definitions)
    plan = prepare_execution(
        catalog,
        {"metric": "consumer", "inputs": {"seed": {"provider": "tracts", "ids": []}}},
    )
    report = ReportCollection()
    report.capture(bind_execution(catalog, plan), catalog, plan)
    operations = [d.operation for d in report.descriptions.values() if d.operation]
    assert len(operations) == 1
    assert operations[0].target_adapter == adapter
    assert operations[0].target_scope.provider_id == "counties"
    blocked.assert_not_called()
