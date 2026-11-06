import asyncio
import importlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from lyra.contracts.config import LyraConfig
from lyra.contracts.jobs import JobCreateRequest
from lyra.contracts.plugins import NODE_MANIFEST_ADAPTER, PluginManifest
from lyra.runtime.planning.catalog import compile_catalog
from lyra.runtime.planning.manifests import MANIFEST_FILENAME
from lyra.runtime.planning.preparation import PreparationError, prepare_execution
from lyra.sdk.parameters import MetricParameters
from lyra.server import registry
from lyra.server.job_submission import submit_job
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import (
    FilterParameters,
    PositiveParameters,
    ValueParameters,
)
from tests.contract_helpers import (
    metric_manifest as _metric,
)
from tests.plugin_helpers import plugin_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
)


def _manifest(
    *,
    plugin_name: str = "fake-plugin",
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "plugin": {"name": plugin_name, "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "nodes": [metric or _metric()],
    }


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def config(tmp_path: Path) -> LyraConfig:
    return load_test_config(
        tmp_path,
        metric_queues={"light_metric": "lightweight", "raster_metric": "heavy"},
    )


def test_catalog_loads_once_without_importing_plugin_code(
    config: LyraConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])
    forbidden = Mock(side_effect=AssertionError("Plugin import or resource access"))
    monkeypatch.setattr("lyra.runtime.resources.resolve_secret", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    compiler = Mock(wraps=registry.compile_catalog)
    monkeypatch.setattr(registry, "compile_catalog", compiler)
    catalog = registry.Catalog.load(config.plugins)
    assert compiler.call_count == 1
    discovery = catalog.discovery
    info = discovery.inspect("light_metric")
    assert discovery is catalog.discovery
    assert discovery.catalog is catalog.compiled
    assert info.family.name == "light_metric"
    assert info.family.description == "A metric."
    assert set(info.native_inputs) == {"location"}
    assert info.outputs["light_metric"].columns[0].name == "value"
    assert (
        cast("dict[str, Any]", info.parameters["properties"])["value"]["type"]
        == "integer"
    )
    assert catalog.metric_queues["light_metric"] == "lightweight"
    assert catalog.compiled.graph.nodes["light_metric"].distribution == "fake-plugin"
    jobs = Mock()
    asyncio.run(
        submit_job(
            JobCreateRequest(
                metric="light_metric",
                input={
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["one"],
                    },
                    "parameters": {"value": 1},
                },
            ),
            catalog=catalog,
            jobs=jobs,
        )
    )
    assert jobs.enqueue.call_args.kwargs == {"queue": "lightweight"}
    assert (
        jobs.enqueue.call_args.args[0].accepted.catalog_fingerprint
        == discovery.fingerprint
    )
    compiler.assert_called_once()
    forbidden.assert_not_called()


def test_discovery_searches_public_contract(config: LyraConfig, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])
    catalog = registry.Catalog.load(config.plugins)
    assert [item.name for item in catalog.discovery.search("metric lot")] == [
        "light_metric"
    ]
    assert catalog.discovery.search("missing") == []


