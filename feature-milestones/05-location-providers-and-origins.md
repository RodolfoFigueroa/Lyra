# 5. Location providers and external origins

Origins identify external data dependencies. Location providers expose
schema-validated location records derived from those dependencies. Each provider
defines one concrete collection, while an origin may describe unstructured data,
several unrelated tables, or a source shared by many providers and metrics.

## Accepted scope

### Origins and direct external acquisition

Provide an immutable `Origin` value with required stable `id`, optional opaque
`revision`, and optional plain-text `description`. Identity is `(id, revision)`;
descriptions and Python object identity do not affect it. An origin has no schema,
connection configuration, or retrieval logic. `revision=None` identifies fixed
unversioned contents. Changed external contents require a changed origin identity.

Metrics and location providers declare their direct external dependencies through
`origins=(...)` on their own declarations. A declaration may contain zero, one, or
several origins. Deduplicate by `(id, revision)`; order and repeated equivalent
values do not affect identity. An empty collection promises no direct external
data acquisition. A node consuming a source only through an upstream output or
location provider needs no direct declaration for that source. A metric querying
it directly must declare it even when an upstream dependency uses the same origin.

Resources provide configuration, credentials, connections, and lifecycle.
`Resource("census_db")` selects an I/O binding; each consuming node declares the
external data it reads. Multiple nodes can share one resource while declaring
different origins. Collect and validate origin declarations before cache lookup,
resource initialization, credential resolution, or service access. Discovery,
computation keys, and reporting use the same declarations as those features arrive.
Cached and skipped nodes retain their declarations without claiming acquisition.

Operations compute from declared inputs and parameters. Their handlers and optional
selectors acquire no external data directly and have no resource bindings or
direct-origin declarations. Location acquisition belongs to providers and their
managed adapters. Providers declare every origin those acquisition paths use.

Authors own complete dependency declarations for every output scope and cache
setting. Lyra neither inspects handler code nor intercepts queries to discover
undeclared acquisition. Resource operation logs remain diagnostics. Declarations
do not prove which sources were actually accessed, archive external data, or
guarantee its later availability.

### Provider declarations and lookup

Each `LocationProvider` declares a stable public `id`, optional opaque
`code_revision`, optional plain-text `description`, one executable Pandera `schema`,
and its `origins`. A provider exposes one collection of records with IDs, geometry,
and attributes. Separate providers expose tracts and counties even when both depend
on the same census origin. One provider may combine several declared origins.

Register provider classes in `PluginDefinition.resources`. Discover every registered
`LocationProvider` subclass and index it by its public ID across active plugins.
Each ID resolves to one provider registration; reject conflicting registrations
before acquisition. The resource mapping key selects configuration and the ordinary
resource lifecycle. The provider ID identifies the collection independently of
that key, its Python class name, or its module. Several providers can share a
schema while retaining separate identities.

Named location inputs and operation targets declare provider ID strings. Writing
`CensusTracts.id` is equivalent to writing `"census_tracts"`. Derive each input's
record schema from its required provider. All registered columns remain available
to the handler. Operation-derived inputs add the correspondence structure defined
in milestone 6. Authors guarantee that attributes have the meanings their
computations expect; matching names, types, or bare IDs establishes no geographic
equivalence or temporal alignment.

Managed requests supply a provider ID and strict nonempty string location IDs
for each named selection. Preparation captures the provider's code revision and origin
identities from live declarations. Providers declare fixed origins and revisions;
exposing two releases simultaneously requires distinct provider IDs. Milestone 6
owns routing from source selections to concrete provider requirements and unifies
local, development, and remote execution around this contract.

Provider and origin metadata must be discoverable without acquiring data. Existing
metric inspection exposes each location input's required provider ID, registered
schema, code revision, and declared origins, along with the metric's own direct
origins. Discovery of accepted source selections follows declared operation routes.

### Immutable records and acquisition

The provider scope is `(provider_id, code_revision, origin identities)`, with origin
identities treated as an unordered, deduplicated collection. This is runtime
metadata, not another author-facing type. A record is identified by that scope and
its `location_id`. Thus tract `"001"` and county `"001"` remain different records
even when their providers share an origin.

A provider scope fixes every record's geometry, attributes, and relationship
fields, as well as the complete searchable membership. Changed external data
requires an updated origin identity. Output-affecting retrieval, transformation,
schema, or membership logic requires an updated provider code revision or ID.
An omitted code revision is a stable `None` value; result-preserving refactors need
no revision change. Changing a shared census origin's revision changes the scopes
of both its tract and county providers; their code revisions change independently.
Changing a location ID alone does not authorize changing membership under the same
provider scope.

Resource aliases, credentials, connection settings, acquisition batches, and
selection paths do not independently alter record identity. Authors must represent
output-affecting configuration through the provider or origin identities. Equal
record identities promise equal contents even with caching disabled. Lyra validates
structure without hashing or comparing contents to establish identity.

