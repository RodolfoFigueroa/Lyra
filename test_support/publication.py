"""Complete execution report fixtures for isolated storage consumers."""

from uuid import uuid4

from lyra.contracts.accepted import SubmittedRequest
from lyra.contracts.outputs import OutputMetadata
from lyra.contracts.reports import ExecutionReport, ReportNode, ReportOutput
from lyra.runtime.result_descriptions import ResultDescription, ResultDescriptions


def publish_result(writer: "ResultDescriptions", description: ResultDescription) -> str:
    """Publish an explicit complete report with a storage fixture's description."""
    writer.store.load_registrations()
    report = ExecutionReport(
        execution_id=uuid4().hex,
        result_ref=uuid4().hex,
        status="succeeded",
        request=SubmittedRequest(
            metric=description.metric_name, parameters=description.parameters
        ),
        root_use_id="root",
        nodes=[
            ReportNode(
                use_id="root",
                node_id=description.metric_name,
                kind="metric",
                outcome="executed",
                parameters=description.parameters,
                primary_input=None,
                inputs={},
                outputs={
                    name: ReportOutput(
                        scope=writer.store.layout.descriptions[
                            output.registration_key
                        ].output_scope,
                        table_schema=OutputMetadata.model_validate(
                            writer.store.layout.descriptions[
                                output.registration_key
                            ].table_schema
                        ),
                        owner_scope=output.owner_scope,
                    )
                    for name, output in description.outputs.items()
                },
                code_revision=description.code_revision,
                origins=[],
            )
        ],
        parameter_bindings=[],
        build_revision=None,
    )
    return writer.publish(description, report)
