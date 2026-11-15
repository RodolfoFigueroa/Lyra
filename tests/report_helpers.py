"""Inspect private unfinished computation summaries in execution tests."""

from lyra.runtime.execution import ExecutionOutcome


def outcomes(result: ExecutionOutcome) -> dict[str, list[str]]:
    report = result.report_state
    values: dict[str, list[str]] = {}
    for use_id, observation in report.computations.items():
        values.setdefault(report.uses[use_id].node, []).append(observation.outcome)
    return values
