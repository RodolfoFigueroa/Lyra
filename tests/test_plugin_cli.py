from __future__ import annotations

import json
import shutil
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

from yaml import safe_load

from lyra.contracts.config import LyraConfig
from lyra.sdk import plugin_cli
from lyra.sdk.dependencies import Dependency, LocationDependency
from lyra.sdk.loading import load_installed_plugin_definition
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.sdk.plugin_cli import (
    describe_plugin,
    main,
    render_description,
    render_manifest,
)
from test_support.location_operations import OUTPUT, MemoryIO, select_places
from test_support.location_resolution import Provider
from tests.plugin_helpers import plugin_config
from tests.plugin_project_helpers import write_project
from tests.worker_graph_integration_helpers import TABLE

if TYPE_CHECKING:
    import pytest


def test_inspection_is_deterministic(tmp_path: Path) -> None:
    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    first = render_manifest(tmp_path)
    assert first == render_manifest(tmp_path)
    payload = json.loads(first)
    assert payload["plugin"] == {"name": "example-plugin", "version": "1.2.3"}
    assert payload["factory"] == "example_plugin:create_plugin"


def test_cli_exit_codes_and_project_errors(
    tmp_path: Path,
    capsys: object,
) -> None:
    del capsys
    assert main(["describe", "--project", str(tmp_path)]) == 2

    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    assert main(["diagram", "--project", str(tmp_path)]) == 0
    assert main(["describe", "--project", str(tmp_path)]) == 0


def test_describe_plugin_renders_human_and_json_output(tmp_path: Path) -> None:
    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    descriptions = describe_plugin(tmp_path)
    assert [description.manifest.name for description in descriptions] == ["example"]
    assert descriptions[0].manifest.request_schema["properties"]["parameters"][
        "properties"
    ]["value"]["description"] == ("Example input value.")

    human = render_description(tmp_path, "example")
    assert "Metric: example" in human
    assert "Signature: calculate(location:" in human
    assert "Example input value." in human
    assert "Output example: table (1 source column(s))" in human

    payload = json.loads(render_description(tmp_path, json_output=True))
    assert payload["nodes"][0]["manifest"]["name"] == "example"
    assert payload["nodes"][0]["request_schema"]["properties"]["parameters"][
        "properties"
    ]["value"]["description"] == ("Example input value.")


