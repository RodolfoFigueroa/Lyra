"""Native location ownership and scope-aware logical comparison."""

from dataclasses import replace

import pandas as pd
import pytest

from lyra.contracts.locations import LocationContractReference, ProviderScope
from lyra.runtime.native_inputs import NativeLocationInput, copy_frame, inputs_equal
from tests.provider_schema_helpers import frame


def native() -> NativeLocationInput:
    value = frame()
    value["sequence"] = range(len(value))
    value.attrs = {"nested": {"values": [1]}}
    return NativeLocationInput(
        value,
        LocationContractReference(
            collection="records",
            unit_type="record",
            source="records",
            geographical_edition="provider",
            provider_scope=ProviderScope(provider_id="records", code_revision="1"),
        ),
    )


def test_row_column_order_and_index_do_not_change_contents() -> None:
    original = native()
    permuted = original.frame.iloc[::-1, ::-1].copy()
    permuted.index = pd.RangeIndex(100, 100 + len(permuted))
    assert inputs_equal(original, replace(original, frame=permuted))
    assert inputs_equal(
        replace(original, frame=original.frame.iloc[:0]),
        replace(original, frame=permuted.iloc[:0]),
    )


@pytest.mark.parametrize(
    "change", ["value", "type", "geometry", "crs", "id", "sequence", "column"]
)
def test_changed_native_contents_are_unequal(change: str) -> None:
    original = native()
    changed = copy_frame(original.frame)
    if change == "value":
        changed["count"] = 99
    elif change == "type":
        changed["sequence"] = changed.sequence.astype("float64")
    elif change == "geometry":
        changed.geometry = changed.geometry.translate(xoff=100)
    elif change == "crs":
        changed = changed.set_crs(3857, allow_override=True)
    elif change == "id":
        changed.loc[changed.index[0], "location_id"] = "other"
    elif change == "sequence":
        changed["sequence"] += 1
    else:
        changed = changed.rename(columns={"sequence": "order"})
    assert not inputs_equal(original, replace(original, frame=changed))


def test_owned_frames_detach_values_and_nested_metadata() -> None:
    original = native()
    expected = copy_frame(original.frame)
    working = copy_frame(original.frame)
    working["sequence"] = 99
    working.geometry = working.geometry.translate(xoff=90)
    working.attrs["nested"]["values"].append(2)
    pd.testing.assert_frame_equal(original.frame, expected)
    assert original.frame.attrs == {"nested": {"values": [1]}}
    source = copy_frame(original.frame)
    captured = replace(original, frame=source)
    source["sequence"] = 100
    source.attrs["nested"]["values"].append(3)
    assert inputs_equal(original, captured)
    assert captured.frame.attrs == original.frame.attrs
