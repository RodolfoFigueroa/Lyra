from pathlib import Path

import pytest
from lyra.sdk.dependencies import Dependency
from lyra.sdk.diagrams import render_mermaid
from lyra.sdk.plugin_cli import main

from tests.test_plugin_graph import manifest, node


def test_partial_complete_and_deterministic() -> None:
    consumer = manifest(
        "consumer",
        node(
            "metric",
            role="metric",
            inputs={"data": Dependency("table"), "other": Dependency("table")},
        ),
    )
    producer = manifest("producer", node("source", outputs=("table",)))
    partial = render_mermaid([consumer])
    assert partial.count("External output: table") == 1
    assert partial.count("-->") == 2
    complete = render_mermaid([consumer, producer])
    assert complete == render_mermaid([producer, consumer])
    assert "External output" not in complete
    assert "subgraph" in complete
    assert "Dataset: source" in complete
    assert "Metric: metric" in complete
    assert complete.endswith("\n")


def test_cycles_and_escaping() -> None:
    diagram = render_mermaid(
        [
            manifest('plugin"<&', node("end", inputs={"entrée": Dependency("end")})),
            manifest("other", node("isolated")),
        ]
    )
    assert "#34;#60;#38;" in diagram
    assert "entrée" in diagram
    assert "n0 -->" in diagram
    assert "isolated" in diagram


@pytest.mark.parametrize("kind", ["empty", "plugin", "node", "output", "collision"])
def test_ambiguous_input(kind: str) -> None:
    first = manifest("first", node("source", outputs=("table",)))
    second = manifest("second", node("other"))
    items = [first, second]
    if kind == "empty":
        items = []
    elif kind == "plugin":
        items = [first, first]
    elif kind == "node":
        second.nodes = [node("source")]
    elif kind == "output":
        second.nodes = [node("other", outputs=("table",))]
    else:
        second.nodes = [node("table")]
    with pytest.raises(ValueError, match=r"At least|Duplicate|collides"):
        render_mermaid(items)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "lyra.plugin.json"
    source.write_text(manifest("plugin", node("source")).model_dump_json())
    original = source.read_text()
    assert main(["diagram", "--project", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("flowchart LR")
    output = tmp_path / "diagram.mmd"
    assert main(["diagram", "--project", str(tmp_path), "--output", str(output)]) == 0
    assert output.read_text().startswith("flowchart LR")
    assert not capsys.readouterr().out
    assert main(["diagram", "--project", str(tmp_path), "--output", str(source)]) == 2
    assert source.read_text() == original
    source.write_text("{}")
    assert main(["diagram", "--project", str(tmp_path)]) == 2


def test_cli_included_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    primary = tmp_path / "lyra.plugin.json"
    primary.write_text(
        manifest(
            "root", node("root", inputs={"data": Dependency("table")})
        ).model_dump_json()
    )
    paths = []
    for name, output in [("producer", "table"), ("disconnected", "unused")]:
        path = tmp_path / f"{name}.json"
        path.write_text(manifest(name, node(name, outputs=(output,))).model_dump_json())
        paths.extend(["--include-manifest", str(path)])
    assert main(["diagram", "--project", str(tmp_path), *paths]) == 0
    result = capsys.readouterr().out
    assert "External output" not in result
    assert "disconnected" in result
    assert result.count("subgraph") == 3
    assert (
        main(
            [
                "diagram",
                "--project",
                str(tmp_path),
                "--include-manifest",
                str(tmp_path / "missing.json"),
            ]
        )
        == 2
    )
    assert "lyra-plugin:" in capsys.readouterr().err
