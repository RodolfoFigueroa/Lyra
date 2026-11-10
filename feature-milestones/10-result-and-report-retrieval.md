# 10. Result and report retrieval

Expose retained values and execution reports consistently through Python, HTTP,
and MCP without depending on transient jobs. It builds on a runtime that publishes
successful results with complete reports in SQLite, adding bounded
inspection, complete downloads, and row access over those durable records.

## Accepted scope

### Shared read-only access

Use shared reference definitions and read-only lookup logic for local Python,
remote Python clients, HTTP, and MCP. Run/result interfaces provide the durable
execution ID alongside ordinary outputs. The stored result description identifies
the values and selected groups to retrieve, and the associated execution report
remains addressable after RQ/job expiry.

Local runs, including development, automatically initialize a persistent store
whose path is configurable. Local result access identifies that store so callers
can reopen it. Successful local or remote completion already requires durable
final values and transactional publication of their retrieval descriptions with
the complete report. Persistence failure cannot yield a successful result whose
report is unavailable. Retain written values and reports indefinitely within the
supported current store.

Both Python interfaces use native HTTPX `Client`/`AsyncClient` and share endpoint
definitions, request construction, response validation, errors, and table
conversion. Keep ordinary HTTPX timeouts, caller-task cancellation, and cleanup;
local cancellation does not cancel remote jobs. Do not add a background bridge for
downloads or a strict total-duration guarantee. MCP uses typed FastMCP handlers
with SDK-generated schemas, argument validation, dispatch, and ordinary validation
errors. Preserve authentication, annotations, bounded responses, and shared domain
operations. SDK-compatible envelopes, including a `result` wrapper for unions,
are acceptable. These extend the clients and tool registration delivered earlier.

References use shared strict string definitions, parsing, and formatting. Keep
raw-job-ID convenience at the Python client boundary by normalizing it before
shared handling. Durable execution IDs identify reports; result/table references
identify retained values and their selected groups, without public row/group
trace identities.

Malformed references are validation errors; well-formed references without a
target are not found; broken required retained relationships are integrity errors.
Lookups do not rerun handlers, acquire external data, reconstruct historical
producers, fabricate records, or run recursive integrity sweeps. Intentionally
unstored intermediates are outside report retrieval and do not make a report
incomplete. Concrete reference encoding, preview fields and omission metadata,
envelopes, cursors, pagination arrangement, JSON row encoding, and size limits
remain implementation choices within these contracts.

### Bounded reports and full downloads

A complete report describes the normalized request, selected graph and named
bindings, node kinds and output scopes, effective parameters including defaults
and nulls, declared origins and relevant revisions, observed execution/reuse/skipped
outcomes, and final status. It distinguishes separate resolved uses of a definition
and can describe mixed computation/reuse. Declared sources are not evidence of
fresh acquisition, and a hit or skipped node must not appear as an invocation.
Location-scoped, unpartitioned, hybrid, empty-result, and zero-location executions
all use this report contract without extra columns on ordinary result tables.

Return the complete report inline when it fits the inspection response limit.
Otherwise return an explicitly partial preview that preserves execution ID and
final status, identifies omissions or shortening, and links to the complete JSON
download. Never silently truncate or present a preview as complete.

Generate the download from the same retained report with ordinary authentication.
It may stream beyond the inspection size limit and requires no permanently stored
report file. Large parameters or graph descriptions select preview-and-download
behavior; they do not fail execution or report retrieval solely because the report
exceeds the inspection bound. The complete retained report and durable-success
requirement remain unchanged.

Python callers can obtain the complete report locally or through the remote JSON
download. MCP returns the same bounded inspection and full-report link. Agents may
download and process oversized reports through other capabilities; complete
inspection exclusively through MCP is not required. Download access remains
resolvable through the durable execution ID after job expiry.

### Ordinary tables in Arrow and paginated JSON

