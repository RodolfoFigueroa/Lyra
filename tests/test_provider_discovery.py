import asyncio
import json
from pathlib import Path
from unittest.mock import Mock, create_autospec

import geopandas as gpd
import httpx
import pandas as pd
import pandera.pandas as pa
import pytest
from fastapi import FastAPI

from lyra.api.client.endpoints import get_metric
from lyra.contracts.dependencies import Input, LocationDependency
from lyra.contracts.origins import Origin
from lyra.runtime.planning.catalog import compile_live_catalog
from lyra.runtime.planning.discovery import Discovery
from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin import PluginDefinition, location_operation, metric
from lyra.sdk.plugin_cli import render_description
from lyra.server.mcp.tools import InProcessLyraBackend
from lyra.server.routes import metrics
from lyra.server.runtime import get_catalog
from test_support.mcp import call_tool
from tests.node_declaration_helpers import provider

CENSUS = Origin(id="census", revision="2025", description="Census release")
ELEVATION = Origin(id="elevation")


@pytest.fixture
def definition(monkeypatch: pytest.MonkeyPatch) -> PluginDefinition:
    forbidden = Mock(side_effect=AssertionError("Inspection acquired a resource"))
    resources = {
        "database_tracts": provider(id="tracts", code_revision="1", origins=(CENSUS,)),
        "database_counties": provider(
            id="counties", code_revision="2", origins=(CENSUS,), description="Counties"
        ),
        "combined": provider(id="combined", origins=(CENSUS, ELEVATION)),
    }
    for cls in resources.values():
        monkeypatch.setattr(cls, "__init__", forbidden)
        monkeypatch.setattr(cls, "setup", forbidden)
        monkeypatch.setattr(
            cls, "resolve", create_autospec(cls.resolve, side_effect=forbidden)
        )
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)

    @location_operation(
        name="aggregate",
        code_revision="1",
        description="Aggregate tracts",
        inputs={"source": LocationDependency(provider="tracts")},
        target="counties",
        canonical=True,
    )
    def aggregate(source: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return source

    @metric(
        name="measure",
        code_revision="1",
        description="Measure records",
        inputs={
            "tracts": LocationDependency(provider="tracts"),
            "counties": LocationDependency(provider="counties"),
            "combined": LocationDependency(provider="combined"),
            "derived": LocationDependency(operation="aggregate").bind(
                source=Input("tracts")
            ),
        },
        origins=(CENSUS,),
        outputs={"result": pa.DataFrameSchema()},
    )
    def measure(
        tracts: gpd.GeoDataFrame,
        counties: gpd.GeoDataFrame,
        combined: gpd.GeoDataFrame,
        derived: gpd.GeoDataFrame,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {"count": [len(tracts) + len(counties) + len(combined) + len(derived)]}
        )

    return PluginDefinition(
        metrics=[measure], location_operations=[aggregate], resources=resources
    )


def test_live_inspection_scope_routes_and_isolation(
    definition: PluginDefinition,
) -> None:
    catalog = compile_live_catalog({"test": definition})
    view = Discovery(catalog)
    detail = view.inspect("measure")
    assert detail.origins == [CENSUS]
    assert set(detail.providers) == {"tracts", "counties", "combined"}
    for key, revision, origins, description in (
        ("tracts", "1", {CENSUS}, None),
        ("counties", "2", {CENSUS}, "Counties"),
        ("combined", None, {CENSUS, ELEVATION}, None),
    ):
        declared = detail.providers[key]
        scope = declared.contract.provider_scope
        assert scope is not None
        assert (scope.provider_id, scope.code_revision, scope.origins) == (
            key,
            revision,
            origins,
        )
        assert declared.description == description
        assert declared.contract == detail.bindings[key]
    assert detail.providers["tracts"].resource == "database_tracts"
    assert (
        detail.native_inputs["tracts"].structure
        == detail.native_inputs["counties"].structure
    )
    assert detail.metric.accepted_selections == {
        "tracts": ["tracts"],
        "counties": ["counties", "tracts"],
        "combined": ["combined"],
    }
    assert detail.computed_inputs["derived"]["operation"] == "aggregate"
    sdk = definition.describe("measure").manifest
    assert set(sdk.request_schema["properties"]) == {"tracts", "counties", "combined"}
    assert sdk.location_inputs["derived"].provider == detail.providers["counties"]
    request = {
        "metric": "measure",
        "inputs": {
            key: {"provider": key, "ids": []}
            for key in ("tracts", "counties", "combined")
        },
    }
    before = prepare_execution(catalog, request)
    detail.providers["tracts"].contract.family.structure.output.columns.clear()
    detail.origins.clear()
    detail.computed_inputs["derived"].clear()
    sdk.location_inputs["tracts"].contract.family.structure.output.columns.clear()
    assert (
        view.inspect("measure")
        .providers["tracts"]
        .contract.family.structure.output.columns
    )
    assert view.inspect("measure").origins == [CENSUS]
    assert (
        definition.describe("measure")
        .manifest.location_inputs["tracts"]
        .contract.family.structure.output.columns
    )
    assert prepare_execution(catalog, request) == before


def test_cli_inspection(
    definition: PluginDefinition, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "lyra.sdk.plugin_cli._project_configuration",
        lambda _path: ("test", "1", "test:create_plugin"),
    )
    monkeypatch.setattr(
        "lyra.sdk.plugin_cli._load_definition", lambda *_args: definition
    )
    payload = json.loads(render_description(tmp_path, "measure", json_output=True))[
        "nodes"
    ][0]
    assert (
        payload["manifest"]["location_inputs"]["counties"]["provider"]["description"]
        == "Counties"
    )
    assert payload["manifest"]["origins"] == [CENSUS.model_dump()]
    assert "derived" not in payload["request_schema"]["properties"]
    assert render_description(tmp_path, "measure")


def test_http_mcp_and_client_inspection(definition: PluginDefinition) -> None:
    view = Discovery(compile_live_catalog({"test": definition}))
    app = FastAPI()
    app.include_router(metrics.router)
    app.dependency_overrides[get_catalog] = lambda: Mock(discovery=view)
    backend = InProcessLyraBackend(lambda: Mock(catalog=Mock(discovery=view)))

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics/measure")
            assert response.status_code == 200
            inspected = get_metric("measure").adapter.validate_json(response.content)
            assert inspected == view.inspect("measure")
            assert (await client.get("/metrics/unknown")).status_code == 404
        result = await call_tool(backend, "lyra_get_metric", {"metric": "measure"})
        assert not result.get("isError", False)
        assert result["structuredContent"] == view.inspect("measure").model_dump(
            mode="json"
        )

    asyncio.run(exercise())