def test_catalog_refresh_reads_directory_source_without_importing_plugin_code(
    config: LyraConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "directory-plugin"
    _write_manifest(source, _manifest())
    config.plugins.installed = [plugin_config(source)]

    def fail_import(name: str, package: str | None = None) -> object:  # ruff:ignore[unused-function-argument]
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    catalog = registry.Catalog.load(config.plugins)
    entry = catalog.compiled.graph.nodes["light_metric"]

    assert entry is not None
    assert catalog.metric_queues["light_metric"] == "interactive"
    assert entry.distribution == "fake-plugin"


def test_catalog_refresh_loads_smoke_directory_fixture(tmp_path: Path) -> None:
    config = load_test_config(
        tmp_path, metric_queues=SMOKE_METRIC_QUEUES, plugins=[SMOKE_PLUGIN_DIR]
    )
    catalog = registry.Catalog.load(config.plugins)
    assert catalog.get_loaded_metric_names() == [
        "example_city_population",
        "example_city_source",
        "example_population_baseline",
        "example_selected_source",
        "smoke_progress_metric",
        "smoke_summary_metric",
        "smoke_table_metric",
    ]
    assert catalog.metric_queues["smoke_table_metric"] == "interactive"
    for name in ("smoke_table_metric", "smoke_summary_metric"):
        info = catalog.discovery.inspect(name)
        assert set(info.native_inputs) == {"location"}
        assert info.outputs[name].kind == "table"


def test_catalog_refresh_rejects_missing_directory_source(tmp_path: Path) -> None:
    missing_source = tmp_path / "missing-smoke-plugin"
    config = load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        plugins=[missing_source],
    )

    with pytest.raises(RuntimeError, match="manifest"):
        registry.Catalog.load(config.plugins)


def test_catalog_refresh_rejects_duplicate_metric_names_across_manifests(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    first_repo = tmp_path / "repo-1"
    second_repo = tmp_path / "repo-2"
    _write_manifest(first_repo, _manifest(plugin_name="plugin-a"))
    _write_manifest(second_repo, _manifest(plugin_name="plugin-b"))
    configure_catalog_plugins(
        config,
        [
            first_repo,
            second_repo,
        ],
    )

    with pytest.raises(ValueError, match="Duplicate"):
        registry.Catalog.load(config.plugins)


def test_catalog_rejects_file_node_declarations() -> None:
    with pytest.raises(ValidationError):
        _metric(
            output={"kind": "file", "media_type": "image/tiff", "extensions": [".tif"]}
        )


def test_catalog_refresh_rejects_invalid_request_json_schema(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric()
    metric["parameters_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "not-a-json-schema-type",
    }
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_plugins(config, [repo])

    with pytest.raises(RuntimeError, match="Invalid request JSON Schema"):
        registry.Catalog.load(config.plugins)


def test_catalog_fingerprint_changes_only_when_manifest_content_changes(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])

    catalog = registry.Catalog.load(config.plugins)

    first = catalog.operational_fingerprint
    catalog = registry.Catalog.load(config.plugins)
    second = catalog.operational_fingerprint
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    catalog = registry.Catalog.load(config.plugins)
    third = catalog.operational_fingerprint

    assert second == first
    assert third != first


def test_public_catalog_fingerprint_changes_when_public_contract_changes(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])

    catalog = registry.Catalog.load(config.plugins)
    first = catalog.discovery.fingerprint
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    catalog = registry.Catalog.load(config.plugins)

    assert catalog.discovery.fingerprint != first


def test_factory_change_only_updates_internal_catalog_fingerprint(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])

    catalog = registry.Catalog.load(config.plugins)

    first = catalog.operational_fingerprint
    public_fingerprint = catalog.discovery.fingerprint
    changed = _manifest()
    changed["factory"] = "fake_plugin.alternate:create_plugin"
    (repo / MANIFEST_FILENAME).write_text(json.dumps(changed), encoding="utf-8")
    catalog = registry.Catalog.load(config.plugins)
    second = catalog.operational_fingerprint

    assert second != first
    assert catalog.discovery.fingerprint == public_fingerprint


def test_public_fingerprint_includes_input_bindings(
    config: LyraConfig, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])
    first = registry.Catalog.load(config.plugins).discovery.fingerprint
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(spatial={"bounds"})))
    )
    assert registry.Catalog.load(config.plugins).discovery.fingerprint != first


