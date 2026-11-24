from pathlib import Path
from textwrap import dedent

import pytest

from lyra.runtime.planning.preparation import prepare_execution
from lyra.sdk.plugin_cli import render_manifest
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
        from lyra.contracts.revisions import ImplementationRevision
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
        from jsonschema import Draft202012Validator
        Draft202012Validator(adapter.json_schema()).validate(payload)
        selection = LocationSelection(
            provider="example:places", ids=["001", "001"]
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
        revision = TypeAdapter(ImplementationRevision)
        assert revision.validate_python(" revision ") == " revision "
        for value in (1, "", "  "):
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
    payloads = [
        render_manifest(path.parent)
        for path in Path("examples").glob("*-plugin/pyproject.toml")
    ]
    assert_fresh_imports(
        MODULES,
        # Pint probes optional NumPy support during unit parsing.
        blocked=tuple(module for module in BLOCKED if module != "numpy"),
        unavailable=("numpy",),
        assertions=f"PAYLOADS = {payloads!r}\n"
        + dedent(
            """
        import json
        from pathlib import Path
        from pydantic import ValidationError
        from lyra.contracts.discovery import (
            MetricSummary,
            MetricInspection,
        )
        from lyra.contracts.plugins import PluginInspection

        for payload in PAYLOADS:
            raw = json.loads(payload)
            manifest = PluginInspection.model_validate_json(payload)
            assert json.loads(manifest.model_dump_json()) == raw
            assert (
                PluginInspection.model_validate_json(manifest.model_dump_json())
                == manifest
            )
            for node in manifest.nodes:
                if node["kind"] != "metric":
                    continue
                summary = MetricSummary(
                    name=node["name"], description=node["description"],
                    output_granularity={
                        name: value["granularity"]
                        for name, value in node["outputs"].items()
                    },
                    accepted_selections={},
                )
                inspection = MetricInspection.model_validate_json(json.dumps({
                    "metric": summary.model_dump(mode="json"),
                    "node": node["name"], "computed_inputs": node["inputs"],
                    "providers": {}, "origins": [],
                    "location_inputs": {
                        name: {
                            "target_provider": value["contract"]["provider_scope"][
                                "provider_id"
                            ]
                        }
                        for name, value in node["location_inputs"].items()
                    },
                    "parameters": node["parameters_schema"] or {},
                    "outputs": node["outputs"],
                }))
                for value in (summary, inspection):
                    assert (
                        type(value).model_validate_json(value.model_dump_json())
                        == value
                    )
        """
        ),
    )


def test_accepted_wire_round_trip_without_execution_dependencies() -> None:
    catalog, request = routed_request()
    payload = prepare_execution(catalog, request).model_dump_json()
    assert_fresh_imports(
        ["lyra.contracts.jobs"],
        blocked=BLOCKED,
        assertions=f"PAYLOAD = {payload!r}\n"
        """
from copy import deepcopy
from datetime import UTC, datetime
from pydantic import TypeAdapter, ValidationError
from lyra.contracts.accepted import PreparedExecution, WorkerRequest
from lyra.contracts.jobs import AcceptedJob, JobCreateRequest, JobEnvelope, JobProgress
from lyra.contracts.plugins import PluginInfo
from lyra.contracts.provenance import ExecutionObservations, InvocationObservation
from lyra.contracts.admin import InstalledPluginResponse
from lyra.contracts.data_types import DataTypesResponse
from lyra.contracts.inspection import json_size, trim_inspection
from lyra.contracts.metrics import normalize_metric_search_tokens
from lyra.contracts.observability import LivenessResponse

plan = PreparedExecution.model_validate_json(PAYLOAD)
assert PreparedExecution.model_validate_json(plan.model_dump_json()) == plan
job = JobEnvelope(job_id="1" * 32, accepted=AcceptedJob(
    plan=WorkerRequest.from_plan(plan), catalog_fingerprint="catalog",
    plugin=PluginInfo(name="fixture", version="1"), created_at=datetime.now(UTC),
))
restored = JobEnvelope.model_validate_json(job.model_dump_json())
assert restored == job
snapshot = restored.model_dump(mode="json")
view = restored.accepted.plan.parameter_uses
view.clear()
assert job.model_dump(mode="json") == snapshot
for mutate in ("revision", "parameters"):
    raw = deepcopy(plan.model_dump(mode="json"))
    if mutate == "revision":
        raw["revisions"]["calculation"] = []
        raw["revisions"]["selection"] = []
    else:
        raw["request"]["parameters"] = []
    try:
        type(plan).model_validate(raw)
    except ValidationError:
        pass
    else:
        raise AssertionError(mutate)
values = [
    JobCreateRequest(metric="score", inputs={}),
    InstalledPluginResponse(distribution="plugin", enabled=True),
    DataTypesResponse(location_selection_schema={"type": "object"}),
    LivenessResponse(status="ok", api_version="1"),
]
for value in values:
    assert type(value).model_validate_json(value.model_dump_json()) == value
for raw in ({"metric": "score", "inputs": {}, "extra": True},
            {"metric": "", "inputs": {}}):
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
