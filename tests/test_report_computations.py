"""Use summaries reflect handlers and historical reuse across scheduling paths."""

from pathlib import Path

import pandas as pd
import pytest

from lyra.contracts.metric_scope import CachePolicy
from lyra.sdk import metric
from tests.cache_helpers import SCHEMA
from tests.graph_execution_helpers import executor
from tests.operation_reuse_helpers import workload
from tests.report_helpers import outcomes
from tests.sqlite_helpers import database

__all__ = ["database"]
pytestmark = pytest.mark.usefixtures("database")


@pytest.mark.parametrize("derived", [False, True])
@pytest.mark.parametrize("policy", ["full", "per_location"])
def test_operation_mapping_reuse_and_empty_completions(
    tmp_path: Path, policy: CachePolicy, *, derived: bool
) -> None:
    run, events, _, mapping = workload(tmp_path, policy=policy, derived=derived)
    # An empty mapping still invokes the handler for nonempty sources.
    assert outcomes(run())["mapping"] == ["executed"]
    events.clear()
    assert outcomes(run())["mapping"] == ["reused"]
    assert not any(event[0] == "mapping" for event in events)
    mapping["c"] = ("target",)
    assert outcomes(run(("b", "c")))["mapping"] == [
        "mixed" if policy == "per_location" else "executed"
    ]


def test_executed_empty_table_is_not_skipped() -> None:
    calls = []

    @metric(name="empty", description="Empty output", outputs={"data": SCHEMA})
    def empty() -> pd.DataFrame:
        calls.append(True)
        return pd.DataFrame({"value": pd.Series(dtype=int)})

    runner, plugin = executor(empty)
    result = runner.execute(plugin.prepare_resolved_invocation("empty", {}))
    assert result.outputs["data"].empty
    assert calls == [True]
    assert outcomes(result) == {"empty": ["executed"]}


@pytest.mark.parametrize("policy", ["off", "full", "per_location"])
@pytest.mark.parametrize("derived", [False, True])
def test_empty_operation_sources_do_not_claim_execution(
    tmp_path: Path, policy: CachePolicy, *, derived: bool
) -> None:
    run, events, _, _ = workload(tmp_path, policy=policy, derived=derived)
    assert outcomes(run(()))["mapping"] == ["skipped"]
    assert outcomes(run(()))["mapping"] == ["reused" if policy == "full" else "skipped"]
    assert events == []
