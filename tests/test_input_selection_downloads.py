"""Bounded input selection and merged Arrow downloads with isolated SQL storage."""

from pathlib import Path
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest

from lyra.contracts.errors import (
    LocationInputUnavailableError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.runtime.input_selection import InputRows
from lyra.runtime.location_inputs import InputBindings, LocationInputs
from lyra.runtime.operation_pairs import OperationPairs, operation_registration
from lyra.runtime.result_descriptions import ResultDescriptions
from lyra.runtime.result_store import ResultStore
from lyra.runtime.sqlite_store import SQLiteStore
from test_support.publication import publish_result
from tests.location_input_helpers import publish_inputs, retained
from tests.result_description_helpers import publish_values
from tests.sqlite_helpers import Database, database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


def union_result(store: SQLiteStore) -> str:
    direct, pairs = retained(store)
    operation = pairs.operations[0]
    second = operation.header.model_copy(
        update={"value_key": "second", "node_id": "other_operation"}
    )
    OperationPairs(store).publish(
        ((second, ("A", "B", "C")),), {"A": ("a",), "B": ("a", "b"), "C": ("b",)}
    )
    inputs = LocationInputs(store)
    selections = {
        "a": (direct, direct.model_copy(update={"records": ("a",)})),
        "pairs": (
            pairs.model_copy(
                update={"operations": (operation.model_copy(update={"targets": None}),)}
            ),
            pairs.model_copy(
                update={
                    "operations": (
                        operation.model_copy(
                            update={
                                "header": second,
                                "sources": ("A", "B"),
                                "targets": ("a",),
                            }
                        ),
                    )
                }
            ),
            pairs,
        ),
    }
    description = publish_values(store, ("one", "two"))
    description = description.model_copy(
        update={
            "location_inputs": InputBindings(
                names=tuple(selections),
                selections={
                    name: tuple(inputs.publish(selection) for selection in values)
                    for name, values in selections.items()
                },
            )
        }
    )
    return publish_result(ResultDescriptions(store), description)


@pytest.mark.parametrize("name", ["a", "pairs"])
def test_union_traversal_without_eager_reads(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    reference = union_result(store)
    reader = ResultStore(store)
    expected = reader.read_location_input(reference, name)
    # Input access does not depend on metric output values or historical reports.
    database.rows["final_results"][0]["report"] = "{"
    for registration in store.layout.descriptions.values():
        if registration.kind == "metric":
            database.rows.get(
                store.layout.tables[registration.key].table.name, []
            ).clear()
    forbidden = Mock(side_effect=AssertionError("eager input read"))
    monkeypatch.setattr(ResultStore, "read_location_input", forbidden)
    monkeypatch.setattr(LocationInputs, "frame", forbidden)
    monkeypatch.setattr(InputRows, "frame", forbidden)
    monkeypatch.setattr(OperationPairs, "read_connection", forbidden)
    assert len(expected) == (3 if name == "pairs" else 2)
    native = gpd.GeoDataFrame.from_arrow(
        pa.ipc.open_stream(
            b"".join(reader.stream_location_input(reference, name))
        ).read_all()
    )
    pd.testing.assert_frame_equal(native, expected)


@pytest.mark.parametrize(
    "damage",
    [
        "record",
        "header",
        "partition",
        "pair",
        "duplicate",
        "completion",
        "schema",
        "selection",
    ],
)
def test_required_damage_is_integrity_failure(
    database: Database, tmp_path: Path, damage: str
) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, pairs = retained(store)
    reference = publish_inputs(store, {"pairs": pairs})
    data = database.rows[store.layout.tables[direct.registration.key].table.name]
    operations = database.rows[
        store.layout.tables[operation_registration("nearby").key].table.name
    ]
    if damage == "record":
        data.clear()
    elif damage == "pair":
        operations.clear()
    elif damage == "duplicate":
        operations.append(dict(operations[0]))
    elif damage == "header":
        database.rows["stored_results"][0]["node_id"] = "wrong"
    elif damage == "partition":
        database.rows["stored_partitions"][0]["location_id"] = "wrong"
    elif damage == "completion":
        next(
            row
            for row in database.rows["stored_results"]
            if row["value_key"] == "operation"
        )["output_rows"] = None
    elif damage == "schema":
        database.rows["storage_registrations"].clear()
    else:
        database.rows["input_selections"].clear()
    reader = ResultStore(SQLiteStore(store.path))
    with pytest.raises(ResultIntegrityError) as caught:
        b"".join(reader.stream_location_input(reference, "pairs"))
    assert caught.value.input_name == "pairs"
    assert caught.value.output_name is None


def test_empty_unavailable_and_missing_preflight(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    direct, pairs = retained(store)
    empty = direct.model_copy(update={"records": ()})
    skipped = empty.model_copy(
        update={
            "description": empty.description.model_copy(
                update={"availability": "not_materialized"}
            )
        }
    )
    zero = pairs.model_copy(
        update={
            "operations": (
                pairs.operations[0].model_copy(update={"sources": ("empty",)}),
            )
        }
    )
    reference = publish_inputs(
        store, {"empty": empty, "zero": zero, "skipped": skipped}
    )
    reader = ResultStore(store)
    for name in ("empty", "zero"):
        reader.validate_location_input(reference, name)
        table = pa.ipc.open_stream(
            b"".join(reader.stream_location_input(reference, name))
        ).read_all()
        assert table.num_rows == 0
        native = gpd.GeoDataFrame.from_arrow(table)
        pd.testing.assert_frame_equal(
            native, reader.read_location_input(reference, name)
        )
        assert native.crs is not None
        assert native.crs.to_epsg() == 4326
    for name, error in (
        ("skipped", LocationInputUnavailableError),
        ("unknown", ResultNotFoundError),
    ):
        with pytest.raises(error):
            next(reader.stream_location_input(reference, name))
        with pytest.raises(error):
            reader.validate_location_input(reference, name)


def test_dense_pair_union_across_batches(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "inputs.sqlite")
    _, selection = retained(store)
    sources = tuple(f"source-{i:04}" for i in range(550))
    operation = selection.operations[0]
    header = operation.header.model_copy(update={"value_key": "dense"})
    OperationPairs(store).publish(
        ((header, sources),), dict.fromkeys(sources, ("a", "b"))
    )
    selection = selection.model_copy(
        update={
            "operations": (
                operation.model_copy(
                    update={"header": header, "sources": sources, "targets": None}
                ),
                operation.model_copy(
                    update={
                        "header": header,
                        "sources": sources[100:400],
                        "targets": None,
                    }
                ),
            )
        }
    )
    reference = publish_inputs(store, {"pairs": selection})
    reader = ResultStore(store)
    expected = {(source, target) for source in sources for target in ("a", "b")}
    table = pa.ipc.open_stream(
        b"".join(reader.stream_location_input(reference, "pairs"))
    ).read_all()
    assert table.num_rows == len(expected)
    assert (
        set(
            zip(
                table["source_location_id"].to_pylist(),
                table["location_id"].to_pylist(),
                strict=True,
            )
        )
        == expected
    )