def test_describe_cli_reports_unknown_metrics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    assert main(["describe", "example", "--project", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"][0]["manifest"]["name"] == "example"

    assert main(["describe", "missing", "--project", str(tmp_path)]) == 2
    assert "available nodes: example" in capsys.readouterr().err


def test_manifest_generation_preserves_explicit_null_unit(tmp_path: Path) -> None:
    write_project(tmp_path)
    source = tmp_path / "example_plugin.py"
    source.write_text(source.read_text().replace("Unit.COUNT", "None"))
    sys.modules.pop("example_plugin", None)
    manifest = json.loads(render_manifest(tmp_path))
    assert manifest["nodes"][0]["outputs"]["example"]["columns"][0]["unit"] is None
    sys.modules.pop("example_plugin", None)


def test_describe_metric_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    try:
        assert main(["describe", "--project", str(tmp_path)]) == 0
        human = capsys.readouterr().out
        assert "Metric: example" in human
        payload = json.loads(render_description(tmp_path, json_output=True))
        assert "role" not in payload["nodes"][0]
        assert main(["describe", "missing", "--project", str(tmp_path)]) == 2
        assert "available nodes: example" in capsys.readouterr().err
    finally:
        sys.modules.pop("example_plugin", None)


def test_manifest_and_inspection_do_not_require_foreign_producer(
    tmp_path: Path,
) -> None:
    write_project(tmp_path)
    path = tmp_path / "example_plugin.py"
    source = (
        path.read_text()
        .replace(
            "from lyra.sdk.parameters import MetricParameters",
            "from lyra.sdk.dependencies import Dependency\n"
            "from lyra.sdk.parameters import MetricParameters",
        )
        .replace(
            '    name="example",',
            '    name="example",\n'
            '    inputs={"foreign": Dependency("uninstalled_output", '
            'producer="foreign")},',
        )
        .replace(
            "def calculate(location: Locations, parameters: Parameters):",
            "def calculate(location: Locations, parameters: Parameters, foreign):",
        )
    )
    path.write_text(source)
    sys.modules.pop("example_plugin", None)
    payload = json.loads(render_manifest(tmp_path))
    assert payload["nodes"][0]["inputs"]["foreign"]["output"] == "uninstalled_output"
    description = render_description(tmp_path)
    assert "uninstalled_output" in description
    assert "foreign" in description


def test_manifest_atomic_failure_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    manifest = tmp_path / "diagram.mmd"
    manifest.write_text("original")

    def fail_replace(_self: Path, _target: Path) -> None:
        message = "replace failed"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)
    assert main(["diagram", "--project", str(tmp_path), "--output", str(manifest)]) == 2
    assert manifest.read_text() == "original"
    assert not list(tmp_path.glob(".diagram.mmd.*"))


def test_inspection_of_operations_and_nested_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(
        code_revision="1",
        name="root",
        description="Root",
        inputs={
            "data": Dependency(
                "foreign",
                producer="foreign",
            ).bind(places=LocationDependency("select_places"))
        },
        outputs={"root": TABLE},
    )
    def root(data: object) -> object:
        del data
        raise AssertionError

    write_project(tmp_path)
    for metrics in ([], [root]):
        definition = PluginDefinition(
            metrics=metrics,
            location_operations=[select_places],
            resources={"memory": MemoryIO},
        )
        monkeypatch.setattr(
            plugin_cli, "_load_definition", lambda *_, definition=definition: definition
        )
        payload = json.loads(render_description(tmp_path, json_output=True))
        descriptions = {
            node["manifest"]["name"]: {
                **node["manifest"],
                "request_schema": node["request_schema"],
            }
            for node in payload["nodes"]
        }
        operation = descriptions["select_places"]
        assert operation["kind"] == "location_operation"
        assert operation["output_contract"]["family"][
            "structure"
        ] == OUTPUT.metadata.model_dump(mode="json")
        assert operation["output_contract"]["family"]["collection"] == "example:places"
        assert operation["output_contract"]["family"]["unit_type"] == "point"
        assert operation["resources"] == {"memory": "memory"}
        assert "Location operation: select_places" in render_description(tmp_path)
        assert "Location output:" in render_description(tmp_path)
        if metrics:
            assert (
                descriptions["root"]["inputs"]["data"]["locations"]["places"][
                    "operation"
                ]
                == "select_places"
            )
            assert descriptions["root"]["request_schema"]["properties"] == {}


def test_inspection_preserves_provider_scopes_without_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = Mock(side_effect=AssertionError("Inspection acquired provider"))
    monkeypatch.setattr(Provider, "setup", setup)
    definition = PluginDefinition(
        location_operations=[select_places],
        resources={"memory": MemoryIO, "locations": Provider},
        location_provider="locations",
    )
    write_project(tmp_path)
    monkeypatch.setattr(plugin_cli, "_load_definition", lambda *_: definition)
    payload = json.loads(render_description(tmp_path, json_output=True))
    provider = definition.location_provider
    assert provider is not None
    assert payload["location_provider"] == provider.model_dump(mode="json")
    assert {item["name"] for item in payload["location_provider"]["collections"]} == {
        "test:units",
        "test:roads",
    }
    assert all(
        item["crs"] and item["outputs"]
        for item in payload["location_provider"]["collections"]
    )
    setup.assert_not_called()


def test_copied_starter_loads_through_installed_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_path = Path(__file__).parents[1] / "examples/lyra-plugin"
    shutil.copytree(import_path, tmp_path, dirs_exist_ok=True)
    metadata = tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]
    entries = metadata["entry-points"]["lyra.plugins"]
    assert set(entries) == {"plugin"}
    monkeypatch.syspath_prepend(str(tmp_path))
    plugin_config(tmp_path)
    definition = load_installed_plugin_definition(metadata["name"])
    assert definition.metric_names
    assert {item.manifest.name for item in describe_plugin(tmp_path)} == set(
        definition.node_names
    )
    assert main(["diagram", "--project", str(tmp_path)]) == 0


def test_project_entry_point_is_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="missing-entry"\nversion="1"\n'
    )
    assert main(["describe", "--project", str(tmp_path)]) == 2
    assert 'project.entry-points."lyra.plugins"' in capsys.readouterr().err


def test_deployment_uses_shared_image_configuration_and_dependencies() -> None:
    root = Path(__file__).parents[1] / "examples/lyra-deployment"
    compose = safe_load((root / "compose.yml").read_text())
    services = [compose["services"][name] for name in ("api", "interactive", "batch")]
    assert len({service["image"] for service in services}) == 1
    assert all(service["build"] == services[0]["build"] for service in services)
    assert all(service["volumes"] == services[0]["volumes"] for service in services)
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    for filename in ("lyra.toml", "lyra.dev.toml"):
        config = LyraConfig.model_validate(tomllib.loads((root / filename).read_text()))
        assert config.build_revision
        assert all(
            plugin.distribution in project["dependencies"]
            for plugin in config.plugins.installed
            if plugin.enabled
        )