def test_public_catalog_fingerprint_ignores_queue_assignment_and_manifest_path(
    config: LyraConfig,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])

    original = registry.Catalog.load(config.plugins)
    config.plugins.installed[0].routing["light_metric"] = "batch"
    rerouted = registry.Catalog.load(config.plugins)
    assert rerouted.discovery.fingerprint == original.discovery.fingerprint
    assert rerouted.operational_fingerprint != original.operational_fingerprint

    moved = tmp_path / "moved"
    _write_manifest(moved, _manifest())
    configure_catalog_plugins(config, [moved])
    config.plugins.installed[0].routing = dict(original.metric_queues)
    relocated = registry.Catalog.load(config.plugins)
    assert relocated.discovery.fingerprint == original.discovery.fingerprint
    assert relocated.operational_fingerprint == original.operational_fingerprint


def test_public_catalog_fingerprint_is_deterministic_across_plugin_load_order(
    tmp_path: Path,
) -> None:
    config = load_test_config(
        tmp_path,
        plugins=[],
    )
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _write_manifest(
        repo_a,
        _manifest(
            plugin_name="plugin-a",
            metric=_metric(name="alpha_metric", description="Alpha metric."),
        ),
    )
    _write_manifest(
        repo_b,
        _manifest(
            plugin_name="plugin-b",
            metric=_metric(name="beta_metric", description="Beta metric."),
        ),
    )

    first_order = [
        repo_a,
        repo_b,
    ]
    second_order = list(reversed(first_order))

    configure_catalog_plugins(config, first_order)
    catalog = registry.Catalog.load(config.plugins)
    first = catalog.discovery.fingerprint
    operational = catalog.operational_fingerprint

    configure_catalog_plugins(config, second_order)
    catalog = registry.Catalog.load(config.plugins)

    assert catalog.discovery.fingerprint == first
    assert catalog.operational_fingerprint == operational


@pytest.mark.parametrize(
    ("parameters", "values", "error"),
    [
        (ValueParameters, {"value": 1}, None),
        (ValueParameters, {"value": "wrong"}, "integer"),
        (PositiveParameters, {"value": 1}, None),
        (PositiveParameters, {"value": -1}, "minimum"),
        (FilterParameters, {"sector_filters": ["retail", "retail"]}, None),
        (FilterParameters, {"sector_filters": [1]}, "string"),
        (FilterParameters, {"sector_filters": ["x"] * 6}, "too long"),
    ],
)
def test_shared_preparation_validates_parameters(
    config: LyraConfig,
    tmp_path: Path,
    parameters: type[MetricParameters],
    values: dict[str, Any],
    error: str | None,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest(metric=_metric(parameters=parameters)))
    configure_catalog_plugins(config, [repo])
    catalog = registry.Catalog.load(config.plugins)
    request = {
        "metric": "light_metric",
        "parameters": values,
        "inputs": {
            "location": {
                "collection": "test:units",
                "unit_type": "lot",
                "ids": ["one"],
            },
        },
    }
    if error:
        with pytest.raises(PreparationError, match=error):
            prepare_execution(catalog.compiled, request)
    else:
        plan = prepare_execution(catalog.compiled, request)
        assert plan.parameters == values
        values.clear()
        assert plan.parameters


