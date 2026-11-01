"""Selections for native ASV runs."""

FAST_BENCH = (
    r"^(catalog.CatalogSuite.time_catalog\(10\)"
    r"|preparation.PreparationSuite.time_prepare\(1\)"
    r"|execution.ExecutionSuite.time_local\('diamond-small'\)"
    r"|execution.CacheSuite.time_execute\('warm'\)"
    r"|core.CodecSuite.time_encode\(10000\)"
    r"|interfaces.InterfaceSuite.time_rest_submit\(10\)"
    r"|interfaces.InterfaceSuite.time_mcp_search\(10\))$"
)
"""Native ASV selector for seven representative calibrated timings."""
