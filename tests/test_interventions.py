from typing import cast

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.engines.numpy_engine import String
from pydantic import ValidationError
from shapely.geometry import Point

from lyra.contracts.accepted import ExecutionRequest
from lyra.contracts.errors import MetricResultError
from lyra.contracts.interventions import AcceptedIntervention, InterventionSpec
from lyra.data.intervention_records import (
    accepted_additions,
    decode_additions,
    encode_additions,
    encode_validated_additions,
)
from lyra.data.interventions import Intervention
from lyra.sdk.interventions import accept_interventions
from lyra.sdk.provider_schema import ProviderSchema
from test_support.imports import assert_fresh_imports
from test_support.location_schema import column
from tests.provider_schema_helpers import declaration, frame


def schema() -> ProviderSchema:
    declared = declaration()
    declared.columns.update(
        {
            "label": column("string", nullable=True),
            "height": column("Float64", nullable=True),
            "enabled": column("boolean", nullable=True),
        }
    )
    return ProviderSchema(declared)


def additions() -> gpd.GeoDataFrame:
    result = frame()
    result["location_id"] = pd.Series(["001", "1"], dtype="string")
    result["count"] = pd.Series([-(2**63), 2**63 - 1], dtype="Int64")
    result["label"] = pd.Series([" 001 ", None], dtype="string")
    result["height"] = pd.Series([1.2345678901234567, None], dtype="Float64")
    result["enabled"] = pd.Series([True, None], dtype="boolean")
    result["geometry"] = gpd.GeoSeries(
        [Point(1.2345678901234567, -2.345678901234567), Point(1, 2)], crs=result.crs
    )
    return result


def spec(**changes: object) -> InterventionSpec:
    return InterventionSpec.model_validate(
        {"id": "proposal", "catalog": "trees", "revision": "v1", "remove": ["old"]}
        | changes
    )


def native(data: gpd.GeoDataFrame | None = None) -> Intervention:
    return Intervention(
        id="proposal", catalog="trees", revision="v1", add=data, remove=["old"]
    )


def accept(value: Intervention | InterventionSpec) -> AcceptedIntervention:
    return accept_interventions([value], {"trees": schema()})[0]


def test_omission_and_empty_overall_collection() -> None:
    assert accept_interventions(None, {}) == accept_interventions([], {}) == ()
    assert ExecutionRequest(metric="metric").interventions == []
    assert ExecutionRequest(metric="metric", interventions=[]).interventions == []
    assert accept(native()).add == []
    assert accepted_additions(accept(native())).empty
    assert accepted_additions(accept(spec())).empty
    captured = accept(native())
    restored = AcceptedIntervention.model_validate_json(captured.model_dump_json())
    assert restored == captured


def test_removal_only_values_and_json_contracts_need_no_native_dependencies() -> None:
    assert_fresh_imports(
        ["lyra.data.interventions", "lyra.contracts.interventions"],
        blocked=(
            "geopandas",
            "pandas",
            "numpy",
            "pyarrow",
            "pandera",
            "lyra.sdk",
            "lyra.runtime",
        ),
        assertions="""
        from lyra.data.interventions import Intervention
        from lyra.contracts.interventions import InterventionSpec
        native = Intervention(id="a", catalog="trees", revision="1", remove=["001"])
        assert native.remove == ["001"] and native.add is None
        spec = InterventionSpec(id="a", catalog="trees", revision="1", remove=["001"])
        assert InterventionSpec.model_validate_json(spec.model_dump_json()) == spec
        """,
    )


@pytest.mark.parametrize("section", ["inputs", "parameters", "interventions"])
def test_null_request_sections_fail(section: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"metric": "metric", section: None})


@pytest.mark.parametrize(
    "changes",
    [
        {"add": None},
        {"remove": None},
        {"remove": []},
        {"remove": [], "add": []},
        {"remove": ["a", "a"]},
        {"remove": [1]},
        {"remove": [""]},
        {"remove": ("a",)},
        {"catalog": 1},
        {"revision": None},
        {"id": ""},
        {"extra": []},
        {"addition_schema": {}},
        {"add": [{"location_id": 1}]},
        {"add": [{"location_id": "a"}, {"location_id": "a"}]},
    ],
)
def test_strict_intervention_shape(changes: dict) -> None:
    with pytest.raises((ValidationError, ValueError)):
        spec(**changes)


