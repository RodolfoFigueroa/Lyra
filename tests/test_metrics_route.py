import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from lyra.sdk.plugin_loader import MANIFEST_FILENAME

from lyra_app import registry
from lyra_app.config import LyraConfig
from lyra_app.routes import metrics
from lyra_app.runtime import get_catalog
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import FilterParameters, metric_manifest, plugin_manifest
from tests.test_discovery import discovery


def _manifest() -> dict[str, Any]:
    return plugin_manifest(
        metric_manifest(name="light_metric", description="A lightweight metric.")
    )


def _list_manifest() -> dict[str, Any]:
    return plugin_manifest(
        metric_manifest(name="light_metric", parameters=FilterParameters)
    )


@pytest.fixture
def config(tmp_path: Path) -> LyraConfig:
    return load_test_config(tmp_path, metric_queues={"light_metric": "lightweight"})


def _use_repo(config: LyraConfig, repo: Path) -> registry.Catalog:
    configure_catalog_plugins(config, [repo])
    return registry.Catalog.load(config.plugins)


def test_metrics_route_returns_empty_catalog(config: LyraConfig) -> None:
    configure_catalog_plugins(config, [])
    catalog = registry.Catalog.load(config.plugins)
    response_context = Response()

    response = asyncio.run(metrics.list_metrics(response_context, catalog))

    assert response == []
    assert response_context.headers["ETag"] == catalog.discovery.fingerprint


def test_metrics_route_returns_schema_metadata_only(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")

    catalog = _use_repo(config, repo)
    response_context = Response()
    response = asyncio.run(metrics.list_metrics(response_context, catalog))

    assert len(response) == 1
    assert response[0].name == "light_metric"
    assert response[0].editions[0].accepted_selections
    assert response_context.headers["ETag"] == catalog.discovery.fingerprint


def test_metric_route_returns_schema_metadata_only(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")

    catalog = _use_repo(config, repo)
    response = asyncio.run(metrics.get_metric("light_metric", catalog))

    payload = response.model_dump()
    assert payload["family"]["name"] == "light_metric"
    assert "location" in payload["native_inputs"]
    assert payload["accepted_selections"]["location"]
    assert payload["outputs"]["light_metric"]["kind"] == "table"
    assert payload["outputs"]["light_metric"]["columns"][0]["name"] == "value"


def test_metrics_route_returns_list_schema_and_static_columns(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_list_manifest()),
        encoding="utf-8",
    )

    catalog = _use_repo(config, repo)
    response = asyncio.run(metrics.get_metric("light_metric", catalog))

    payload = response.model_dump()
    assert payload["outputs"]["light_metric"]["kind"] == "table"
    assert payload["outputs"]["light_metric"]["columns"][0]["name"] == "value"
    assert "batched_columns" not in payload["outputs"]["light_metric"]
    filters = payload["parameters"]["$defs"]["FilterParameters"]["properties"][
        "sector_filters"
    ]
    assert filters["maxItems"] == 5
    assert filters["items"] == {"type": "string"}


def test_metric_route_returns_404_for_unknown_metric(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    catalog = _use_repo(config, repo)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(metrics.get_metric("missing", catalog))

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("unit", ["m2", None])
def test_metric_catalog_preserves_unit(
    config: LyraConfig,
    tmp_path: Path,
    unit: str | None,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = _manifest()
    manifest["nodes"][0]["outputs"][manifest["nodes"][0]["name"]]["columns"][0][
        "unit"
    ] = unit
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    catalog = _use_repo(config, repo)
    metric = asyncio.run(metrics.get_metric("light_metric", catalog))
    assert (
        metric.model_dump(mode="json")["outputs"]["light_metric"]["columns"][0]["unit"]
        == unit
    )


def test_http_edition_inspection_and_bounded_search() -> None:

    view = discovery()
    app = FastAPI()
    app.include_router(metrics.router)
    app.dependency_overrides[get_catalog] = lambda: Mock(discovery=view)
    with TestClient(app) as client:
        listing = client.get("/metrics?query=1&limit=1")
        assert listing.status_code == 200
        assert listing.headers["ETag"] == view.fingerprint
        assert listing.json()[0]["editions"][0]["edition"] == "1"
        assert client.get("/metrics?offset=1").json() == []
        assert client.get("/metrics?limit=101").status_code == 422
        assert client.get("/metrics?offset=-1").status_code == 422
        assert client.get("/metrics/measure").json()["node"] is None
        concrete = client.get("/metrics/measure?edition=1")
        assert concrete.json()["node"] == "measure_1"
        assert concrete.json()["accepted_selections"]["places"]
        assert client.get("/metrics/measure?edition=missing").status_code == 404
        assert client.get("/metrics/missing").status_code == 404
