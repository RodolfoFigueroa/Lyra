"""Combined fidelity through runtime selections, standalone reads, and cache hits."""

from pathlib import Path

import pandas as pd
import pytest

from lyra.runtime.artifacts import ArtifactStore
from lyra.runtime.local import LocalRunner
from lyra.sdk.dependencies import Dependency
from lyra.sdk.plugin import PluginDefinition, metric
from lyra.server import worker
from tests.queue_helpers import accepted_envelope
from tests.storage_data import native_declarations as declarations
from tests.storage_data import native_frame as frame
from tests.storage_roundtrip import assert_equal
from tests.worker_graph_integration_helpers import install_definitions, native_job


@pytest.mark.usefixtures("worker_state")
@pytest.mark.parametrize("state", ["empty", "all-null"])
@pytest.mark.parametrize("spatial", [False, True])
def test_nullable_results_across_execution_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, *, spatial: bool
) -> None:
    schema = declarations(spatial=spatial).remove_columns(["category"])
    produced = frame(state, spatial=spatial).drop(columns="category")
    expected = produced[list(schema.columns)].reset_index(drop=True)

    @metric(
        name="root",
        description="Nullable native outputs",
        code_revision="1",
        outputs={"first": schema, "second": schema},
    )
    def root() -> dict[str, pd.DataFrame]:
        return {"first": produced, "second": produced}

    definitions = {"nullable": PluginDefinition(metrics=[root])}
    _, runtime = install_definitions(tmp_path, monkeypatch, definitions)
    runner = LocalRunner.from_definitions(definitions)
    local = runner.run("root")
    development = runner.run_development("root")
    envelope = accepted_envelope(
        job_id="nullable-job",
        metric="root",
        inputs={},
        catalog=runtime.catalog.compiled,
    )
    native = native_job(monkeypatch)
    native.id = envelope.job_id
    result = worker.run_metric_task(envelope.model_dump(mode="json"))
    store = ArtifactStore(runtime.config.artifact_dir)
    key = str(result["artifact_id"])
    assert (
        store.descriptions(key)
        == local.metadata.outputs
        == development.metadata.outputs
    )
    for tables in (local.tables, development.tables, store.read(key)):
        for output in ("first", "second"):
            assert_equal(tables[output], expected)
        del tables["first"]["signed"]
        assert_equal(tables["second"], expected)
    assert_equal(store.read(key)["first"], expected)
    assert_equal(produced, expected)


@pytest.mark.parametrize(
    "state", ["populated", "empty", "all-null", "indexed", "shuffled"]
)
@pytest.mark.parametrize("spatial", [False, True])
def test_metric_fidelity_reaches_consumer(
    tmp_path: Path, state: str, *, spatial: bool
) -> None:
    schema = declarations(spatial=spatial).remove_columns(["category"])
    for name, column in schema.columns.items():
        column.description = name
        column.metadata = {"unit": None}
    produced = frame(state, spatial=spatial).drop(columns="category")
    expected = produced[list(schema.columns)].reset_index(drop=True)
    calls = []

    @metric(
        cache="full",
        code_revision="1",
        name="source",
        description="Source",
        outputs={"rows": schema},
    )
    def source() -> pd.DataFrame:
        calls.append("source")
        return produced

    @metric(
        cache="full",
        code_revision="1",
        name="consumer",
        description="Consumer",
        outputs={"result": schema},
        inputs={"rows": Dependency("rows", producer="source")},
    )
    def consumer(rows: pd.DataFrame) -> pd.DataFrame:
        calls.append("consumer")
        assert_equal(rows, expected)
        return rows

    runner = LocalRunner.from_definitions(
        {"acceptance": PluginDefinition(metrics=[source, consumer])},
        artifact_dir=tmp_path,
    )
    assert_equal(runner.run("source", inputs={}).tables["rows"], expected)
    assert calls == ["source"]
    assert_equal(runner.run("consumer", inputs={}).tables["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(runner.run("consumer", inputs={}).tables["result"], expected)
    assert calls == ["source", "consumer"]
    assert_equal(produced, frame(state, spatial=spatial).drop(columns="category"))
