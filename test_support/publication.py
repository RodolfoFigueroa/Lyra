"""Complete execution report fixtures for isolated storage consumers."""

from uuid import uuid4

from lyra.contracts.accepted import SubmittedRequest
from lyra.contracts.reports import DataBinding, ExecutionReport, ReportNode
from lyra.runtime.result_descriptions import ResultDescription, ResultDescriptions


def publish_result(writer: "ResultDescriptions", description: ResultDescription) -> str:
    """Publish an explicit complete report with a storage fixture's description."""
    return writer.publish(description, publication_report(writer, description))


def publication_report(
    writer: ResultDescriptions, description: ResultDescription
) -> ExecutionReport:
    """Build a matching report before testing publication of a description."""
    writer.store.load_registrations()
    execution_id, result_ref = uuid4().hex, uuid4().hex
    projection = writer.metadata(description, result_ref, execution_id)
    sources = []
    bindings = {}
    for name, location in projection.location_inputs.items():
        scope = location.provider_scope
        use_id = f"provider/{name}"
        sources.append(
            ReportNode(
                use_id=use_id,
                node_id=scope.provider_id,
                kind="provider",
                outcome="reused",
                parameters={},
                primary_input=None,
                inputs={},
                outputs={},
                code_revision=scope.code_revision,
                origins=list(scope.origins),
            )
        )
        bindings[name] = DataBinding(
            source_use_id=use_id, output_name=None, provider_scope=scope
        )
    return ExecutionReport(
        execution_id=execution_id,
        result_ref=result_ref,
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
                inputs=bindings,
                outputs=projection.outputs,
                code_revision=description.code_revision,
                origins=[],
            ),
            *sources,
        ],
        parameter_bindings=[],
        build_revision=None,
    )
