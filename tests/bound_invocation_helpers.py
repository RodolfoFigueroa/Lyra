"""Selected binding fixture shared by identity and topology tests."""

from lyra.contracts.accepted import PreparedExecution
from lyra.runtime.planning.bindings import BoundInvocation, bind_execution
from lyra.runtime.planning.catalog import CompiledCatalog, compile_live_catalog
from lyra.runtime.planning.preparation import prepare_execution
from tests.live_graph_helpers import plugins


def bound(
    ids: list[str], *, year: int = 2025
) -> tuple[CompiledCatalog, PreparedExecution, BoundInvocation]:
    catalog = compile_live_catalog(plugins())
    prepared = prepare_execution(
        catalog,
        {
            "metric": "consumer",
            "inputs": {"seed": {"provider": "tracts", "ids": ids}},
            "parameters": {"year": year},
        },
    )
    return catalog, prepared, bind_execution(catalog, prepared)
