"""Fixed operation mapping contract for Python values and JSON restoration."""

from typing import Annotated

from pydantic import Field, TypeAdapter

OperationMapping = Annotated[
    dict[
        Annotated[str, Field(strict=True)],
        Annotated[tuple[Annotated[str, Field(strict=True)], ...], Field(strict=True)],
    ],
    Field(strict=True),
]
OPERATION_MAPPING_ADAPTER = TypeAdapter(OperationMapping)