The provider's exact-ID resolver is `resolve(self, ids: list[str]) -> GeoDataFrame`.
It receives nonempty, deduplicated IDs, uses its ordinary configured resources and
declared origins, and accepts no arbitrary calculation parameters or upstream
tables. It returns exactly one record for each requested ID. Missing records fail
acquisition. Records are independent of other requested IDs, so acquisition can
narrow to the IDs currently needed. Empty needed
selections invoke no resolver. Parameters such as a search radius belong to the
selecting metric or operation.

Providers return ordinary GeoDataFrames validated against their declared schemas.
Scope comes from resolved declarations, never `DataFrame.attrs`. The identity
guarantee concerns record contents, not frozen Python objects. Milestone 6 gives
handlers independent working frames while retaining authoritative records and scope.

Provider schemas explicitly declare unique, non-null, nonempty string `location_id`
and active `geometry`, including expected CRS and geometry validity requirements.
Authors rename external columns and perform required reprojection before return.
Reserve `location_id`, `source_location_id`, `row_key`, and `geometry` for their
structural roles and reject collisions. Empty frames retain declared dtypes and
CRS. Public IDs use Pydantic's strict string validation without coercion or a custom
identity codec; Pandera owns table-column checks. Numeric or composite source IDs
must be exposed as stable, unambiguous strings.

[Milestone 7](07-operation-target-selection-with-ibis.md) adds predicate selection
through managed provider adapters. Operations construct lazy filters; Lyra executes
them against the declared target provider and validates candidate records. Both
predicate acquisition and exact-ID resolution use the same provider scope and
record schema. Individual resolved records do not prove the completeness of a
predicate's matching set. A selection remains request context or a graph dependency,
not an extra exact-ID argument or record identity field. A revision identifies
data without archiving a historical snapshot.

### Native table construction and validation

Replace production `LocationPayload` calls carrying empty feature lists with
focused scope and CRS validation. Remove the unused feature-list validation path
while preserving required checks of actual location contents at shared producer
and persistence boundaries. Authors and test helpers construct ordinary
GeoDataFrames and declare Pandera schemas; validation returns native tables.
Keep required ID, geometry, CRS, type, and nullability checks at their designated
boundaries. Schema compilation requires no additional public data container.

Replace the authoring compatibility-check workflow with representative native-table
authoring checks. Remove the supported-version gate and checks tied to internal
payload schemas or location factories. Exercise declarations, valid and invalid
producer frames, and normal execution. Milestone 6 owns removal of the public
location-value abstraction across execution, construction, and inspection.

## API sketches

These sketches establish declaration and selection syntax. Acquisition bodies,
resource setup, and the ordinary `COUNTY_SCHEMA` and `POPULATION_SCHEMA` declarations
are omitted to focus on this milestone's contracts.

### A shared origin and provider schema

```python
import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
from pandera.engines.geopandas_engine import Geometry

from lyra.contracts.units import Unit
from lyra.sdk import IOConfig, LocationProvider, Origin, PluginDefinition, metric

CENSUS = Origin(
    id="national-census",
    revision="2025",
    description="National census release.",
)

TRACT_SCHEMA = pa.DataFrameSchema(
    {
        "location_id": pa.Column(
            str, nullable=False, unique=True,
            checks=pa.Check(lambda s: s.str.len().gt(0), name="nonempty"),
            description="Tract identifier", metadata={"unit": None},
        ),
        "geometry": pa.Column(
            Geometry(crs="EPSG:4326"), nullable=False,
            checks=[
                pa.Check(
                    lambda s: s.geom_type.isin(["Polygon", "MultiPolygon"]),
                    name="polygonal",
                ),
                pa.Check(lambda s: s.is_valid & ~s.is_empty, name="valid_geometry"),
            ],
            description="Tract boundary", metadata={"unit": None},
        ),
        "population": pa.Column(
            "int64", nullable=False, checks=pa.Check.ge(0),
            description="Resident population", metadata={"unit": Unit.COUNT},
        ),
        "name": pa.Column(
            str, nullable=True,
            description="Tract name", metadata={"unit": None},
        ),
    },
    strict=True,
)
```

The schema describes the provider's complete records. The origin describes the
external source without imposing a table structure. `strict=True` rejects
undeclared record columns; the resolver returns data in the declared CRS.

### Two providers sharing that origin

```python
class CensusTracts(LocationProvider[IOConfig]):
    id = "census_tracts"
    code_revision = "1"
    origins = (CENSUS,)
    schema = TRACT_SCHEMA

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        ...


class CensusCounties(LocationProvider[IOConfig]):
    id = "census_counties"
    code_revision = "1"
    origins = (CENSUS,)
    schema = COUNTY_SCHEMA

    def resolve(self, ids: list[str]) -> gpd.GeoDataFrame:
        ...
```