def test_empty_missing_actions_and_unknown_request_fields() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        InterventionSpec(id="a", catalog="trees", revision="v1")
    with pytest.raises(ValidationError, match="extra"):
        ExecutionRequest.model_validate({"metric": "m", "other": []})
    with pytest.raises(ValueError, match="at least one"):
        Intervention(id="a", catalog="trees", revision="v1", add=None, remove=None)
    with pytest.raises(ValueError, match="Repeated"):
        Intervention(id="a", catalog="trees", revision="v1", remove=["a", "a"])
    with pytest.raises(ValidationError):
        Intervention(
            id="a", catalog="trees", revision="v1", remove=cast("list[str]", [1])
        )


def test_duplicate_catalogs_and_unknown_catalogs() -> None:
    values = [spec(), spec(id="other")]
    with pytest.raises(ValidationError, match="one intervention set per catalog"):
        ExecutionRequest(metric="m", interventions=values)
    with pytest.raises(ValueError, match="one intervention set per catalog"):
        accept_interventions(values, {"trees": schema()})
    with pytest.raises(ValueError, match="Unknown intervention catalog"):
        accept_interventions([spec()], {})


def test_native_json_and_retained_roundtrips_preserve_precision() -> None:
    original = additions()
    original.index = pd.Index(["irrelevant", "irrelevant"], name="location_id")
    captured = accept(native(original))
    row = captured.add[0]
    assert row["location_id"] == "001"
    assert row["count"] == str(-(2**63))
    assert captured.add[1]["count"] == str(2**63 - 1)
    assert row["height"] == original["height"].iloc[0]
    assert row["label"] == " 001 "
    assert row["enabled"] is True
    assert captured.add[1]["height"] is None
    assert "1.2345678901234567" in str(row["geometry"])
    submitted = InterventionSpec.model_validate(captured.submission_payload())
    from_json = InterventionSpec.model_validate_json(submitted.model_dump_json())
    restored = accept(from_json)
    assert restored == captured
    retained = AcceptedIntervention.model_validate_json(captured.model_dump_json())
    for value in (captured, restored, retained):
        data = accepted_additions(value)
        assert data is not None
        pd.testing.assert_frame_equal(
            data, original.reset_index(drop=True)[list(data.columns)]
        )
        assert data.geometry.to_wkb().tolist() == original.geometry.to_wkb().tolist()


@pytest.mark.parametrize("empty", [False, True])
def test_declared_types_survive_all_null_and_empty_records(*, empty: bool) -> None:
    data = additions()
    for name in ("count", "label", "height", "enabled"):
        data[name] = pd.Series([None, None], dtype=data[name].dtype)
    if empty:
        data = data.iloc[:0]
    accepted = accept(native(data))
    restored = AcceptedIntervention.model_validate_json(accepted.model_dump_json())
    result = accepted_additions(restored)
    assert result is not None
    pd.testing.assert_frame_equal(result, data[list(result.columns)])
    assert result.crs == data.crs
    assert result.active_geometry_name == "geometry"
    assert accept(spec(add=[])).addition_schema is not None


def test_nonempty_row_index_requires_registered_columns() -> None:
    data = gpd.GeoDataFrame(index=[0])
    with pytest.raises(ValueError, match="location_id"):
        accept(native(data))


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(
    "problem", ["missing", "extra", "crs", "active", "dtype", "no_crs"]
)
def test_native_metadata_is_validated_before_encoding(
    problem: str, *, empty: bool
) -> None:
    data = additions()
    if empty:
        data = data.iloc[:0]
    if problem == "missing":
        data = data.drop(columns="label")
    elif problem == "extra":
        data["extra"] = 1
    elif problem == "crs":
        data = data.to_crs("EPSG:3857")
    elif problem == "active":
        data = data.rename_geometry("shape")
    elif problem == "dtype":
        data["count"] = data["count"].astype("string")
    elif problem == "no_crs":
        data = gpd.GeoDataFrame(data.to_dict("list"), geometry="geometry")
    if empty:
        assert accept(native(data)) == accept(spec())
    else:
        with pytest.raises((ValueError, MetricResultError)):
            accept(native(data))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("count", 1),
        ("count", "1.0"),
        ("count", str(2**63)),
        ("count", str(-(2**63) - 1)),
        ("count", " 1"),
        ("count", True),
        ("height", "1.5"),
        ("height", True),
        ("height", float("inf")),
        ("height", float("nan")),
        ("height", 2**63 + 1),
        ("enabled", 1),
        ("enabled", "true"),
        ("label", 123),
        ("label", {"nested": []}),
        ("geometry", "invalid"),
        ("geometry", None),
        ("geometry", "POINT EMPTY"),
        ("location_id", 1),
        ("location_id", None),
    ],
)
def test_malformed_cells_fail_without_repair(name: str, value: object) -> None:
    rows = accept(native(additions())).model_dump()["add"]
    rows[0][name] = value
    with pytest.raises((ValueError, MetricResultError)):
        accept(spec(add=rows))


