from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

from lyra.sdk import (
    Dependency,
    LocationDependency,
    PluginDefinition,
    metric,
    plugin_cli,
)
from lyra.sdk.plugin_cli import (
    build_manifest,
    check_manifest,
    describe_plugin,
    main,
    render_description,
    render_manifest,
)
from pre_commit.clientlib import load_config

from tests.test_location_operations import OUTPUT, MemoryIO, select_places
from tests.test_location_resolution import Provider
from tests.test_mixed_execution import TABLE

if TYPE_CHECKING:
    import pytest


def _write_project(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        """
[project]
name = "example-plugin"
version = "1.2.3"

[tool.lyra]
factory = "example_plugin:create_plugin"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (project / "example_plugin.py").write_text(
        """from pydantic import Field
from lyra.sdk import MetricParameters, Locations, PluginDefinition, Unit, metric
import pandera.pandas as pa


class Parameters(MetricParameters):
    value: int = Field(default=2, description="Example input value.")


@metric(
    name="example",
    description="Example metric.",
    outputs={
        "example": pa.DataFrameSchema(
            {
                "value": pa.Column(
                    int, description="Example value.", metadata={"unit": Unit.COUNT}
                )
            }
        )
    },
)
def calculate(location: Locations, parameters: Parameters):
    raise AssertionError


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[calculate])
""".lstrip(),
        encoding="utf-8",
    )


def test_build_and_check_manifest_are_deterministic(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    manifest_path = build_manifest(tmp_path)
    first = manifest_path.read_text(encoding="utf-8")
    second = render_manifest(tmp_path)

    assert first == second
    assert first.endswith("\n")
    payload = json.loads(first)
    assert payload["plugin"] == {"name": "example-plugin", "version": "1.2.3"}
    assert payload["factory"] == "example_plugin:create_plugin"
    assert "entrypoint" not in payload["nodes"][0]
    assert check_manifest(tmp_path) == (True, "")

    manifest_path.write_text("{}\n", encoding="utf-8")
    valid, diff = check_manifest(tmp_path)
    assert valid is False
    assert "generated:" in diff
    assert manifest_path.read_text(encoding="utf-8") == "{}\n"


def test_cli_exit_codes_and_project_errors(
    tmp_path: Path,
    capsys: object,
) -> None:
    del capsys
    assert main(["check-manifest", "--project", str(tmp_path)]) == 2

    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    assert main(["check-manifest", "--project", str(tmp_path)]) == 1
    assert main(["build-manifest", "--project", str(tmp_path)]) == 0
    assert main(["check-manifest", "--project", str(tmp_path)]) == 0


def test_describe_plugin_renders_human_and_json_output(tmp_path: Path) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    descriptions = describe_plugin(tmp_path)
    assert [description.name for description in descriptions] == ["example"]
    assert descriptions[0].request_schema["$defs"]["Parameters"]["properties"]["value"][
        "description"
    ] == ("Example input value.")

    human = render_description(tmp_path, "example")
    assert "Metric: example" in human
    assert "Signature: calculate(location:" in human
    assert "Example input value." in human
    assert "Output example: table (1 source column(s))" in human

    payload = json.loads(render_description(tmp_path, json_output=True))
    assert payload["nodes"][0]["name"] == "example"
    assert payload["nodes"][0]["request_schema"]["$defs"]["Parameters"]["properties"][
        "value"
    ]["description"] == ("Example input value.")


def test_describe_cli_reports_unknown_metrics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)

    assert main(["describe", "example", "--project", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["nodes"][0]["name"] == "example"

    assert main(["describe", "missing", "--project", str(tmp_path)]) == 2
    assert "available nodes: example" in capsys.readouterr().err


def test_manifest_generation_preserves_explicit_null_unit(tmp_path: Path) -> None:
    _write_project(tmp_path)
    source = tmp_path / "example_plugin.py"
    source.write_text(source.read_text().replace("Unit.COUNT", "None"))
    sys.modules.pop("example_plugin", None)
    path = build_manifest(tmp_path)
    manifest = json.loads(path.read_text())
    assert manifest["nodes"][0]["outputs"]["example"]["columns"][0]["unit"] is None
    assert check_manifest(tmp_path) == (True, "")
    sys.modules.pop("example_plugin", None)


def test_describe_metric_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_project(tmp_path)
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
    _write_project(tmp_path)
    path = tmp_path / "example_plugin.py"
    source = (
        path.read_text()
        .replace(
            "from lyra.sdk import MetricParameters,",
            "from lyra.sdk import Dependency, MetricParameters,",
        )
        .replace(
            '    name="example",',
            '    name="example",\n'
            '    inputs={"foreign": Dependency("uninstalled_output")},',
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


def test_static_hook_configuration() -> None:
    root = Path(__file__).parents[1]
    example = root / "examples/lyra-plugin/.pre-commit-config.yaml"
    hook = load_config(str(example))["repos"][0]["hooks"][0]
    assert hook["entry"] == "uv run lyra-plugin build-manifest"
    assert hook["always_run"]
    assert not hook["pass_filenames"]


def test_manifest_atomic_failure_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project(tmp_path)
    sys.modules.pop("example_plugin", None)
    manifest = tmp_path / "lyra.plugin.json"
    manifest.write_text("original")

    def fail_replace(_self: Path, _target: Path) -> None:
        message = "replace failed"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)
    assert main(["build-manifest", "--project", str(tmp_path)]) == 2
    assert manifest.read_text() == "original"
    assert not list(tmp_path.glob(".lyra.plugin.json.*"))


def test_inspection_of_operations_and_nested_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @metric(
        name="root",
        description="Root",
        inputs={
            "data": Dependency(
                "foreign", locations={"places": LocationDependency("select_places")}
            )
        },
        outputs={"root": TABLE},
    )
    def root(data: object) -> object:
        del data
        raise AssertionError

    _write_project(tmp_path)
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
        descriptions = {node["name"]: node for node in payload["nodes"]}
        operation = descriptions["select_places"]
        assert operation["kind"] == "location_operation"
        assert operation["location_output"] == {
            **OUTPUT.metadata.model_dump(mode="json"),
            "scope": {"kind": "input", "path": "source"},
        }
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
    _write_project(tmp_path)
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