Serve named output tables as Arrow bulk downloads and bounded paginated JSON rows
through HTTP and MCP. Keep status, schemas, reports, previews, and download links
in JSON. MCP row access lets agents inspect result values without code execution
or an Arrow reader; bulk clients need an Arrow reader.

Read selected rows from SQLite using shared retrieval logic and standard table
conversions. Pagination selects rows in SQL rather than decoding an entire table
or partition blob before slicing. Assemble cached and freshly computed groups by
explicit keys and retained references without permanently copying their values.
Arrow remains a generated transport format.

The value store uses complete headers addressed directly by computation or
location-record keys, partition records where applicable, and typed named-output
rows. Schemas are retained with registered definitions rather than inferred from
observed rows. Operation pairs are stored separately from authoritative target
records; a forwarded table joins each pair to its target's values without
duplicating those stored records. Retrieval descriptions select existing value
keys and groups, including a mixture of cached and newly computed fragments.

Preserve the same supported logical types and applicable standard columns across
Python, Arrow, and JSON: metric `location_id`/`row_key`, operation
`source_location_id`/`location_id`, provider `location_id`, and spatial `geometry`.
Retain dataset/revision qualifications, named-output and result/table scope,
nullability, descriptions, units, and declared CRS in the applicable schema and
metadata. Use standard WKB/spatial conversions, including native CRS through
SQL-to-Arrow-to-client reconstruction. Empty and all-null outputs retain their
declared schema. Internal storage fields do not become author-facing columns.

Supported values are strings, signed 64-bit integers, finite 64-bit floats,
booleans, and geometry with declared nullability. Missing values correspond to
SQL `NULL`; reconstruction uses pandas string/nullable numeric/boolean types and
GeoPandas geometry from standard WKB with declared CRS. Temporal or categorical
values use supported strings or numbers; nested objects/lists are not table
columns. Preserve strict string ID and row-key spelling across all transports.
Do not promise original dtype widths or arbitrary backing-array encodings.

Dataset/revision scope qualifies each location ID, and `row_key` is scoped to its
owning location and named output. In operation tables, source and target IDs have
separate scopes; shared target records remain visible once per correspondence.
Nonstructural ID columns are ordinary data, not reasons to acquire records or
infer additional associations. Spatial outputs always use active `geometry`.

Unpartitioned tables expose their scope without mandatory owners or author row
keys. Internal addressing supports pagination without public semantic row
identities. A fixed result must be traversable exactly once, including distinct
stored rows with equal values, without omissions or repetitions. No relative row
order is promised; separate reads or transports may arrange rows differently.
Completion records preserve empty partitions and outputs without introducing
public trace references or empty-group inspection.

## Completion boundary

Both Python client interfaces, HTTP, and MCP can retrieve ordinary stored tables
and inspect reports after job records expire. Local callers can reopen the store
and obtain complete reports. Small reports are complete inline; large reports are
explicit previews with usable full downloads.

Isolated and in-process checks cover authentication, advertised MCP schemas and
actual envelopes, bounded responses, all reference error categories, job expiry,
and result assembly. Verify SQL-to-Arrow-to-client type/geometry/CRS preservation,
empty and all-null schemas, pagination boundaries, and duplicate-valued
unpartitioned rows. Report checks include oversized parameters/graphs, explicit
omission information, and complete downloads exceeding inspection limits.

## Deferred or excluded work

Managed GIS files follow in [milestone 11](11-geoparquet-exports.md). This milestone
does not create a permanent Arrow or report artifact store.

MCP-only report pagination/chunk tools, public graph navigation, finding lookups,
row/group traces, empty-group inspection, and intermediate membership APIs remain
deferred. Retrieval supports the current store without migrations or legacy
readers across incompatible schema/code changes, and does not restore external
inputs that are no longer available.

## Reference points

Current table conversion is split between [server results](../src/lyra/server/results.py)
and [client reconstruction](../src/lyra/api/client/_tables.py).
GeoPandas provides the [Arrow reader](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.from_arrow.html)
used for spatial reconstruction.
