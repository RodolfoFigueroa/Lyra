from pathlib import Path

import pytest

from lyra.contracts.bound_plan import serialize_bound_plan
from lyra.runtime.planning.preparation import prepare_execution
from test_support.accepted_sources import routed_request
from test_support.imports import assert_fresh_imports

CONTRACTS = Path("src/lyra/contracts")
MODULES = sorted(
    "lyra.contracts" + ("" if path.stem == "__init__" else f".{path.stem}")
    for path in CONTRACTS.glob("*.py")
)
BLOCKED = (
    "lyra.sdk",
    "lyra.runtime",
    "lyra.server",
    "lyra.data",
    "lyra.api",
    "lyra.utils",
    "numpy",
    "pandas",
    "geopandas",
    "pyarrow",
    "pandera",
    "shapely",
    "pyproj",
    "rasterio",
    "sqlalchemy",
    "redis",
    "httpx",
    "requests",
    "fastapi",
)


@pytest.mark.parametrize("module", MODULES)
def test_contract_import_is_independent(module: str) -> None:
    assert_fresh_imports([module], blocked=BLOCKED)


def test_contract_initializer_does_not_load_model_families() -> None:
    assert_fresh_imports(
        ["lyra.contracts"],
        blocked=(*BLOCKED, "lyra.version"),
        assertions="""
        assert not any(name.startswith("lyra.contracts.") for name in sys.modules)
        """,
    )


def test_contract_serialization_without_execution_dependencies() -> None:
    assert_fresh_imports(
        MODULES,
        blocked=BLOCKED,
        assertions="""
        from pydantic import TypeAdapter, ValidationError
        from lyra.contracts.config import InstalledPluginConfig
        from lyra.contracts.resources import ResourceConfig, SecretReference
        from lyra.contracts.errors import MetricResultError, ResourceConfigurationError
        from lyra.contracts.geometry import PointGeometry, PolygonGeometry
        from lyra.contracts.locations import LocationSelection
        from lyra.contracts.revisions import CacheRevision
        from lyra.contracts.schema import (
            CLIENT_SCHEMA_VERSION, build_node_request_schema, subschema_validator,
        )
        from lyra.contracts.types import JsonValue, validate_json_object

        plugin = InstalledPluginConfig(distribution="Sample_Plugin.Name")
        assert plugin.distribution == "sample-plugin-name"
        settings = ResourceConfig(secrets={"token": SecretReference(env="UNREAD")})
        encoded_settings = settings.model_dump_json()
        assert ResourceConfig.model_validate_json(encoded_settings) == settings
        assert ResourceConfigurationError("invalid").input_name is None
        payload = {"nested": [None, True, {"count": 2, "value": 3.5}]}
        assert validate_json_object(payload) == payload
        adapter = TypeAdapter(JsonValue)
        assert adapter.validate_json(adapter.dump_json(payload)) == payload
        assert "$defs" in adapter.json_schema()
        selection = LocationSelection(
            collection="example:places", unit_type="point", ids=["001", "001"]
        )
        encoded = selection.model_dump_json()
        assert LocationSelection.model_validate_json(encoded) == selection
        schema = build_node_request_schema("probe", None, {"places": "Places"})
        subschema_validator(schema, schema).validate({"places": selection.model_dump()})
        assert CLIENT_SCHEMA_VERSION == 4
        assert PointGeometry(type="Point", coordinates=[1, 2]).model_dump() == {
            "type": "Point", "coordinates": [1.0, 2.0]
        }
        invalid = [
            (PointGeometry, {"type": "Point", "coordinates": [1, float("inf")]}),
            (PolygonGeometry, {"type": "Polygon", "coordinates": [
                [[0, 0], [1, 0], [1, 1], [0, 1]]
            ]}),
            (LocationSelection, {**selection.model_dump(), "ids": [1]}),
            (LocationSelection, {**selection.model_dump(), "extra": True}),
        ]
        for model, value in invalid:
            try:
                model.model_validate(value)
            except ValidationError:
                pass
            else:
                raise AssertionError(value)
        revision = TypeAdapter(CacheRevision)
        assert revision.validate_python(" revision ") == " revision "
        for value in (1, "", "  ", "x" * 257):
            try:
                revision.validate_python(value)
            except ValidationError:
                pass
            else:
                raise AssertionError(value)
        error = MetricResultError("probe", "output", "x" * 3000)
        assert error.metric == "probe" and error.path == "output"
        assert len(error.message) == 2000 and error.truncated
        assert len(error.failures[0]["message"]) == 1000
        """,
    )


