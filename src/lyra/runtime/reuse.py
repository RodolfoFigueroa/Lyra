"""Node-local reuse and retention decisions shared by computed values."""

from lyra.contracts.metric_scope import CachePolicy


def reuse_allowed(policy: CachePolicy) -> bool:
    """Return whether a retained value may avoid this node's invocation."""
    return policy != "off"


def publication_required(policy: CachePolicy, *, required: bool = False) -> bool:
    """Return whether computation outputs are needed for reuse or ordinary retrieval."""
    return required or reuse_allowed(policy)
