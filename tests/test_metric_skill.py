from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import create_autospec

import pandas as pd
import pytest
from lyra.sdk import MetricInputError, PluginDefinition, RunContext
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.plugin_cli import build_manifest, check_manifest, render_description
from lyra.utils.geometry import convert_geojson_to_gdf
from yaml import safe_load

from tests.smoke_plugin_helpers import feature_collection

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills/lyra-metric"
FIXTURES = ROOT / "tests/fixtures/skill_workflows"
VALUES = {"parameter_1": 3, "parameter_2": 0.5, "parameter_3": ["urban"]}


@pytest.fixture
def adapted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ModuleType, Path]]:
    reference = (SKILL / "references/authoring.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", reference, re.DOTALL)
    blocks = [block for block in blocks if "def create_plugin()" in block]
    assert len(blocks) == 1
    (tmp_path / "skill_trial_adapter.py").write_text(blocks[0], encoding="utf-8")
    shutil.copyfile(FIXTURES / "workflow.py", tmp_path / "workflow.py")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "skill-trial"\nversion = "0.1.0"\n'
        '[tool.lyra]\nfactory = "skill_trial_adapter:create_plugin"\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("workflow", "skill_trial_adapter"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("skill_trial_adapter")
    yield module, tmp_path
    for name in ("workflow", "skill_trial_adapter"):
        sys.modules.pop(name, None)


@pytest.fixture
def location() -> GeoJSON:
    payload = feature_collection(("zone-z", "zone-a", "zone-m"))
    for feature, value, category in zip(
        payload["features"], [2.0, 5.0, 7.0], ["urban", "rural", "urban"], strict=True
    ):
        feature["properties"] = {"base_value": value, "category": category}
    return GeoJSON.model_validate(payload)


def test_skill_structure_and_local_references() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)
    assert len(frontmatter) == 3
    metadata = safe_load(frontmatter[1])
    assert metadata["name"] == "lyra-metric"
    assert isinstance(metadata["description"], str)
    assert metadata["description"].strip()
    links = re.findall(r"\]\((references/[^)#]+)(?:#[^)]*)?\)", text)
    assert links
    for target in links:
        resolved = (SKILL / target).resolve()
        assert resolved.is_relative_to(SKILL.resolve())
        assert resolved.is_file()


def test_reference_preserves_calculation_and_feature_order(
    adapted: tuple[ModuleType, Path], location: GeoJSON
) -> None:
    module, _ = adapted
    plugin = module.create_plugin()
    assert isinstance(plugin, PluginDefinition)
    parameters = plugin.prepare_parameters("zone_score", VALUES)
    actual = module.run(location=location, parameters=parameters)
    workflow = importlib.import_module("workflow")
    expected = workflow.calculate_metric(
        convert_geojson_to_gdf(location),
        parameter_1=3,
        parameter_2=0.5,
        parameter_3=["urban"],
    )
    pd.testing.assert_frame_equal(actual, expected)
    result = plugin.normalize_result("zone_score", actual)
    assert isinstance(result, dict)
    result = next(iter(result.values()))
    assert result.index.tolist() == ["zone-z", "zone-a", "zone-m"]
    assert result.to_numpy().tolist() == [[6.5], [0.0], [21.5]]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {**VALUES, "parameter_1": "3"},
        {**VALUES, "parameter_2": float("inf")},
        {**VALUES, "parameter_3": [1]},
        {**VALUES, "unexpected": True},
    ],
)
def test_reference_rejects_invalid_parameters(
    adapted: tuple[ModuleType, Path], values: object
) -> None:
    module, _ = adapted
    with pytest.raises(MetricInputError):
        module.create_plugin().prepare_parameters("zone_score", values)


@pytest.mark.parametrize("defect", ["row_order", "missing_row", "column"])
def test_reference_preserves_producer_owned_rows_and_columns(
    adapted: tuple[ModuleType, Path], location: GeoJSON, defect: str
) -> None:
    module, _ = adapted
    plugin = module.create_plugin()
    result = module.run(
        location=location,
        parameters=plugin.prepare_parameters("zone_score", VALUES),
    )
    if defect == "row_order":
        result = result.iloc[::-1]
    elif defect == "missing_row":
        result = result.iloc[:2]
    else:
        result = result.rename(columns={"score": "unknown"})
    assert plugin.normalize_result("zone_score", result)["zone_score"] is result