@pytest.mark.parametrize("version", [2, 4])
def test_catalog_refresh_rejects_legacy_manifest(
    config: LyraConfig,
    version: int,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    legacy_metric = {
        "name": "legacy_metric",
        "description": "Legacy metric.",
        "request_schema": {
            "type": "object",
            "required": ["location"],
            "properties": {"location": {}},
        },
        "spatial_inputs": {"location": "location"},
        "output": {
            "kind": "table",
            "columns": [
                {
                    "name": "value",
                    "dtype": "int64",
                    "unit": "count",
                    "description": "Value.",
                }
            ],
        },
        "execution": {"queue": "legacy"},
        "entrypoint": "legacy_plugin.runner:run",
    }
    _write_manifest(
        repo,
        {
            "schema_version": version,
            "plugin": {"name": "legacy-plugin", "version": "1.0.0"},
            "metrics": [legacy_metric],
        },
    )
    configure_catalog_plugins(config, [repo])

    with pytest.raises(RuntimeError, match="manifest"):
        registry.Catalog.load(config.plugins)


def test_catalog_prepares_location_and_bounds(
    config: LyraConfig, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest(metric=_metric(spatial={"location", "bounds"})))
    configure_catalog_plugins(config, [repo])
    catalog = registry.Catalog.load(config.plugins)
    inputs = {
        key: {"collection": "test:units", "unit_type": "lot", "ids": [key]}
        for key in ("location", "bounds")
    }
    plan = prepare_execution(
        catalog.compiled,
        {
            "metric": "light_metric",
            "parameters": {"value": 1},
            "inputs": inputs,
        },
    )
    assert {item.name: item.ids for item in plan.inputs} == {
        "location": ("location",),
        "bounds": ("bounds",),
    }
    assert set(catalog.discovery.inspect("light_metric").bindings) == set(inputs)


def test_catalog_captures_and_validates_routing(
    config: LyraConfig, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins(config, [repo])
    original = registry.Catalog.load(config.plugins)
    queues = dict(original.metric_queues)
    captured = registry.Catalog(original.compiled, queues)
    queues["light_metric"] = "changed"
    assert captured.metric_queues["light_metric"] == "lightweight"
    with pytest.raises(TypeError):
        cast("dict[str, str]", captured.metric_queues)["light_metric"] = "changed"
    for invalid in ({}, {**queues, "unknown": "batch"}):
        with pytest.raises(ValueError, match="Queue assignments"):
            registry.Catalog(original.compiled, invalid)


def test_empty_catalog() -> None:
    catalog = registry.Catalog(compile_catalog({}), {})
    assert catalog.discovery.search() == []
    assert catalog.get_loaded_metric_names() == []
    assert catalog.get_loaded_metric_queues() == {}


@pytest.mark.parametrize("edition", ["one", "two"])
def test_submission_routes_concrete_editions(edition: str) -> None:
    base = NODE_MANIFEST_ADAPTER.validate_python(_metric())
    manifests = {
        name: PluginManifest.model_validate(
            {
                **_manifest(plugin_name=name),
                "nodes": [
                    base.model_copy(
                        update={
                            "name": f"metric_{name}",
                            "family": "measure",
                            "edition": name,
                        }
                    )
                ],
            }
        )
        for name in ("one", "two")
    }
    catalog = registry.Catalog(
        compile_catalog(manifests), {"metric_one": "interactive", "metric_two": "batch"}
    )
    jobs = Mock()
    asyncio.run(
        submit_job(
            JobCreateRequest(
                metric="measure",
                edition=edition,
                input={
                    "parameters": {"value": 1},
                    "location": {
                        "collection": "test:units",
                        "unit_type": "lot",
                        "ids": ["one"],
                    },
                },
            ),
            catalog=catalog,
            jobs=jobs,
        )
    )
    (envelope,) = jobs.enqueue.call_args.args
    provenance = envelope.accepted
    assert envelope.accepted.plan.node == f"metric_{edition}"
    assert envelope.accepted.plan.edition == provenance.plan.edition == edition
    assert provenance.plan.metric == "measure"
    assert provenance.plugin.name == edition
    assert base.kind == "metric"
    assert provenance.plan.output_metadata == base.outputs
    assert provenance.catalog_fingerprint == catalog.discovery.fingerprint
    assert jobs.enqueue.call_args.kwargs == {
        "queue": catalog.metric_queues[envelope.accepted.plan.node]
    }
    assert catalog.get_loaded_metric_names() == ["metric_one", "metric_two"]
    assert [item.name for item in catalog.discovery.search()] == ["measure"]
    updated_metric = manifests[edition].nodes[0]
    assert updated_metric.kind == "metric"
    updated_metric.edition_metadata["release"] = "updated"
    updated = registry.Catalog(compile_catalog(manifests), catalog.metric_queues)
    assert updated.discovery.fingerprint != catalog.discovery.fingerprint
