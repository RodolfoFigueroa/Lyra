"""Shared PostGIS provider declarations without resource acquisition."""

from typing import Annotated

from lyra.sdk import Origin, PostGISLocationProvider, PostgresIO, Resource
from tests.provider_schema_helpers import declaration


class Places(PostGISLocationProvider):
    id = "places"
    code_revision = "1"
    origins = (Origin(id="city", revision="1"),)
    schema = declaration()
    db_schema = "city data"
    table = "places"
    database: Annotated[PostgresIO, Resource("database")]
