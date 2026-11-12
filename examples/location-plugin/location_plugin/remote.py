"""Consumer workflow; tests supply an in-process transport for the client."""

from lyra.api import LyraClient
from lyra.contracts.jobs import TableJobResult


def street_rating(client: LyraClient, *, metric: str) -> TableJobResult:
    """Discover, inspect, submit, poll, and retrieve a registered street-rating metric.

    Returns:
        The successful result with declared output metadata.

    Raises:
        ValueError: If discovery does not advertise the requested metric.
    """
    metrics = client.catalog.metrics(query="street_rating")
    selected = next((item for item in metrics if item.name == metric), None)
    if selected is None:
        message = f"Unknown street rating metric: {metric}"
        raise ValueError(message)
    client.catalog.metric(selected.name)
    handle = client.jobs.submit(
        selected.name,
        inputs={
            "streets": {
                "collection": "city:entities",
                "unit_type": "block",
                "ids": ["b1", "b2"],
            },
            "surroundings": {
                "collection": "city:entities",
                "unit_type": "street",
                "ids": ["s1"],
            },
        },
        parameters={},
        cache_revision="external-refresh-1",
    )
    handle.wait(timeout=30, poll_interval=0.01)
    return handle.result()
