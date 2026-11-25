from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import metric_manifest
from tests.job_id_helpers import TEST_JOB_ID
from tests.plugin_helpers import MANIFEST_FILENAME, plugin_config
from tests.queue_helpers import accepted_envelope, run_and_read
from tests.smoke_plugin_helpers import (
    SMOKE_PLUGIN_DIR,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from lyra.contracts.config import LyraConfig
    from lyra.server.worker import WorkerRuntime


def _metric(
    *, name: str, factory: str, output: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        **metric_manifest(name=name, description=f"{name} metric.", output=output),
        "_factory": factory,
    }


def _manifest(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    factories = {metric["_factory"] for metric in metrics}
    assert len(factories) == 1
    return {
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": next(iter(factories)),
        "nodes": [
            {key: value for key, value in metric.items() if key != "_factory"}
            for metric in metrics
        ],
    }


def _feature_collection(feature_id: str = "area-1") -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": feature_id,
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-99.20, 19.30],
                            [-99.10, 19.30],
                            [-99.10, 19.40],
                            [-99.20, 19.40],
                            [-99.20, 19.30],
                        ]
                    ],
                },
                "properties": {},
            }
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


@pytest.fixture
def worker_module(tmp_path: Path) -> Iterator[ModuleType]:
    config = load_test_config(
        tmp_path,
    )
    worker = importlib.import_module("lyra.server.worker")
    worker.initialize_worker(config=config)
    worker.set_runner_temp_base(tmp_path / "runner-temp")
    yield worker
    worker.set_runner_temp_base(None)


def _write_module(tmp_path: Path, module_name: str, source: str) -> None:
    sys.modules.pop(module_name, None)
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def _write_plugin_definition(
    path: Path,
    module_name: str,
    metrics: list[dict[str, Any]],
) -> None:
    declarations = [
        (metric["name"], metric["description"], metric["outputs"][metric["name"]])
        for metric in metrics
    ]
    _write_module(
        path,
        module_name,
        "import geopandas as gpd\n"
        "from lyra.sdk.context import RunContext\n"
        "from lyra.sdk.plugin import PluginDefinition, metric as declare_metric\n"
        "import pandera.pandas as pa\n"
        "from tests.contract_helpers import ValueParameters\n"
        "from test_support.location_resolution import Provider\n"
        "from lyra.sdk import LocationDependency\n"
        f"declarations = {declarations!r}\n"
        "handlers = []\n"
        "for metric_name, description, raw_output in declarations:\n"
        "    @declare_metric(\n"
        '        code_revision="1",\n'
        "        name=metric_name,\n"
        "        inputs={'location': LocationDependency(provider=Provider.id)},\n"
        "        description=description,\n"
        "        outputs={metric_name: "
        'pa.DataFrameSchema(strict=True, columns={"value": pa.Column(int, '
        'description="Example output value.", '
        'metadata={"unit": "count"})})},\n'
        "    )\n"
        "    def metric(\n"
        "        location: gpd.GeoDataFrame, parameters: ValueParameters, *,\n"
        "        context: RunContext\n"
        "    ):\n"
        "        raise AssertionError('metric should only be imported')\n"
        "    handlers.append(metric)\n"
        "def create_plugin():\n"
        "    return PluginDefinition("
        "metrics=handlers, resources={'locations': Provider})\n",
    )


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _configure_runner_plugins(config: LyraConfig, path: Path) -> None:
    configure_catalog_plugins(config, [path])


def _load_smoke_runner_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: ModuleType
) -> WorkerRuntime:
    config = load_test_config(tmp_path, plugins=[SMOKE_PLUGIN_DIR])
    for name in ("smoke_plugin.metrics", "smoke_plugin.plugin", "smoke_plugin"):
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(SMOKE_PLUGIN_DIR))
    return worker.initialize_worker(config=config)


def test_runner_loads_enabled_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    metrics = [
        _metric(name="light_metric", factory="heavy_plugin:create_plugin"),
        _metric(name="heavy_metric", factory="heavy_plugin:create_plugin"),
    ]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(worker_module.require_worker_config(), repo)

    entries = worker_module.initialize_worker(
        config=worker_module.require_worker_config(),
    )

    assert entries.catalog.get_loaded_metric_names() == ["heavy_metric", "light_metric"]


