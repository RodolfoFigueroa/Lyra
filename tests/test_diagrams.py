from pathlib import Path
from typing import Literal

import pytest
from lyra.sdk.dependencies import Dependency
from lyra.sdk.diagrams import render_mermaid
from lyra.sdk.models.plugin import TableColumn, TableOutput
from lyra.sdk.plugin_cli import main
from lyra.sdk.units import Unit

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


@pytest.mark.parametrize("role", ["dataset", "metric"])
def test_output_metadata(role: Literal["dataset", "metric"]) -> None:
    declaration = node("example", role=role)
    declaration.outputs = {
        "summary": TableOutput(
            columns=[
                TableColumn(
                    name="population",
                    type="integer",
                    unit=Unit.COUNT,
                    description="Hidden description",
                    nullable=True,
                ),
                TableColumn(
                    name="density",
                    type="number",
                    unit=Unit.COUNT_PER_SQUARE_KILOMETRE,
                    description="Hidden description",
                ),
                TableColumn(
                    name="code",
                    type="string",
                    unit=None,
                    description="Hidden description",
                ),
                TableColumn(
                    name="ratio",
                    type="number",
                    unit=Unit.DIMENSIONLESS,
                    description="Hidden description",
                ),
            ],
            geometry="shape",
        ),
        "empty": TableOutput(columns=[]),
    }
    plugin = manifest("plugin", declaration)
    plain = render_mermaid([plugin])
    assert "Output: empty<br/>Output: summary" in plain
    assert "population:" not in plain
    detailed = render_mermaid([plugin], detailed=True)
    assert (
        "population: integer #91;count#93;<br/>"
        "density: number #91;count#47;km2#93;<br/>code: string<br/>"
        "ratio: number #91;dimensionless#93;<br/>Geometry: shape" in detailed
    )
    assert "Hidden description" not in detailed
    assert "nullable" not in detailed
    declaration.outputs = dict(reversed(list(declaration.outputs.items())))
    assert render_mermaid([plugin], detailed=True) == detailed


def test_metadata_escaping() -> None:
    declaration = node("source")
    declaration.outputs = {
        "table": TableOutput(
            columns=[
                TableColumn(
                    name='entrée"<&[]', type="string", unit=None, description="Unused"
                )
            ],
            geometry="shape<br/>",
        )
    }
    result = render_mermaid([manifest("plugin", declaration)], detailed=True)
    assert (
        "entrée#34;#60;#38;#91;#93;: string<br/>Geometry: shape#60;br#47;#62;" in result
    )


@pytest.mark.parametrize("file_output", [False, True])
def test_detailed_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, file_output: bool
) -> None:
    declaration = node("source")
    declaration.outputs = {"spatial": TableOutput(columns=[], geometry="shape")}
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
    assert "Geometry: shape" in actual
    if file_output:
        assert not stdout
