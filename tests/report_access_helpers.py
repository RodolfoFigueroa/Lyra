"""Isolated retained reports with forbidden execution and table-read boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pandas as pd
import pytest
import sqlalchemy as sa
from starlette.applications import Starlette
from starlette.routing import Mount

from lyra.contracts.reports import ExecutionReport
from lyra.runtime import provider_acquisition
from lyra.runtime.local import LocalRunner
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.results import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from lyra.sdk import PluginDefinition, loading, metric
from lyra.server.main import create_app
from lyra.server.results import ResultService
from lyra.server.runtime import ApplicationRuntime
from test_support.publication import publish_result
from tests.cache_helpers import SCHEMA
from tests.config_helpers import api_credentials, load_test_config
from tests.receiving_route_helpers import Trace, definitions
from tests.receiving_route_helpers import request as route_request
from tests.result_description_helpers import publish_values
from tests.selector_reuse_helpers import selector_definitions

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from tests.sqlite_helpers import Database

BASE = "https://lyra.example.test/public/api"
TEXT = '雪 "\\\n'


@dataclass
class Retained:
    app: FastAPI
    public: Starlette
    runtime: ApplicationRuntime
    report: ExecutionReport


def _unpartitioned_report(path: Path) -> ExecutionReport:
    @metric(name="total", description="Unpartitioned total", outputs={"rows": SCHEMA})
    def total() -> pd.DataFrame:
        return pd.DataFrame({"value": [1]})

    runner = LocalRunner.from_definitions(
        {"test": PluginDefinition(metrics=[total])}, result_store_path=path
    )
    return runner.run("total").report


def _publish(path: Path, shape: str, database: Database) -> ExecutionReport:
    if shape == "unpartitioned":
        report = _unpartitioned_report(path)
    elif shape in {"hybrid", "zero"}:
        runner = LocalRunner.from_definitions(
            definitions(Trace(), policy="full"), result_store_path=path
        )
        payload = route_request(() if shape == "zero" else ("a", "b"))
        metric = payload.pop("metric")
        runner.run(metric, **payload)
        report = runner.run(metric, **payload).report
    elif shape in {"location", "empty"}:
        plugins, _, _ = selector_definitions(target_cache=False)
        runner = LocalRunner.from_definitions(plugins, result_store_path=path)
        inputs = {
            "source": {
                "provider": "sources",
                "ids": ["empty" if shape == "empty" else "a"],
            }
        }
        runner.run("consumer", inputs=inputs)
        report = runner.run("consumer", inputs=inputs).report
    else:
        with SQLiteStore(path) as store:
            description = publish_values(store, ("one",))
            if shape == "parameters":
                description.parameters["large"] = TEXT * 16000
            reference = publish_result(ResultDescriptions(store), description)
            report = ResultStore(store).read_result(reference).report
        if shape == "graph":
            # Large valid retained graph with intentionally unretained intermediates.
            root = report.nodes[0]
            report.nodes.extend(
                root.model_copy(
                    update={"use_id": f"child_{i}", "outcome": "skipped"}, deep=True
                )
                for i in range(300)
            )
            row = database.rows["final_results"][0]
            row["report"] = report.model_dump_json()
    path.touch()
    return ExecutionReport.model_validate_json(report.model_dump_json())


@pytest.fixture
def retained(
    request: pytest.FixtureRequest,
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Retained:
    config = load_test_config(tmp_path)
    config.api.public_base_url = BASE
    config.mcp.enabled = True
    report = _publish(
        config.result_store_path, getattr(request, "param", "small"), database
    )
    app = create_app(config, credentials=api_credentials())
    jobs = Mock()
    jobs.read_job.side_effect = AssertionError("Job expired: no job lookups")
    runtime = ApplicationRuntime(
        config,
        Mock(spec=[]),
        Mock(),
        Mock(),
        jobs,
        api_credentials(),
        ResultService(jobs, config.result_store_path),
    )
    app.state.runtime = runtime
    forbidden = Mock(side_effect=AssertionError("Report read isolation"))
    for target, names in (
        (MetricTables, ("read_connection",)),
        (ResultStore, ("read_result", "read_table")),
        (LocalRunner, ("__init__", "run")),
        (loading, ("load_plugin_definition", "load_installed_plugin_definition")),
        (
            provider_acquisition,
            ("acquire_provider_input", "acquire_provider_candidates"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(target, name, forbidden)
    # Report retrieval may validate required final descriptions, but cannot read values.
    execute = database.execute

    def reports_only(statement: object, parameters: object = None) -> object:
        assert isinstance(statement, sa.sql.Select)
        source = statement.get_final_froms()[0]
        assert isinstance(source, sa.Table)
        assert source.name == "final_results"
        return execute(statement, parameters)

    database.connection.execute.side_effect = reports_only
    return Retained(app, Starlette(routes=[Mount("/public/api", app)]), runtime, report)