A provider combining census and elevation data declares both sources, for example
`origins=(CENSUS, ELEVATION)`, where `ELEVATION` is another `Origin`. Neither tuple
order nor repeating the same origin changes its scope.

### A concrete metric, registration, and selection

```python
@metric(
    name="tract_population",
    description="Return population for each tract.",
    locations={"tracts": CensusTracts.id},
    outputs={"population": POPULATION_SCHEMA},
)
def tract_population(tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "location_id": tracts["location_id"],
        "row_key": "population",
        "population": tracts["population"],
    })


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[tract_population],
        resources={
            "census_tracts": CensusTracts,
            "census_counties": CensusCounties,
        },
    )
```

`locations={"tracts": "census_tracts"}` is equivalent: `tracts` names the handler
argument and `census_tracts` is the public provider ID. The record schema is inferred
from the provider. This metric acquires census data only through its location input,
so it has no direct origin declaration. Its output schema declares `location_id`,
`row_key`, and `population` with the output rules of milestone 6.

```python
description = client.catalog.metric("tract_population")
handle = client.jobs.submit(
    "tract_population",
    {"tracts": {"provider": "census_tracts", "ids": ["001", "002"]}},
)
```

Inspection exposes the required provider, its schema, code revision, and origins
without constructing a resource. Submission supplies provider and location IDs;
preparation captures revisions from declarations. A different source provider may
reach this metric through a declared operation route under milestone 6.

### A metric that queries an origin directly

```python
from typing import Annotated

from lyra.sdk import Resource


@metric(
    name="queried_tract_population",
    description="Query census population for the selected tracts.",
    locations={"tracts": "census_tracts"},
    origins=(CENSUS,),
    outputs={"population": POPULATION_SCHEMA},
)
def queried_tract_population(
    tracts: gpd.GeoDataFrame,
    census: Annotated[CensusDatabaseIO, Resource("census_db")],
) -> pd.DataFrame:
    ...
```

Here `CensusDatabaseIO` is an ordinary I/O class registered as `census_db` alongside
the location provider. The metric declares its direct census query even though the
provider already declares the same origin. The resource supplies access; the node
identifies the data it uses.

## Completion boundary

Registered providers can be discovered and acquired through the new contract, and
their consumers, routing, and ordinary result paths still work. Origin metadata
is available without acquiring resources. Isolated fake providers cover strict
IDs, missing records, exact resolution, schema/CRS preservation, empty selections,
and immutable contents and membership under a fixed provider scope.

Cover separate tract and county providers sharing one origin, providers combining
several origins, conflicting provider IDs, and lookup by provider ID even when
schemas match. Verify distinct records for equal bare IDs in separate providers,
scope changes from provider code or origin revisions, equivalent origin values,
and order-independent origin deduplication. Resource aliases and descriptions do
not change scope. Discovery exposes provider and metric origins without service
access. Reject resource bindings and direct origins on operations.

Producer checks use native frames, preserve empty and all-null schemas, and keep
identity independent of mutable frame metadata. Local development can expose fixed
local data through a provider with declared origins; tests use in-process providers
and isolated records. Managed callers supply parameters and provider/ID selections;
providers supply records and upstream nodes supply dependency tables. Full-record
injection, arbitrary tables, and caller-supplied result references replacing
dependencies are outside this contract.

## Deferred or excluded work

Provider record caching is completed in [milestone 9](09-node-caching.md), and
observed acquisition/reuse/skipped reporting in
[milestone 10](10-execution-reports-and-durable-success.md). A declared provider
boundary alone must not be presented as observed acquisition.

The provider cache setting is boolean and off by default. Exact-ID caching reuses
needed records individually and acquires only misses; disabled caching acquires
needed IDs without own-record reuse. Both allow reduced ID selections, and a valid
downstream hit may bypass acquisition entirely. Shared inputs and upstream scope
still determine needed records. Predicate acquisition discovers complete membership
and is not satisfied by individually cached records; milestone 7 supplies that
execution boundary.

There is no separate origin registry, query interception, per-call source tracking,
content-based identity verification, or proof of external-I/O behavior. Locations
constructed externally can be exposed through registered providers; creating and
registering new locations inside operations is outside scope.

## Reference points

Provider work spans [provider declarations](../src/lyra/sdk/locations.py),
[provider lookup](../src/lyra/runtime/planning/providers.py),
[location contracts](../src/lyra/contracts/locations.py), and
[geometry schemas](../src/lyra/sdk/location_schema.py); the
[city provider](../examples/city-plugin/city_example/provider.py) is an authoring example.
Validation and authoring-check work also involves the
[payload model](../src/lyra/data/location_payload.py),
[location construction](../src/lyra/data/locations.py), and
[authoring-check script](../skills/lyra-metric/scripts/check_compatibility.py).
