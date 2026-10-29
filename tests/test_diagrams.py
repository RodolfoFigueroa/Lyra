from pathlib import Path

import pytest
from lyra.sdk.dependencies import Dependency
from lyra.sdk.diagrams import render_mermaid
from lyra.sdk.graph import build_graph
from lyra.sdk.models.output import ColumnMetadata, OutputMetadata
from lyra.sdk.models.plugin import PluginManifest
from lyra.sdk.plugin_cli import main
from lyra.sdk.units import Unit

from tests.test_plugin_graph import manifest, node


def test_preview_fixture_matches_renderer() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "diagram-preview"
    plugin = PluginManifest.model_validate_json(
        (root / "lyra.plugin.json").read_text(encoding="utf-8")
    )
    expected = (
        "# Detailed diagram preview\n\n```mermaid\n"
        + render_mermaid([plugin], detailed=True)
        + "```\n"
    )
    assert (root / "preview.md").read_text(encoding="utf-8") == expected
    graph = build_graph({plugin.plugin.name: plugin.nodes})
    assert {
        edge.output.producer.node.name
        for edge in graph.dependencies["population_report"].values()
    } == {"population_source", "population_summary"}


def test_detailed_metric_inputs() -> None:
    declaration = node(
        "consumer",
        inputs={"totals": Dependency("summary"), "entrée": Dependency("areas")},
    )
    plugin = manifest("plugin", declaration)
    detailed = render_mermaid([plugin], detailed=True)
    assert (
        "<b>Inputs</b><br/>#8226;#160;entrée:#160;areas<br/>"
        "#8226;#160;totals:#160;summary<br/><br/><b>Output: consumer</b>" in detailed
    )
    assert "External output: areas" in detailed
    producers = manifest(
        "sources",
        node("population_source", outputs=("areas",)),
        node("population_summary", outputs=("summary",)),
    )
    resolved = render_mermaid([plugin, producers], detailed=True)
    assert "entrée:#160;population_source#46;areas" in resolved
    assert "totals:#160;population_summary#46;summary" in resolved
    assert resolved == render_mermaid([producers, plugin], detailed=True)
    assert "<b>Inputs</b>" not in render_mermaid([plugin])
    declaration.inputs = dict(reversed(list(declaration.inputs.items())))
    assert render_mermaid([plugin], detailed=True) == detailed
    declaration.inputs = {}
    assert "<b>Inputs</b>" not in render_mermaid([plugin], detailed=True)


def test_partial_complete_and_deterministic() -> None:
    consumer = manifest(
        "consumer",
        node(
            "metric",
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
    assert "Metric: source" in complete
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


def test_output_metadata() -> None:
    declaration = node("example")
    declaration.outputs = {
        "summary": OutputMetadata(
            columns=[
                ColumnMetadata(
                    name="population",
                    dtype="int64",
                    unit=Unit.COUNT,
                    description="Hidden description",
                    nullable=True,
                ),
                ColumnMetadata(
                    name="density",
                    dtype="float64",
                    unit=Unit.COUNT_PER_SQUARE_KILOMETRE,
                    description="Hidden description",
                ),
                ColumnMetadata(
                    name="code",
                    dtype="str",
                    unit=None,
                    description="Hidden description",
                ),
                ColumnMetadata(
                    name="ratio",
                    dtype="float64",
                    unit=Unit.DIMENSIONLESS,
                    description="Hidden description",
                ),
                ColumnMetadata(
                    name="shape", dtype="geometry", description="Geometry", unit=None
                ),
            ],
            geometry="shape",
        ),
        "empty": OutputMetadata(columns=[]),
    }
    plugin = manifest("plugin", declaration)
    plain = render_mermaid([plugin])
    assert '    n0["<b>Metric: example</b><br/><br/>' in plain
    separator = "<br/><br/>"
    assert f"<b>Output: empty</b>{separator}<b>Output: summary</b>" in plain
    assert f"<b>Output: empty</b>{separator}<b>Output: summary</b>" in render_mermaid(
        [plugin], detailed=True
    )
    assert "population:" not in plain
    detailed = render_mermaid([plugin], detailed=True)
    assert '    n0["<b>Metric: example</b><br/><br/>' in detailed
    assert (
        "#8226;#160;population:#160;#91;count#93;<br/>"
        "#8226;#160;density:#160;#91;count#47;km2#93;<br/>#8226;#160;code:#160;str<br/>"
        "#8226;#160;ratio:#160;#91;dimensionless#93;<br/>#8226;#160;Geometry:#160;shape"
        in detailed
    )
    assert "Hidden description" not in detailed
    assert "nullable" not in detailed
    declaration.outputs = dict(reversed(list(declaration.outputs.items())))
    assert render_mermaid([plugin], detailed=True) == detailed


def test_metadata_escaping() -> None:
    declaration = node("source")
    declaration.outputs = {
        "table": OutputMetadata(
            columns=[
                ColumnMetadata(
                    name='entrée"<&[]', dtype="str", unit=None, description="Unused"
                ),
                ColumnMetadata(
                    name="shape<br/>",
                    dtype="geometry",
                    description="Geometry",
                    unit=None,
                ),
            ],
            geometry="shape<br/>",
        )
    }
    result = render_mermaid([manifest("plugin", declaration)], detailed=True)
    assert (
        "#8226;#160;entrée#34;#60;#38;#91;#93;:#160;str<br/>"
        "#8226;#160;Geometry:#160;shape#60;br#47;#62;" in result
    )


@pytest.mark.parametrize("file_output", [False, True])
def test_detailed_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, file_output: bool
) -> None:
    declaration = node("source")
    declaration.outputs = {
        "spatial": OutputMetadata(
            columns=[
                ColumnMetadata(
                    name="shape", dtype="geometry", description="Geometry", unit=None
                )
            ],
            geometry="shape",
        )
    }
    plugin = manifest("plugin", declaration)
    (tmp_path / "lyra.plugin.json").write_text(plugin.model_dump_json())
    output = tmp_path / "diagram.mmd"
    args = ["diagram", "--project", str(tmp_path), "--detailed"]
    if file_output:
        args.extend(["--output", str(output)])
    assert main(args) == 0
    stdout = capsys.readouterr().out
    actual = output.read_text() if file_output else stdout
    assert actual == render_mermaid([plugin], detailed=True)
    assert "#8226;#160;Geometry:#160;shape" in actual
    if file_output:
        assert not stdout