@pytest.mark.parametrize("problem", ["missing", "extra", "duplicate"])
def test_record_completeness_and_duplicate_ids(problem: str) -> None:
    rows = accept(native(additions())).model_dump()["add"]
    if problem == "missing":
        del rows[0]["label"]
    elif problem == "extra":
        rows[0]["extra"] = None
    else:
        rows[1]["location_id"] = rows[0]["location_id"]
    with pytest.raises(ValueError, match=r"declared column|Duplicate added IDs"):
        accept(spec(add=rows))
    data = additions()
    data["location_id"] = ["a", "a"]
    with pytest.raises(ValueError, match="duplicate"):
        accept(native(data))


def test_executable_checks_run_without_coercing_submitted_types() -> None:
    declared = declaration()
    declared.columns["count"].checks = [pa.Check.ge(0)]
    registered = ProviderSchema(declared)
    data = frame()
    data["count"] = pd.Series(["1", "2"], dtype="string")
    with pytest.raises(MetricResultError):
        accept_interventions([native(data)], {"trees": registered})
    data["count"] = pd.Series([-1, 2], dtype="Int64")
    with pytest.raises(MetricResultError):
        accept_interventions([native(data)], {"trees": registered})


def test_ownership_of_frames_actions_metadata_and_nested_request_values() -> None:
    data = additions()
    data.attrs["nested"] = {"values": ["original"]}
    removals = ["old"]
    value = Intervention(
        id="proposal", catalog="trees", revision="v1", add=data, remove=removals
    )
    captured = accept(value)
    expected = captured.model_dump_json()
    data.loc[0, "location_id"] = "changed"
    data.attrs["nested"]["values"].append("changed")
    removals.append("changed")
    assert value.add is not None
    assert value.add.attrs["nested"] == {"values": ["original"]}
    value.add.loc[0, "label"] = "changed"
    assert value.remove is not None
    value.remove.append("changed")
    reconstructed = accepted_additions(captured)
    assert reconstructed is not None
    reconstructed.loc[0, "label"] = "changed"
    assert captured.model_dump_json() == expected
    payload = captured.submission_payload()
    request = ExecutionRequest.model_validate(
        {"metric": "m", "interventions": [payload]}
    )
    payload["add"][0]["label"] = "changed"
    assert request.interventions[0].add[0]["label"] == " 001 "
    source = spec(add=[{"location_id": "a", "nested": {"items": [1]}}])
    request = ExecutionRequest(metric="m", interventions=[source])
    source.add[0]["nested"] = None
    assert request.interventions[0].add[0]["nested"] == {"items": [1]}
    registered = schema()
    accepted = accept_interventions([native(additions())], {"trees": registered})[0]
    before = accepted.model_dump_json()
    registered.metadata.columns[0].checks.clear()
    assert accepted.model_dump_json() == before


def test_row_index_column_and_action_order_do_not_change_values() -> None:
    data = additions()
    first = accept(native(data))
    reordered = data.iloc[::-1][list(reversed(data.columns))]
    reordered.index = pd.Index([99, -1])
    second = accept(native(reordered))
    assert {row["location_id"]: row for row in first.add} == {
        row["location_id"]: row for row in second.add
    }
    assert [row["location_id"] for row in second.add] == ["1", "001"]
    sets = [spec(remove=["01", "1"]), spec(catalog="other", remove=["1", "01"])]
    accepted = accept_interventions(sets, {"trees": schema(), "other": schema()})
    reversed_sets = accept_interventions(
        list(reversed(sets)), {"trees": schema(), "other": schema()}
    )
    assert {value.catalog: value for value in accepted} == {
        value.catalog: value for value in reversed_sets
    }
    assert accepted[0].remove == ["01", "1"]
    assert accepted[1].remove == ["1", "01"]


@pytest.mark.parametrize("values", [[True, False], [True, None], [None, None], []])
def test_nullable_bool_declaration_round_trip(values: list[bool | None]) -> None:
    declared = declaration()
    declared.columns["active"] = column("bool", nullable=True)
    data = frame().iloc[: len(values)].assign(active=pd.array(values, dtype="boolean"))
    registered = ProviderSchema(declared)
    (captured,) = accept_interventions([native(data)], {"trees": registered})
    assert [row["active"] for row in captured.add] == values
    (decoded,) = accept_interventions(
        [InterventionSpec.model_validate(captured.submission_payload())],
        {"trees": registered},
    )
    assert decoded == captured


