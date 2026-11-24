"""Metric projections preserve visibility and executable contracts."""

import pytest

from test_support.edition_catalog import discovery


def test_direct_names_visibility_pagination_and_detachment() -> None:
    view = discovery()
    assert [item.name for item in view.search()] == ["measure_0", "measure_1"]
    assert [item.name for item in view.search(offset=1, limit=1)] == ["measure_1"]
    assert view.search("unavailable") == []
    assert view.search(limit=0) == []
    with pytest.raises(ValueError, match="nonnegative"):
        view.search(offset=-1)
    detail = view.inspect("measure_1")
    assert detail.node == "measure_1"
    scope = detail.providers[detail.location_inputs["places"].target_provider].scope
    assert scope is not None
    assert scope.provider_id == "measurement_1"
    assert detail.providers["measurement_1"].accepted_sources == ["measurement_1"]
    detail.outputs.clear()
    view.search()[0].accepted_selections.clear()
    assert view.inspect("measure_1").outputs
    assert view.search()[0].accepted_selections
    assert view.fingerprint == discovery().fingerprint
    view = discovery(private=1)
    assert [item.name for item in view.search()] == ["measure_0"]
    for name in ("unknown", "measure_1"):
        with pytest.raises(KeyError):
            view.inspect(name)


def test_constraints_and_source_definitions_survive_projection() -> None:
    detail = discovery().inspect("measure_1")
    properties = detail.parameters["properties"]
    assert isinstance(properties, dict)
    count = properties["count"]
    assert isinstance(count, dict)
    assert count["maximum"] == 9
    assert "location_id" in {
        c.name for c in detail.providers["measurement_1"].record_schema.columns
    }
    assert detail.providers["measurement_1"].accepted_sources == ["measurement_1"]


def test_routed_source_availability_is_provider_specific() -> None:
    view = discovery(regional=True)
    assert [item.name for item in view.search("regional")] == ["measure_0"]
    assert view.inspect("measure_0").providers["measurement_0"].accepted_sources == [
        "measurement_0",
        "regional",
    ]
    assert discovery(private=0, regional=True).search("regional") == []
