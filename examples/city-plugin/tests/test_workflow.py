"""Verify the public examples using the bundled dataset."""

import pytest
from city_example import create_plugin
from city_example.__main__ import run_chapter, selection
from city_example.checkpoints import checkpoint

from lyra.runtime.local import LocalRunner


@pytest.mark.parametrize("chapter", range(1, 8))
def test_checkpoint(chapter: int) -> None:
    """Every runnable chapter produces identified, validated tables."""
    tables = run_chapter(chapter)
    assert all(not table.empty for table in tables.values())


def test_block_allocation() -> None:
    """Allocate the shared street once across its two blocks."""
    tables = run_chapter(5)
    assert tables["summary"].to_dict("records") == [
        {
            "block_id": "block-1",
            "allocated_length_m": 200.0,
            "maintenance_length_m": 100.0,
        },
        {
            "block_id": "block-2",
            "allocated_length_m": 400.0,
            "maintenance_length_m": 300.0,
        },
        {
            "block_id": "block-empty",
            "allocated_length_m": 0.0,
            "maintenance_length_m": 0.0,
        },
    ]


def test_threshold_boundary_and_order() -> None:
    """Equality does not trigger maintenance, and selection order is retained."""
    runner = LocalRunner.from_definitions({"city": create_plugin()})
    table = runner.run(
        "city_street_maintenance",
        {
            "streets": selection("street", "street-3", "street-1"),
            "parameters": {"condition_threshold": 40},
        },
    )["maintenance"]
    assert table.street_id.tolist() == ["street-3", "street-1"]
    assert table.maintenance_length_m.tolist() == [0.0, 100.0]


def test_dependency_refactoring_preserves_calculation() -> None:
    """The graph chapter preserves the direct adapter's result."""
    request = {"streets": selection("street", "street-3", "street-1")}
    direct = LocalRunner.from_definitions({"city": checkpoint(3)})
    graph = LocalRunner.from_definitions({"city": checkpoint(4)})
    assert direct.run("city_street_maintenance", request)["maintenance"].equals(
        graph.run("city_street_maintenance", request)["maintenance"]
    )
