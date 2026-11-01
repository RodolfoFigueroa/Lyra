"""Consumer workflow; tests supply an in-process transport for the client."""

from lyra.api.client.sync import LyraClient
from lyra.sdk.models.job import TableJobResult


def street_rating(client: LyraClient, *, edition: str) -> TableJobResult:
    """Discover, inspect, submit, poll, and retrieve a named street-rating edition.

    Returns:
        The successful result with declared output metadata.

    Raises:
        ValueError: If discovery does not advertise the requested edition.
    """
    families = client.catalog.metrics(query="street_rating")
    family = next(item for item in families if item.name == "street_rating")
    if edition not in {item.edition for item in family.editions}:
        message = f"Unknown street_rating edition: {edition}"
        raise ValueError(message)
    client.catalog.metric(family.name, edition=edition)
    handle = client.raw.submit(
        family.name,
        {
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
        edition=edition,
        cache_revision="external-refresh-1",
    )
    return handle.wait(timeout=30, poll_interval=0.01)