def test_reference_metadata_survives_manifest_and_inspection(
    adapted: tuple[ModuleType, Path],
) -> None:
    module, project = adapted
    description = module.create_plugin().describe("zone_score")
    assert "\n\n" in description.description
    generated = json.loads(build_manifest(project).read_text(encoding="utf-8"))
    inspected = json.loads(render_description(project, "zone_score", json_output=True))
    assert generated["nodes"][0] == description.model_dump(
        mode="json", exclude={"handler", "signature"}, exclude_unset=True
    )
    assert inspected["nodes"][0] == description.model_dump(
        mode="json", exclude_unset=True
    )
    parameter_schema = description.request_schema["properties"]["parameters"]
    while "$ref" in parameter_schema:
        reference = parameter_schema["$ref"]
        assert reference.startswith("#/")
        parameter_schema = description.request_schema
        for key in reference[2:].split("/"):
            parameter_schema = parameter_schema[
                key.replace("~1", "/").replace("~0", "~")
            ]
    for name, field in module.Parameters.model_fields.items():
        assert parameter_schema["properties"][name]["description"] == field.description
    assert (
        generated["nodes"][0]["outputs"]["zone_score"]["columns"][0]["description"]
        == description.outputs["zone_score"].columns[0].description
    )
    assert description.description in render_description(project, "zone_score")


def test_reference_manifest_generation_and_drift(
    adapted: tuple[ModuleType, Path],
) -> None:
    _, project = adapted
    manifest_path = build_manifest(project)
    assert check_manifest(project) == (True, "")
    manifest_path.write_text("{}\n", encoding="utf-8")
    valid, difference = check_manifest(project)
    assert not valid
    assert "generated:" in difference


def test_reference_extends_copied_plugin_and_preserves_existing_behavior(
    adapted: tuple[ModuleType, Path],
    location: GeoJSON,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, project = adapted
    copied = project / "existing"
    shutil.copytree(ROOT / "examples/lyra-plugin", copied)
    monkeypatch.syspath_prepend(str(copied))
    for name in ("smoke_plugin", "smoke_plugin.metrics", "smoke_plugin.plugin"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        existing_module = importlib.import_module("smoke_plugin.plugin")
        metrics = importlib.import_module("smoke_plugin.metrics")
        original = existing_module.create_plugin()
        combined = PluginDefinition(
            nodes=[
                metrics.run_table,
                metrics.run_summary,
                metrics.run_progress,
                existing_module.city_source,
                existing_module.city_population,
                existing_module.selected_source,
                existing_module.population_baseline,
                module.run,
            ]
        )
        assert set(combined.metric_names) == {*original.metric_names, "zone_score"}
        for name in original.metric_names:
            assert combined.describe(name) == original.describe(name)
        context = create_autospec(RunContext, instance=True)
        context.temp_dir = project / "artifacts"
        context.temp_dir.mkdir()
        for name, handler in (
            ("smoke_table_metric", metrics.run_table),
            ("smoke_progress_metric", metrics.run_progress),
        ):
            frame = handler(
                location=location,
                parameters=combined.prepare_parameters(name, {"value": 7}),
                context=context,
            )
            result = combined.normalize_result(name, frame)
            assert isinstance(result, dict)
            result = next(iter(result.values()))
            assert result.index.tolist() == ["zone-z", "zone-a", "zone-m"]
            assert result.to_numpy().tolist() == [[7], [7], [7]]
        summary = metrics.run_summary(location=location)
        result = combined.normalize_result("smoke_summary_metric", summary)
        assert isinstance(result, dict)
        result = next(iter(result.values()))
        assert result.to_numpy().tolist() == [[1], [1], [1]]
        frame = module.run(
            location=location,
            parameters=combined.prepare_parameters("zone_score", VALUES),
        )
        result = combined.normalize_result("zone_score", frame)
        assert isinstance(result, dict)
        result = next(iter(result.values()))
        assert result.to_numpy().tolist() == [[6.5], [0.0], [21.5]]
    finally:
        for name in ("smoke_plugin", "smoke_plugin.metrics", "smoke_plugin.plugin"):
            sys.modules.pop(name, None)