def test_fixture_manifests_and_discovery_without_execution_dependencies() -> None:
    assert_fresh_imports(
        MODULES,
        blocked=BLOCKED,
        assertions="""
        import json
        from pathlib import Path
        from pydantic import ValidationError
        from lyra.contracts.dependencies import DependencyManifest
        from lyra.contracts.discovery import (
            EditionChoice,
            FamilySummary,
            MetricInspection,
        )
        from lyra.contracts.editions import EditionRegistry
        from lyra.contracts.plugins import PluginManifest

        for path in sorted(Path("examples").glob("*/lyra.plugin.json")):
            raw = json.loads(path.read_text())
            manifest = PluginManifest.model_validate_json(path.read_text())
            assert json.loads(manifest.model_dump_json()) == raw
            assert (
                PluginManifest.model_validate_json(manifest.model_dump_json())
                == manifest
            )
            registry = EditionRegistry(manifest.nodes)
            for family, editions in registry.families.items():
                for edition, node in editions.items():
                    assert registry.resolve(family, edition=edition) == node
            for node in manifest.nodes:
                node.request_schema
                if node.kind != "metric":
                    continue
                summary = FamilySummary(
                    name=node.family or node.name,
                    description=node.description,
                    output_granularity={
                        name: value.granularity for name, value in node.outputs.items()
                    },
                    requires_edition=node.edition is not None,
                    editions=[
                        EditionChoice(edition=node.edition, accepted_selections={})
                    ],
                )
                inspection = MetricInspection(
                    family=summary,
                    edition=node.edition,
                    node=node.name,
                    edition_metadata=node.edition_metadata,
                    native_inputs={
                        name: value.contract.family
                        for name, value in node.location_inputs.items()
                    },
                    parameters=node.parameters_schema or {},
                    outputs=node.outputs,
                    bindings={
                        name: value.contract
                        for name, value in node.location_inputs.items()
                    },
                    accepted_selections={},
                )
                for value in (summary, inspection, *summary.editions):
                    assert (
                        type(value).model_validate_json(value.model_dump_json())
                        == value
                    )
        literal = {"kind": "input", "path": "parameters.year"}
        valid = DependencyManifest.model_validate(
            {
                "producer": "source",
                "output": "records",
                "locations": {
                    "places": {
                        "kind": "location_dependency",
                        "operation": "select",
                        "parameters": {
                            "options": {"kind": "literal", "value": literal}
                        },
                    }
                },
            }
        )
        assert valid.locations["places"].parameters["options"].value == literal
        for malformed in (
            {"locations": {"places": {"kind": "input", "path": "parameters.year"}}},
            {"parameters": {"year": {"kind": "input", "path": "places"}}},
            {"parameters": {"nested.field": {"kind": "literal", "value": 1}}},
            {"locations": {"places": {"kind": "literal", "value": "places"}}},
            {"parameters": {"year": {"kind": "unknown", "value": 1}}},
            {
                "locations": {
                    "places": {
                        "kind": "location_dependency",
                        "operation": "select",
                        "parameters": {"year": {"kind": "input", "path": "places"}},
                    }
                }
            },
        ):
            try:
                DependencyManifest.model_validate(
                    {
                        "producer": "source",
                        "output": "records",
                        **malformed,
                    }
                )
            except ValidationError:
                pass
            else:
                raise AssertionError(malformed)
        nested = valid.locations["places"]
        nested.parameters["bad.field"] = nested.parameters["options"]
        try:
            DependencyManifest(
                producer="source", output="records", locations={"p": nested}
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("Nested typed bindings were not validated")
        """,
    )


