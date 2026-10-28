import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from lyra.sdk.plugin_loader import MANIFEST_FILENAME

from lyra_app import registry
from lyra_app.config import LyraConfig
from lyra_app.routes import metrics
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import FilterParameters, metric_manifest, plugin_manifest


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

    payload = response.model_dump()
    assert payload["metrics"] == []
    assert payload["catalog_fingerprint"]
    assert response_context.headers["ETag"] == payload["catalog_fingerprint"]


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

    payload = response.model_dump()
    assert payload["catalog_fingerprint"]
    assert response_context.headers["ETag"] == payload["catalog_fingerprint"]
    assert len(payload["metrics"]) == 1
    metric_payload = payload["metrics"][0]
    assert metric_payload["name"] == "light_metric"
    assert metric_payload["description"] == "A lightweight metric."
    assert metric_payload["spatial_inputs"] == {"location": "location"}
    assert metric_payload["request_schema"]["required"] == ["parameters", "location"]
    assert (
        metric_payload["request_schema"]["$defs"]["ValueParameters"]["properties"][
            "value"
        ]["type"]
        == "integer"
    )
    assert "oneOf" in metric_payload["request_schema"]["properties"]["location"]
    assert metric_payload["outputs"]["light_metric"]["kind"] == "table"
    assert metric_payload["outputs"]["light_metric"]["columns"][0]["name"] == "value"


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
    assert payload["name"] == "light_metric"
    assert payload["description"] == "A lightweight metric."
    assert payload["spatial_inputs"] == {"location": "location"}
    assert payload["request_schema"]["required"] == ["parameters", "location"]
    assert (
        payload["request_schema"]["$defs"]["ValueParameters"]["properties"]["value"][
            "type"
        ]
        == "integer"
    )
    assert "oneOf" in payload["request_schema"]["properties"]["location"]
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
    filters = payload["request_schema"]["$defs"]["FilterParameters"]["properties"][
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
    catalog = asyncio.run(metrics.list_metrics(Response(), catalog))
    assert (
        catalog.model_dump(mode="json")["metrics"][0]["outputs"]["light_metric"][
            "columns"
        ][0]["unit"]
        == unit
    )
