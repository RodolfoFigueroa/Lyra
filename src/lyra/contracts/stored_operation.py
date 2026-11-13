"""Persisted completed operation relationships and captured provider roles."""

from lyra.contracts.locations import LocationContractReference
from lyra.contracts.operations import OperationMapping
from lyra.contracts.strict import StrictBaseModel


class StoredOperation(StrictBaseModel):
    """Completed relationships, with source and target contracts kept separate."""

    groups: OperationMapping
    source_scope: LocationContractReference
    target_scope: LocationContractReference