def test_runner_rejects_broken_enabled_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    selected = tmp_path / "selected"
    other = tmp_path / "other"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(selected, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    other_manifest = _manifest(
        [_metric(name="light_metric", factory="uninstalled_factory:create_plugin")]
    )
    other_manifest["plugin"]["name"] = "other-plugin"
    _write_manifest(other, other_manifest)
    worker_module.require_worker_config().plugins.installed = [
        plugin_config(selected),
        plugin_config(other),
    ]
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(RuntimeError, match="uninstalled_factory"):
        worker_module.initialize_worker(config=worker_module.require_worker_config())


def test_runner_loads_editable_plugin_before_api_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    source = tmp_path / "editable-plugin"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(source, _manifest(metrics))
    _write_plugin_definition(source, "heavy_plugin", metrics)
    worker_module.require_worker_config().plugins.installed = [plugin_config(source)]
    monkeypatch.syspath_prepend(str(source))
    entries = worker_module.initialize_worker(
        config=worker_module.require_worker_config(),
    )
    assert "heavy_metric" in entries.catalog.get_loaded_metric_names()
    assert not (tmp_path / "plugins").exists()


def test_runner_loads_smoke_editable_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_module: ModuleType
) -> None:
    entries = _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    assert entries.catalog.get_loaded_metric_names() == [
        "example_city_population",
        "example_city_source",
        "example_population_baseline",
        "example_selected_source",
        "smoke_progress_metric",
        "smoke_summary_metric",
        "smoke_table_metric",
    ]
    plugin_file = sys.modules["smoke_plugin.plugin"].__file__
    assert plugin_file is not None
    assert Path(plugin_file).resolve().is_relative_to(SMOKE_PLUGIN_DIR.resolve())


def test_runner_uses_configured_worker_temp_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    config = load_test_config(
        tmp_path,
    )
    config.worker.temp_dir = tmp_path / "worker-temp"
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="heavy_plugin:create_plugin")]
    _write_manifest(repo, _manifest(metrics))
    _write_plugin_definition(tmp_path, "heavy_plugin", metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(worker_module.require_worker_config(), repo)

    worker_module.initialize_worker(config=config)
    context = worker_module.build_run_context(
        TEST_JOB_ID,
        node="heavy_metric",
    )

    assert context.temp_dir == tmp_path / "worker-temp" / TEST_JOB_ID
    assert context.temp_dir.is_dir()
    assert "db" not in vars(context)


def test_runner_rejects_raw_function_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    metrics = [_metric(name="heavy_metric", factory="raw_plugin:run")]
    _write_manifest(repo, _manifest(metrics))
    _write_module(tmp_path, "raw_plugin", "def run(job, context):\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(worker_module.require_worker_config(), repo)

    with pytest.raises(RuntimeError, match="must declare no parameters"):
        worker_module.initialize_worker(
            config=worker_module.require_worker_config(),
        )


def test_runner_uses_live_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    repo = tmp_path / "repo"
    live_metrics = [_metric(name="heavy_metric", factory="stale_plugin:create_plugin")]
    manifest_metrics = [
        {
            **live_metrics[0],
            "description": "Description changed without regeneration.",
        }
    ]
    _write_manifest(repo, _manifest(manifest_metrics))
    _write_plugin_definition(tmp_path, "stale_plugin", live_metrics)
    monkeypatch.syspath_prepend(str(tmp_path))
    _configure_runner_plugins(worker_module.require_worker_config(), repo)

    entries = worker_module.initialize_worker(
        config=worker_module.require_worker_config()
    )
    assert (
        entries.catalog.compiled.graph.nodes["heavy_metric"].node.description
        == live_metrics[0]["description"]
    )


def test_worker_encodes_named_outputs_without_request_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_module: ModuleType,
) -> None:
    entries = _load_smoke_runner_registry(tmp_path, monkeypatch, worker_module)
    node = entries.catalog.compiled.graph.nodes["smoke_table_metric"].node
    worker_module.set_runner_temp_base(tmp_path / "native-results")
    native = SimpleNamespace(
        id="a" * 32,
        origin=entries.config.jobs.queue,
        meta={},
        started_at=None,
        ended_at=None,
        worker_name="test-worker",
        save_meta=lambda: None,
    )
    monkeypatch.setattr(worker_module, "get_current_job", lambda: native)
    inputs = {
        "location": {
            "provider": "smoke_areas",
            "ids": ["area-1"],
        },
        "parameters": {"value": 7},
    }
    envelope = accepted_envelope(
        job_id=native.id,
        metric=node.name,
        inputs=inputs,
    )
    result = run_and_read(
        worker_module.run_metric_task, envelope.model_dump(mode="json")
    )
    assert result == {
        "kind": "table",
        "job_id": native.id,
        "status": "succeeded",
        "outputs": {"smoke_table_metric": [{"value": 7}]},
        "output_metadata": node.model_dump(mode="json")["outputs"],
    }
