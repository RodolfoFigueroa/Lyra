from copy import deepcopy

import pytest
from pydantic import ValidationError

from lyra.contracts.errors import (
    ExecutionPersistenceError,
    InvalidExecutionIDError,
    InvalidResultReferenceError,
    ReportIntegrityError,
    ReportNotFoundError,
    ResultAccessError,
    ResultIntegrityError,
    ResultNotFoundError,
)
from lyra.contracts.references import validate_execution_id, validate_result_reference
from lyra.contracts.reports import ExecutionReport


def report_payload() -> dict:
    origin = {"id": "survey", "revision": None, "description": "Survey records"}
    scope = {"provider_id": "places", "code_revision": "v1", "origins": [origin]}
    parameters = {"config": {"values": [None, True, 1, 1.5, "text"]}, "default": None}
    provider = {
        "use_id": "p",
        "node_id": "places",
        "kind": "provider",
        "outcome": "reused",
        "parameters": {},
        "primary_input": None,
        "inputs": {},
        "outputs": {},
        "code_revision": "v1",
        "origins": [origin, origin],
    }
    operation = {
        "use_id": "o",
        "node_id": "near",
        "kind": "operation",
        "outcome": "mixed",
        "parameters": parameters,
        "primary_input": "source",
        "inputs": {
            "source": {
                "source_use_id": "p",
                "output_name": None,
                "provider_scope": scope,
            }
        },
        "outputs": {},
        "code_revision": None,
        "origins": [],
        "operation": {
            "target_scope": scope,
            "target_adapter": "parquet",
            "selector": "executed",
            "target_query": "executed",
            "mapping": "executed",
        },
    }
    root = {
        "use_id": "root",
        "node_id": "area",
        "kind": "metric",
        "outcome": "executed",
        "parameters": parameters,
        "primary_input": "places",
        "inputs": {
            "places": {
                "source_use_id": "o",
                "output_name": None,
                "provider_scope": scope,
            }
        },
        "outputs": {
            "area": {
                "scope": "location",
                "owner_scope": scope,
                "table_schema": {"columns": []},
            }
        },
        "code_revision": None,
        "origins": [origin],
    }
    return deepcopy(
        {
            "request": {
                "metric": "area",
                "parameters": parameters,
                "inputs": {"places": {"provider": "places", "ids": ["A", "B"]}},
            },
            "execution_id": "a" * 32,
            "result_ref": "b" * 32,
            "status": "succeeded",
            "root_use_id": "root",
            "nodes": [provider, operation, root],
            "parameter_bindings": [
                {
                    "source_use_id": "root",
                    "source_parameter": "config",
                    "target_use_id": "o",
                    "target_parameter": "config",
                }
            ],
            "build_revision": None,
        }
    )


def test_complete_round_trip_and_ownership() -> None:
    raw = report_payload()
    report = ExecutionReport.model_validate(raw)
    assert ExecutionReport.model_validate_json(report.model_dump_json()) == report
    assert ExecutionReport.model_validate(report.model_dump()) == report
    assert len(report.nodes[0].origins) == 1
    assert report.nodes[0].origins[0].description == "Survey records"
    snapshot = report.model_dump()
    raw["nodes"][2]["parameters"]["config"]["values"].clear()
    assert report.model_dump() == snapshot
    captured = ExecutionReport(**dict(report))
    returned = report.model_copy(deep=True)
    returned.request.inputs["places"].ids.clear()
    returned.nodes[2].parameters.clear()
    returned.nodes[2].outputs["area"].table_schema.meaning = "Changed"
    returned.parameter_bindings[0].target_parameter = "changed"
    assert report.model_dump() == snapshot
    report.nodes[0].origins.clear()
    assert captured.model_dump() == snapshot
    assert returned.nodes is returned.nodes
    assert captured.request.parameters == snapshot["request"]["parameters"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 31,
        "a" * 33,
        "A" * 32,
        "g" * 32,
        "a" * 32 + "\n",
        " " + "a" * 32,
        123,
        None,
        b"a" * 32,
    ],
)
def test_strict_shared_references(value: object) -> None:
    for field in ("execution_id", "result_ref"):
        raw = report_payload()
        raw[field] = value
        with pytest.raises(ValidationError):
            ExecutionReport.model_validate(raw)
    with pytest.raises(InvalidExecutionIDError) as caught:
        validate_execution_id(value)
    assert caught.value.execution_id == value
    with pytest.raises(InvalidResultReferenceError) as result:
        validate_result_reference(value, "area")
    assert result.value.result_ref == value
    assert result.value.output_name == "area"


