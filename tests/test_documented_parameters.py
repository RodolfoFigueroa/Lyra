from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "reference",
    [
        "docs/src/content/docs/reference/parameters.md",
        "skills/lyra-metric/references/authoring.md",
    ],
)
def test_effective_parameter_example_survives_json_transport(
    reference: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocks = re.findall(
        r'```python title="effective_parameters.py"\n(.*?)```',
        (ROOT / reference).read_text(),
        re.DOTALL,
    )
    assert blocks
    path = tmp_path / "effective_parameters.py"
    path.write_text("\n".join(blocks))
    spec = importlib.util.spec_from_file_location("effective_parameters", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    parameters = module.Parameters(
        label=" north ", filters={"tags": ["street"]}, options={"enabled": True}
    )
    restored = module.Parameters.model_validate_json(parameters.model_dump_json())
    assert restored.model_dump(mode="json") == parameters.model_dump(mode="json")
    restored.filters.tags.append("park")
    assert parameters.filters.tags == ["street"]
    assert module.Parameters().filters.tags == []