def test_accepted_wire_round_trip_without_execution_dependencies() -> None:
    catalog, request = routed_request()
    payload = serialize_bound_plan(prepare_execution(catalog, request))
    assert_fresh_imports(
        ["lyra.contracts.jobs"],
        blocked=BLOCKED,
        assertions=f"PAYLOAD = {payload!r}\n"
        """
from copy import deepcopy
from datetime import UTC, datetime
from pydantic import TypeAdapter, ValidationError
from lyra.contracts.bound_plan import deserialize_bound_plan, serialize_bound_plan
from lyra.contracts.jobs import AcceptedJob, JobCreateRequest, JobEnvelope, JobProgress
from lyra.contracts.plugins import PluginInfo
from lyra.contracts.provenance import ExecutionObservations, InvocationObservation
from lyra.contracts.admin import PluginRoutingResponse
from lyra.contracts.data_types import DataTypesResponse
from lyra.contracts.inspection import json_size, trim_inspection
from lyra.contracts.metrics import normalize_metric_search_tokens
from lyra.contracts.observability import LivenessResponse

plan = deserialize_bound_plan(PAYLOAD)
assert deserialize_bound_plan(serialize_bound_plan(plan)) == plan
job = JobEnvelope(job_id="job", accepted=AcceptedJob(
    plan=plan, catalog_fingerprint="catalog",
    plugin=PluginInfo(name="fixture", version="1"), created_at=datetime.now(UTC),
))
restored = JobEnvelope.model_validate_json(job.model_dump_json())
assert restored == job
snapshot = restored.model_dump(mode="json")
view = restored.accepted.plan.contracts.nodes
view.clear()
assert restored.model_dump(mode="json") == snapshot
for mutate in ("revision", "route", "presence"):
    raw = deepcopy(plan.model_dump(mode="json"))
    if mutate == "revision":
        raw["revisions"]["calculation"] = []
        raw["revisions"]["selection"] = []
    elif mutate == "route":
        raw["inputs"][0]["binding"]["operations"] = ["missing"]
    else:
        raw["request"]["supplied_fields"] = []
    try:
        type(plan).model_validate(raw)
    except ValidationError:
        pass
    else:
        raise AssertionError(mutate)
values = [
    JobCreateRequest(metric="score", input={}),
    PluginRoutingResponse(metric_queues={}, allowed_queues=["default"],
                          default_queue="default"),
    DataTypesResponse(location_selection_schema={"type": "object"}),
    LivenessResponse(status="ok", api_version="1"),
]
for value in values:
    assert type(value).model_validate_json(value.model_dump_json()) == value
for raw in ({"metric": "score", "input": {}, "extra": True},
            {"metric": "", "input": {}}):
    try:
        JobCreateRequest.model_validate(raw)
    except ValidationError:
        pass
    else:
        raise AssertionError(raw)
try:
    JobProgress(timestamp=datetime.now(UTC), stage="run", current=2, total=1)
except ValidationError:
    pass
else:
    raise AssertionError("Invalid progress accepted")
observations = ExecutionObservations(native_inputs=(), observations=(
    InvocationObservation(node=plan.node, invocation="run", path=(),
                          outcome="completed"),
), succeeded=True)
adapter = TypeAdapter(ExecutionObservations)
assert adapter.validate_json(adapter.dump_json(observations)) == observations
assert normalize_metric_search_tokens("CaféScore cafe") == ("cafe", "score")
payload = {"error": {"message": "é"}, "truncation": {"omitted_sections": []}}
assert json_size(payload) > 0
assert trim_inspection(payload)
assert payload["error"] is None
assert payload["truncation"]["omitted_sections"] == ["error"]
assert not trim_inspection(payload)
""",
    )