@pytest.mark.parametrize(
    "field", ["result_ref", "status", "build_revision", "request", "root_use_id"]
)
def test_success_requires_complete_fields(field: str) -> None:
    raw = report_payload()
    del raw[field]
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(raw)


@pytest.mark.parametrize(
    "case",
    [
        "status",
        "duplicate",
        "root",
        "request",
        "data",
        "output",
        "parameter_use",
        "parameter_name",
        "primary",
        "mapping",
        "applicability",
        "origins",
        "owner",
    ],
)
def test_invalid_graph_and_stage_contracts(case: str) -> None:
    raw = report_payload()
    root = raw["nodes"][2]
    operation = raw["nodes"][1]
    changes = {
        "status": (raw, "status", "failed"),
        "duplicate": (raw, "nodes", [*raw["nodes"], root]),
        "root": (raw, "root_use_id", "missing"),
        "request": (raw["request"], "parameters", {}),
        "data": (root["inputs"]["places"], "source_use_id", "missing"),
        "output": (root["inputs"]["places"], "output_name", "invalid"),
        "parameter_use": (raw["parameter_bindings"][0], "target_use_id", "missing"),
        "parameter_name": (raw["parameter_bindings"][0], "source_parameter", "missing"),
        "primary": (root, "primary_input", "missing"),
        "mapping": (operation["operation"], "mapping", "not_applicable"),
        "applicability": (operation["operation"], "selector", "not_applicable"),
        "origins": (operation, "origins", root["origins"]),
        "owner": (root["outputs"]["area"], "owner_scope", None),
    }
    target, key, value = changes[case]
    target[key] = value
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(raw)


@pytest.mark.parametrize("outcome", ["executed", "reused", "mixed", "skipped"])
@pytest.mark.parametrize("stage", ["executed", "skipped", "not_applicable"])
def test_supported_outcomes(outcome: str, stage: str) -> None:
    raw = report_payload()
    operation = raw["nodes"][1]
    operation["outcome"] = outcome
    operation["operation"].update(selector=stage, target_query=stage, mapping="skipped")
    assert ExecutionReport.model_validate(raw).nodes[1].outcome == outcome


def test_error_context_and_chaining() -> None:
    for error_type in (
        InvalidExecutionIDError,
        ReportNotFoundError,
        ReportIntegrityError,
    ):
        error = error_type("attempt")
        assert isinstance(error, ResultAccessError)
        assert error.execution_id == "attempt"
    for error_type in (
        InvalidResultReferenceError,
        ResultNotFoundError,
        ResultIntegrityError,
    ):
        error = error_type("reference", "area")
        assert isinstance(error, ResultAccessError)
        assert error.result_ref == "reference"
        assert error.output_name == "area"
    cause = OSError("storage")
    execution_id = "attempt"
    with pytest.raises(ExecutionPersistenceError) as caught:
        raise ExecutionPersistenceError(execution_id) from cause
    assert caught.value.execution_id == "attempt"
    assert caught.value.__cause__ is cause
    assert validate_execution_id("a" * 32) == "a" * 32
    assert validate_result_reference("b" * 32) == "b" * 32


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "value"}])
def test_parameters_preserve_only_supported_json(value: object) -> None:
    raw = report_payload()
    raw["nodes"][2]["parameters"] = {"invalid": value}
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(raw)


@pytest.mark.parametrize("output_name", ["area", "missing", None])
def test_named_metric_data_links(output_name: str | None) -> None:
    raw = report_payload()
    consumer = deepcopy(raw["nodes"][2])
    consumer.update(use_id="consumer", primary_input=None)
    consumer["inputs"] = {
        "table": {
            "source_use_id": "root",
            "output_name": output_name,
            "provider_scope": None,
        }
    }
    consumer["outputs"]["area"].update(scope="unpartitioned", owner_scope=None)
    raw["nodes"].append(consumer)
    raw["root_use_id"] = "consumer"
    if output_name == "area":
        report = ExecutionReport.model_validate(raw)
        assert report.nodes[-1].outputs["area"].owner_scope is None
    else:
        with pytest.raises(ValidationError):
            ExecutionReport.model_validate(raw)
