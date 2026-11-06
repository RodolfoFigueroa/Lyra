from pathlib import Path

import pytest
from city_example import create_plugin
from city_example.__main__ import run_chapter, selection
from city_example.checkpoints import checkpoint
from city_example.data import load_city
from city_example.provider import CityLocationProvider

from lyra.contracts.errors import LocationAcquisitionError, UnresolvedLocationIDsError
from lyra.contracts.resources import IOConfig
from lyra.runtime.execution import GraphExecutionError
from lyra.runtime.local import LocalRunner
from lyra.runtime.planning.preparation import PreparationError
from lyra.sdk.plugin_cli import render_manifest


@pytest.mark.parametrize("chapter", range(1, 8))
def test_city_checkpoints(chapter: int) -> None:
    tables = run_chapter(chapter)
    assert all(not frame.empty for frame in tables.values())


def test_city_data_does_not_depend_on_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert [street.id for street in load_city().streets] == [
        "street-1",
        "street-2",
        "street-3",
    ]
    assert run_chapter(1)["lengths"].length_m.tolist() == [100.0, 200.0, 300.0]


def test_city_selected_checkpoint_preserves_deduplicated_order() -> None:
    runner = LocalRunner.from_definitions({"city": checkpoint(2)})
    table = runner.run(
        "city_street_lengths",
        {"streets": selection("street", "street-3", "street-1", "street-3")},
    )["lengths"]
    assert table.to_dict("records") == [
        {"street_id": "street-3", "length_m": 300.0},
        {"street_id": "street-1", "length_m": 100.0},
    ]


def test_city_direct_and_dependency_results_match() -> None:
    request = {
        "streets": selection("street", "street-3", "street-1"),
        "parameters": {"condition_threshold": 40},
    }
    direct = LocalRunner.from_definitions({"city": checkpoint(3)})
    graph = LocalRunner.from_definitions({"city": checkpoint(4)})
    expected = direct.run("city_street_maintenance", request)["maintenance"]
    actual = graph.run("city_street_maintenance", request)["maintenance"]
    assert actual.equals(expected)
    assert actual.maintenance_length_m.tolist() == [0.0, 100.0]


def test_city_blocks_allocate_shared_street_and_keep_empty_block() -> None:
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_block_maintenance",
        {
            "blocks": selection("block", "block-2", "block-empty", "block-1"),
            "parameters": {"condition_threshold": 90},
        },
    )["summary"]
    assert table.block_id.tolist() == ["block-2", "block-empty", "block-1"]
    assert table.allocated_length_m.tolist() == [400.0, 0.0, 200.0]
    assert table.maintenance_length_m.tolist() == [400.0, 0.0, 200.0]
    empty = runner.run(
        "city_block_maintenance",
        {"blocks": selection("block", "block-empty")},
    )["summary"]
    assert empty.to_dict("records") == [
        {
            "block_id": "block-empty",
            "allocated_length_m": 0.0,
            "maintenance_length_m": 0.0,
        }
    ]


def test_city_canonical_selection_is_union_without_aggregation() -> None:
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance", {"streets": selection("block", "block-1", "block-2")}
    )["maintenance"]
    assert table.street_id.tolist() == ["street-1", "street-2", "street-3"]
    assert table.maintenance_length_m.tolist() == [100.0, 0.0, 300.0]
    empty = runner.run(
        "city_street_maintenance", {"streets": selection("block", "block-empty")}
    )["maintenance"]
    assert empty.empty
    assert empty.columns.tolist() == table.columns.tolist()
    assert empty.dtypes.equals(table.dtypes)


def test_city_rejects_invalid_threshold_before_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_setup(_self: CityLocationProvider) -> None:
        pytest.fail("Preparation must reject this request before acquisition")

    monkeypatch.setattr(CityLocationProvider, "setup", forbidden_setup)
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    with pytest.raises(PreparationError):
        runner.run(
            "city_street_maintenance",
            {
                "streets": selection("street", "street-1"),
                "parameters": {"condition_threshold": 101},
            },
        )


def test_city_unknown_id_fails_with_resolution_context() -> None:
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    with pytest.raises(GraphExecutionError) as error:
        runner.run("city_street_lengths", {"streets": selection("street", "missing")})
    assert isinstance(error.value.__cause__, UnresolvedLocationIDsError)


def test_city_manifest_does_not_acquire_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_setup(_self: CityLocationProvider) -> None:
        pytest.fail("Manifest generation must not acquire resources")

    monkeypatch.setattr(CityLocationProvider, "setup", forbidden_setup)
    project = Path(__file__).parents[1] / "examples/city-plugin"
    assert render_manifest(project) == (project / "lyra.plugin.json").read_text()


def test_city_provider_rejects_an_unavailable_pinned_revision() -> None:
    provider = CityLocationProvider(IOConfig(), {})
    provider.setup()
    pinned = provider.contracts[0].model_copy(update={"data_revision": "unavailable"})
    try:
        with pytest.raises(LocationAcquisitionError):
            provider.resolve(pinned, ["street-1"])
    finally:
        provider.close()
