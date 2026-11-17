"""Retained table publication fixtures shared by native and Arrow access tests."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pandera.pandas as pa

from lyra.contracts.jobs import JobStatusInfo
from lyra.contracts.origins import Origin
from lyra.contracts.provider_scope import ProviderScope
from lyra.runtime.location_inputs import InputBindings
from lyra.runtime.metric_tables import MetricTables
from lyra.runtime.result_descriptions import (
    ComputationSelection,
    ResultDescription,
    ResultDescriptions,
    ResultOutput,
)
from lyra.runtime.sqlite_store import Header, SQLiteStore
from lyra.sdk.output_schema import compile_output
from lyra.sdk.storage_schema import describe
from lyra.server.job_store import JobObservation
from lyra.server.results import ResultService
from test_support.publication import publish_result
from tests.metric_execution_helpers import schema


def retained_service(
    path: Path, schema: pa.DataFrameSchema, frame: pd.DataFrame
) -> tuple[ResultService, str]:
    """Publish through isolated relational boundaries for remote retrieval."""
    writer = SQLiteStore(path)
    layout = describe(schema, metadata=compile_output(schema)[1])
    MetricTables(writer).publish(
        (
            (
                Header(
                    value_key="value", node_id="native", output_scope="unpartitioned"
                ),
                (),
            ),
        ),
        {"rows": frame},
        {"rows": layout},
        location_inputs={
            Header(
                value_key="value", node_id="native", output_scope="unpartitioned"
            ).value_key: InputBindings()
        },
    )
    registration = next(iter(writer.layout.descriptions.values()))
    key = publish_result(
        ResultDescriptions(writer),
        ResultDescription(
            metric_name="native",
            output_names=("rows",),
            parameters={},
            outputs={
                "rows": ResultOutput(
                    registration_key=registration.key,
                    owner_scope=None,
                    computations=(ComputationSelection(value_key="value"),),
                )
            },
        ),
    )
    path.touch()
    now = datetime.now(UTC)
    jobs = Mock(
        read_job=Mock(
            return_value=JobObservation(
                snapshot=JobStatusInfo(
                    job_id="native",
                    status="succeeded",
                    created_at=now,
                    updated_at=now,
                    result_ref=key,
                    execution_id=ResultDescriptions(writer)
                    .read_complete(key)[1]
                    .execution_id,
                ),
                result_ref=key,
                execution_id=ResultDescriptions(writer)
                .read_complete(key)[1]
                .execution_id,
            )
        )
    )
    service = ResultService(jobs, path)
    return service, key


def mixed_result(store: SQLiteStore) -> tuple[ResultDescriptions, str]:
    declaration = schema()
    layout = describe(declaration, metadata=compile_output(declaration)[1])
    scope = ProviderScope(provider_id="places", code_revision="1")
    for key, owner in (
        ("cached", scope),
        ("fresh", scope),
        ("other-provider", ProviderScope(provider_id="districts", code_revision="1")),
        ("other-code", ProviderScope(provider_id="places", code_revision="2")),
        (
            "other-origin",
            scope.model_copy(
                update={"origins": frozenset({Origin(id="survey", revision="2")})}
            ),
        ),
    ):
        frame = pd.DataFrame(
            {
                "location_id": ["next", "same", "same"],
                "row_key": ["r", "z", "a"],
                "value": pd.array(
                    [2, 3, 1] if key == "fresh" else [20, 30, 10], dtype="Int64"
                ),
            }
        )
        MetricTables(store).publish(
            (
                (
                    Header(
                        value_key=key,
                        node_id="metric",
                        output_scope="location",
                        owner_scope=owner,
                    ),
                    ("same", "next", "empty"),
                ),
            ),
            {"a": frame},
            {"a": layout},
            location_inputs={
                Header(
                    value_key=key,
                    node_id="metric",
                    output_scope="location",
                    owner_scope=owner,
                ).value_key: InputBindings()
            },
        )
    registration = next(iter(store.layout.descriptions.values()))
    descriptions = ResultDescriptions(store)
    reference = publish_result(
        descriptions,
        ResultDescription(
            metric_name="metric",
            parameters={},
            output_names=("a",),
            outputs={
                "a": ResultOutput(
                    registration_key=registration.key,
                    owner_scope=scope,
                    computations=(
                        ComputationSelection(value_key="cached", groups=("same",)),
                        ComputationSelection(
                            value_key="fresh", groups=("next", "empty")
                        ),
                    ),
                )
            },
        ),
    )
    return descriptions, reference