def test_nullable_bool_preserves_executable_check() -> None:
    declared = declaration()
    declared.columns["active"] = column(
        "bool", nullable=True, checks=pa.Check.eq(value=True)
    )
    data = frame().assign(active=pd.array([False, None], dtype="boolean"))
    with pytest.raises(MetricResultError, match="equal_to"):
        accept_interventions([native(data)], {"trees": ProviderSchema(declared)})


@pytest.mark.parametrize("values", [["001", None], [None, None], []])
def test_numpy_string_provider_and_additions_preserve_values(
    values: list[str | None],
) -> None:
    declared = declaration()
    declared.columns["label"] = column(String, nullable=True)
    registered = ProviderSchema(declared)
    labels = pd.Series(values, dtype=object if values else "string")
    data = frame().iloc[: len(values)].assign(label=labels)
    pd.testing.assert_series_equal(registered.validate(data).label, data.label)
    encoded = encode_additions(data, registered.metadata)
    (accepted,) = accept_interventions([native(data)], {"trees": registered})
    assert accepted.add == encoded
    assert [row["label"] for row in encoded] == values


@pytest.mark.parametrize("value", [17, ["nested"]])
def test_numpy_string_provider_and_additions_reject_nonstring_cells(
    value: object,
) -> None:
    declared = declaration()
    declared.columns["label"] = column(String)
    registered = ProviderSchema(declared)
    data = frame().assign(label=pd.Series(["001", value], dtype=object))
    with pytest.raises(MetricResultError, match="label"):
        registered.validate(data)
    with pytest.raises(MetricResultError, match="label"):
        accept_interventions([native(data)], {"trees": registered})
    with pytest.raises(ValueError, match="label"):
        encode_additions(data, registered.metadata)


@pytest.mark.parametrize("empty", [True, False])
def test_native_boolean_and_alternate_string_backing(*, empty: bool) -> None:
    data = additions().iloc[: 0 if empty else 1].astype({"label": "string[python]"})
    captured = accept(native(data))
    assert [row["label"] for row in captured.add] == data.label.tolist()
    if empty:
        assert accept(native(data.astype({"enabled": bool}))) == captured
    else:
        with pytest.raises(MetricResultError):
            accept(native(data.astype({"enabled": bool})))


@pytest.mark.parametrize("storage", ["python", "pyarrow"])
@pytest.mark.parametrize("values", [[], [None, None], ["001", None]])
def test_provider_and_additions_accept_either_string_storage(
    storage: str, values: list[str | None]
) -> None:
    declared = declaration()
    other = "python" if storage == "pyarrow" else "pyarrow"
    declared.columns["label"] = column(
        f"string[{other}]", nullable=True, checks=pa.Check.str_startswith("0")
    )
    registered = ProviderSchema(declared)
    data = (
        frame()
        .iloc[: len(values)]
        .assign(label=pd.Series(values, dtype=f"string[{storage}]"))
    )
    validated = registered.validate(data)
    assert validated.label.fillna("<missing>").tolist() == [
        v if v is not None else "<missing>" for v in values
    ]
    (captured,) = accept_interventions([native(data)], {"trees": registered})
    assert [row["label"] for row in captured.add] == values
    invalid = frame().assign(label=pd.Series(["bad", None], dtype=f"string[{storage}]"))
    with pytest.raises(MetricResultError):
        registered.validate(invalid)


def test_wkt_column_preserves_empty_missing_and_three_dimensions() -> None:
    metadata = schema().metadata
    geometry = next(column for column in metadata.columns if column.name == "geometry")
    geometry.nullable = True
    data = pd.concat([additions(), additions()], ignore_index=True).iloc[:3].copy()
    data["geometry"] = gpd.GeoSeries(
        [Point(0.0003144543032703886, 2, 3), Point(), None], crs=data.crs
    )
    data.index = [7, 7, 1]
    records = encode_validated_additions(data, metadata)
    assert [row["geometry"] for row in records][1:] == ["POINT EMPTY", None]
    restored = decode_additions(records, metadata)
    assert restored.geometry.to_wkb().tolist() == data.geometry.to_wkb().tolist()
    assert restored.crs == data.crs
