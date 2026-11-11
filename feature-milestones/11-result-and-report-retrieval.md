# 11. Result and report retrieval

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

Preserve the local `RunResult` contract from
[milestone 6](06-unified-graph-execution.md): named native tables are in `.tables`,
and the attached run description is in `.metadata`, with output descriptions in
`.metadata.outputs`. Durable execution identification and report access extend
this structure; callers retain each run's tables and metadata together.

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

Job-based lookup accepts plain `job_id` strings across Python, HTTP, and MCP,
using the shared validation contract from
[milestone 3](03-mcp-and-shared-references.md). Durable execution IDs identify
reports; result references identify retained values and their selected groups.
A named table is addressed by `(result_ref, output_name)`, matching local
`ResultStore.read_table()`. These identifiers use shared strict string validation
in the contracts layer, without separate table-reference types or public row/group
trace identities. Job-based methods accept `job_id`; durable result and report
methods explicitly accept `result_ref` and `execution_id`, respectively.

Malformed references are validation errors; well-formed references without a
target are not found; broken required retained relationships are integrity errors.
Lookups do not rerun handlers, acquire external data, reconstruct historical
producers, fabricate records, or run recursive integrity sweeps. Intentionally
unstored intermediates are outside report retrieval and do not make a report
incomplete. Concrete reference and cursor encoding, transport envelopes around
the sketched payloads, and size limits remain implementation choices within these
contracts.

### Bounded reports and full downloads

A complete report describes the normalized request, selected graph and named
bindings, node kinds and output scopes, effective parameters including defaults
and nulls, declared origins and relevant revisions, observed execution/reuse/skipped
outcomes, and final status. It distinguishes separate resolved uses of a definition
and can describe mixed computation/reuse. Declared sources are not evidence of
fresh acquisition, and a hit or skipped node must not appear as an invocation.
Location-scoped, unpartitioned, hybrid, empty-result, and zero-location executions
all use this report contract without extra columns on ordinary result tables.

Return the complete report inline with `kind="complete"` when it fits the
inspection response limit. Otherwise return `kind="preview"` with a fixed summary:
execution ID, root metric name, final status, `omitted=["report"]`, and a link to
the complete JSON download. The preview omits the complete report body as a unit;
it does not select individual nodes or shorten parameter values. Never silently
truncate or present a preview as complete.

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

Serve the root metric's final named output tables as Arrow bulk downloads and
bounded paginated JSON rows through HTTP and MCP, matching the `RunResult` and
`ResultStore` contracts from milestones 6 and 8. Provider and operation scopes
remain inspectable through the retained execution report. Keep status, schemas,
reports, previews, and download links in JSON. MCP row access lets agents inspect
result values without code execution or an Arrow reader; bulk clients need an
Arrow reader.

Table downloads use Arrow IPC stream format with HTTP content type
`application/vnd.apache.arrow.stream`. Spatial columns use standard GeoArrow WKB
encoding and CRS metadata. Saved downloads preserve the stream bytes regardless
of the filename. Empty downloads include the declared schema, with applicable
geometry and CRS metadata.

JSON row pages respect both row-count and encoded response-size limits, including
envelope overhead. Return complete rows that fit, leaving remaining rows for
subsequent pages. Each page contains `rows`, a list of JSON objects, and
`next_cursor`, an opaque string or `null` when finished. Do not silently truncate
cell values or skip rows.

If the next row cannot fit in an otherwise empty page, return a bounded error
with code `"row_too_large"`, identifying the result and named output and providing
the table's Arrow download link. Python clients raise `RowTooLargeError`. If
preceding rows fit, return them with a continuation cursor pointing to the
oversized row; requesting that continuation produces the error. Do not advance
past that row. This condition does not fail execution, invalidate the stored
result, or prevent complete bulk retrieval. Exact size limits remain
implementation choices.

Read selected rows from SQLite using shared retrieval logic and standard table
conversions. Pagination selects rows in SQL rather than decoding an entire table
or partition blob before slicing. Assemble cached and freshly computed groups by
explicit keys and retained references without permanently copying their values.
Arrow remains a generated transport format.

Python table retrieval returns independent pandas DataFrames or GeoPandas
GeoDataFrames directly. Keep result references, owning provider scopes, and report
access in result descriptions separate from the frames. Upstream source/target
scopes remain in the report's bindings and operation details. For local run
results, output descriptions accompany the tables in
`RunResult.metadata`; table retrieval helpers still expose the native values. A
caller may modify its retrieved frame without affecting stored values or subsequent
reads; frame attributes do not establish identity or update the result description.
Standalone files use ordinary library readers returning native tables, without a
location-artifact wrapper, conversion chain, format-version dispatch, or historical
reader.

For location-scoped metrics, retrieve the owning provider scope from the retained
computation and result description: provider ID, code revision, and declared origin
identities. Preserve the scope used by that computation even if the active
registration has changed. Results owned by different provider scopes remain
distinct even when their location IDs match, including cached fragments and empty
outputs.
Python, HTTP, and MCP expose these concrete scopes consistently without metric
family/edition selectors or reconstruction of a graph to determine ownership.
Unpartitioned outputs remain whole tables without an owning provider scope.

The value store uses complete headers addressed directly by computation or
location-record keys, partition records where applicable, and typed named-output
rows. Schemas are retained with registered definitions rather than inferred from
observed rows. Operation pairs are stored separately from authoritative target
records; a forwarded table joins each pair to its target's values without
duplicating those stored records. Retrieval descriptions select existing value
keys and groups, including a mixture of cached and newly computed fragments.

Generate descriptive table metadata from the registered Pandera declarations
through the existing `compile_output()` conversion to `OutputMetadata`. Retain
that generated metadata with the result so empty outputs and reopened results
expose the same declared schema without loading current plugins. Use ordinary
Pydantic models for result descriptions, serialization, and API schema generation.
This milestone uses the existing dependencies and conversion path.

Preserve the same supported logical types and applicable standard columns across
Python, Arrow, and JSON: `location_id`/`row_key` for location-scoped metric outputs
and `geometry` for spatial outputs.
Retain provider-scope qualifications, named-output and result/table scope,
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

Provider scope qualifies each location ID, and `row_key` is scoped to its
owning location and named output.
Nonstructural ID columns are ordinary data, not reasons to acquire records or
infer additional associations. Spatial outputs always use active `geometry`.

Unpartitioned tables expose their scope without mandatory owners or author row
keys. Internal addressing supports pagination without public semantic row
identities. When every individual encoded row fits within the response limit,
pagination must traverse a fixed result exactly once, including distinct stored
rows with equal values, without omissions or repetitions. Results containing
oversized rows remain completely retrievable through Arrow; complete inspection
exclusively through MCP is not guaranteed for those results. No relative row order
is promised; separate reads or transports may arrange rows differently.
Completion records preserve empty partitions and outputs without introducing
public trace references or empty-group inspection.

## API sketches

### Finishing a job and retaining its identifiers

Use `client.jobs` for job execution, `client.results` for result access, and
`client.reports` for report access. Successful job status and terminal result
responses expose `execution_id` and `result_ref`, both populated on success.
Waiting for a job retrieves its successful status without downloading tables:

```python
from lyra.api import LyraClient

with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    handle = client.jobs.submit(
        "block_area",
        {"blocks": {"provider": "city_blocks", "ids": ["block_1", "block_2"]}},
    )
    status = handle.wait()

    execution_id = status.execution_id
    result_ref = status.result_ref
```

`handle.result()` and `client.jobs.run()` expose the same two fields on their
successful results. Retain these plain string identifiers together with the
deployment address for later access. Job-based helpers continue accepting plain
job IDs; methods do not infer an identifier's kind from its string contents.

### Durable result and report access

Reconnect to the same deployment using the retained identifiers, including after
the job record expires:

```python
with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    description = client.results.describe(result_ref)
    area = client.results.table(result_ref, "area")
    report = client.reports.get(execution_id)

    assert description.execution_id == report.execution_id
    assert description.result_ref == report.result_ref
```

The methods have these return contracts:

| Call | Returns |
| --- | --- |
| `client.results.describe(result_ref)` | A result description containing `result_ref`, `execution_id`, and an `outputs` mapping of named output descriptions, without loading tables. |
| `client.results.table(result_ref, output_name)` | An independent DataFrame or GeoDataFrame reconstructed from the table's Arrow download. |
| `client.results.rows(result_ref, output_name, limit=100, cursor=None)` | A bounded page with `rows` and `next_cursor`, using the requested maximum row count and optional continuation cursor. |
| `client.reports.get(execution_id)` | The complete `ExecutionReport` from milestone 10, fetched through the full JSON download without loading tables. |
| `client.reports.inspect(execution_id)` | A bounded inspection with `kind="complete"` and the full inline report, or `kind="preview"` and the fixed summary sketched below. |
| `client.reports.download(execution_id, path)` | Streams the complete report as JSON to `path`; returns `None`. |
| `client.results.download_table(result_ref, output_name, path)` | Streams the named table as an Arrow IPC stream to `path`; returns `None`. |

`description.outputs` contains exactly the root metric's declared output names.
The table, row, and table-download methods address one of those outputs through
`(result_ref, output_name)`. Output descriptions reuse the fields established for
local result metadata:

```python
output = description.outputs["area"]

output.scope
output.table_schema
output.owner_scope
```

The description retains the schema and concrete provider scope used by the
execution, including empty outputs. Unpartitioned outputs have
`scope="unpartitioned"` and `owner_scope=None`. The retrieved frame remains an
ordinary native table; its associated description carries the scope and schema.
Returned tables, descriptions, and complete reports remain usable after the
client closes, and caller mutations cannot alter retained state or later reads.

Local callers keep the `RunResult` and `ResultStore` contracts from milestones
8 and 10, including `.metadata.outputs`, `.metadata.execution_id`, and the eagerly
attached complete `.report`.

### Generated table metadata

`table_schema` is the existing `OutputMetadata` Pydantic model generated from the
author's Pandera declaration. Reuse its column types, nullability, descriptions,
units, geometry-column name, and declared CRS. The result description and its
named output descriptions are ordinary Pydantic models containing this retained
metadata. Populate execution identifiers, result references, and provider scopes
from the retained execution description.

For a described result, ordinary Pydantic methods provide the metadata payload
and the JSON Schema describing its structure:

```python
from lyra.contracts.outputs import OutputMetadata

metadata = description.outputs["area"].table_schema

payload = metadata.model_dump(mode="json")
metadata_schema = OutputMetadata.model_json_schema()
```

`payload` describes this particular table. `metadata_schema` describes the
structure of an `OutputMetadata` response. Pydantic generates API schemas from
the response models, with the HTTP and MCP frameworks consuming those models.
Authors continue declaring Pandera schemas; Lyra generates and retains the
descriptive metadata during registration and result publication.

`metadata.columns` contains the existing `ColumnMetadata` objects, exposing
`name`, `dtype`, `nullable`, `description`, `unit`, and `parameters`.
For a spatial output, `metadata.geometry` identifies the geometry column, whose
`parameters["crs"]` retains the declared CRS. Retrieval reads the retained
description independently of the number of rows or the active registration.
Keep application-specific ownership and execution fields in the enclosing result
and output descriptions. The first version requires no additional dependency or
general-purpose schema framework.

### HTTP routes and MCP tools

Expose five HTTP retrieval endpoints and four MCP tools. Paths are relative to
the deployment's API base URL:

| Operation | HTTP | MCP tool |
| --- | --- | --- |
| Describe a result | `GET /results/{result_ref}` | `lyra_describe_result(result_ref)` |
| Read table rows | `GET /results/{result_ref}/tables/{output_name}/rows?limit=100&cursor=...` | `lyra_get_result_rows(result_ref, output_name, limit=100, cursor=None)` |
| Download a table | `GET /results/{result_ref}/tables/{output_name}/download` | `lyra_download_table(result_ref, output_name)` returns a download link. |
| Inspect a report | `GET /executions/{execution_id}/report/inspection` | `lyra_inspect_report(execution_id)` |
| Download a complete report | `GET /executions/{execution_id}/report/download` | Follow the link from the report preview. |

Existing job-based routes and tools continue accepting `job_id`. All four new
MCP tools are read-only and use the shared payloads sketched in this milestone,
with SDK-generated response wrappers where applicable.

Successful responses from these HTTP retrieval endpoints use `200`, including
partial report previews. The inspection's `kind` identifies whether the report
is complete.

An MCP row request uses these arguments:

```json
{
  "name": "lyra_get_result_rows",
  "arguments": {
    "result_ref": "result_example",
    "output_name": "area",
    "limit": 100,
    "cursor": null
  }
}
```

It returns the `rows` and `next_cursor` payload. For HTTP, omit the `cursor` query
parameter when requesting the first page.

`lyra_download_table` returns this JSON payload; following its URL downloads the
complete Arrow table:

```json
{
  "result_ref": "result_example",
  "output_name": "area",
  "download_url": "https://lyra.example.com/results/result_example/tables/area/download"
}
```

All returned `download_url` values are absolute URLs built from the deployment's
public API base URL, including links in report previews and oversized-row errors.
They use the deployment's existing authentication. Example hosts and identifiers
are illustrative; the route patterns above define the retrieval contract.

Python methods share these endpoints: `results.describe()` uses the description
endpoint, and `results.rows()` uses the row endpoint. `results.table()` and
`results.download_table()` share the Arrow endpoint. `reports.inspect()` uses the
inspection endpoint, while `reports.get()` and `reports.download()` share the
complete-report endpoint.

### Lookup errors and validation

Both Python clients reuse the domain exceptions from `lyra.contracts.errors`
defined in milestones 8 and 10, with the same attributes and shared
`ResultAccessError` base. Apply the same error categories across Python, HTTP,
and MCP:

| Condition | HTTP | Error code | Python exception |
| --- | --- | --- | --- |
| Malformed result reference | 422 | `invalid_result_reference` | `InvalidResultReferenceError` |
| Malformed execution ID | 422 | `invalid_execution_id` | `InvalidExecutionIDError` |
| Invalid cursor or page limit | 422 | `invalid_pagination` | `InvalidPaginationError` |
| Missing result or undeclared output name | 404 | `result_not_found` | `ResultNotFoundError` |
| Missing execution report | 404 | `report_not_found` | `ReportNotFoundError` |
| Damaged required result data or relationships | 500 | `result_integrity_error` | `ResultIntegrityError` |
| Damaged retained report or its required relationships | 500 | `report_integrity_error` | `ReportIntegrityError` |
| Next row cannot fit in an otherwise empty JSON page | 422 | `row_too_large` | `RowTooLargeError` |

Result lookup exceptions expose `result_ref` and `output_name`, with
`output_name=None` for whole-result reads. Report lookup exceptions expose
`execution_id`. `RowTooLargeError` also exposes `download_url`, as sketched below.
Errors follow the requested lookup: a missing report required by an existing
result is a result integrity error; a report whose required result description
is missing has a report integrity error. A valid empty output succeeds normally.

HTTP and MCP domain failures use a small shared payload containing a stable
`code`, a short `message`, and the relevant exception attributes. Both Python
clients map structured domain failures to the corresponding exceptions without
parsing message text. Message wording is flexible; callers branch on codes and
fields. The oversized-row payload is sketched below. Its HTTP `422` status
identifies a JSON row request that cannot be fulfilled; the error code and Arrow
link provide the actionable detail. Integrity failures use HTTP `500` because
damaged retained state prevents the server from fulfilling the lookup.

Expose `InvalidPaginationError` from `lyra.contracts.errors`, under
`ResultAccessError`, for both cursor and limit validation. It exposes `result_ref`,
`output_name`, and `parameter`, with `parameter` equal to `"cursor"` or `"limit"`.
It covers malformed cursors, cursors used with the wrong result or named table,
and invalid page limits. Its shared payload is:

```json
{
  "code": "invalid_pagination",
  "message": "The cursor is invalid for this table.",
  "result_ref": "result_example",
  "output_name": "area",
  "parameter": "cursor"
}
```

MCP argument validation continues using ordinary SDK validation errors. Domain
lookup failures remain distinguishable from argument-validation failures;
the shared domain payload does not replace SDK argument validation. MCP protocol
envelopes remain SDK-owned, with the shared codes used for domain failures.

With an open client, callers can catch the same lookup exception used for local
access:

```python
from lyra.contracts.errors import ResultNotFoundError

try:
    area = client.results.table(result_ref, "area")
except ResultNotFoundError as error:
    print(error.result_ref, error.output_name)
```

Asynchronous callers use the same exception with an awaited table request.

### Bounded inspection and file downloads

Use inspection for a bounded view, and downloads to save complete content:

```python
with LyraClient("lyra.example.com", agent_api_key="agent-key") as client:
    inspection = client.reports.inspect(execution_id)
    client.reports.download(execution_id, "report.json")
    client.results.download_table(result_ref, "area", "area.arrow")
```

`reports.inspect()` returns one of two variants distinguished by `kind`. These
payloads are shared with HTTP and MCP; an SDK-generated MCP `result` wrapper may
surround them. Python callers use ordinary model fields such as
`inspection.kind` and, for a complete inspection, `inspection.report`.

For a small report, return the complete `ExecutionReport` from milestone 10. This
sketch uses Python notation to include its complete JSON representation without
abbreviating the report:

```python
{
    "kind": "complete",
    "report": report.model_dump(mode="json"),
}
```

For a report with oversized parameters, return the fixed preview:

```json
{
  "kind": "preview",
  "execution_id": "exec_large_parameters",
  "metric": "block_area",
  "status": "succeeded",
  "omitted": ["report"],
  "download_url": "https://lyra.example.com/executions/exec_large_parameters/report/download"
}
```

For a large graph, return the same preview shape:

```json
{
  "kind": "preview",
  "execution_id": "exec_large_graph",
  "metric": "regional_summary",
  "status": "succeeded",
  "omitted": ["report"],
  "download_url": "https://lyra.example.com/executions/exec_large_graph/report/download"
}
```

`metric` is the root metric name. `omitted=["report"]` explicitly identifies the
absence of the complete report body, including its request, parameters, and graph
details. The summary copies only the execution ID, root metric name, and status
from the report. Both oversized cases use this fixed summary; the server need
only determine whether the complete inspection fits, without diagnosing which
section caused the overflow.

The example identifiers, metric names, and deployment host are illustrative.
Inspection and downloads use ordinary authentication and remain available through
the durable identifiers after job expiry. `reports.get(execution_id)` always
returns the complete report, regardless of the inspection variant.

`results.download_table()` and `reports.download()` stream into a temporary file
in the destination directory. Complete the download and close the response and
temporary file before replacing the destination. Successful completion replaces
the destination, overwriting any existing file, and returns `None`.

On failure or cancellation, remove the temporary file and leave an existing
destination unchanged. If no destination existed, leave it absent. Require the
parent directory to exist and expose ordinary filesystem errors, including
`FileNotFoundError` for a missing parent. Use standard file operations for this
behavior. Asynchronous calls follow the same rules, with cleanup in the calling
operation under milestone 2's cancellation contract.

`download_table()` always writes an Arrow IPC stream, regardless of the filename.
Job-based JSON downloads through `client.results.download(job_id, path)` and
`client.jobs.run_to_file(...)` retain milestone 2's contract.

### Reading Arrow downloads

Use ordinary PyArrow and GeoPandas readers for saved table downloads. A spatial
download uses the IPC stream reader followed by GeoPandas reconstruction from
the standard GeoArrow WKB and CRS metadata:

```python
import geopandas as gpd
import pyarrow as pa

with open("inspection_points.arrow", "rb") as source:
    with pa.ipc.open_stream(source) as reader:
        table = reader.read_all()

points = gpd.GeoDataFrame.from_arrow(table)
```

A nonspatial download uses the same stream reader and ordinary pandas conversion:

```python
with open("area.arrow", "rb") as source:
    with pa.ipc.open_stream(source) as reader:
        area = reader.read_all().to_pandas()
```

Empty downloads remain valid IPC streams containing the declared schema. Spatial
reconstruction retains active `geometry` and its declared CRS, including when no
rows are present. `results.table()` uses this same download format and standard
reconstruction path to return native tables. Both the server and clients use the
libraries already present in the project.

### Paginated JSON rows

Use `results.rows()` to inspect a named output as JSON objects. With an open
client, request the first page:

```python
page = client.results.rows(result_ref, "area", limit=100)

page.rows
page.next_cursor
```

`rows` is a list of dictionaries, one per complete table row. `next_cursor` is an
opaque string when more rows remain, or `None` when finished. Omitting `cursor`
or passing `None` starts the first page. Continue only when a cursor is present:

```python
if page.next_cursor is not None:
    next_page = client.results.rows(
        result_ref,
        "area",
        limit=100,
        cursor=page.next_cursor,
    )
```

The shared JSON page payload uses `rows` and `next_cursor`, with JSON `null` for
completion. An empty output returns:

```json
{
  "rows": [],
  "next_cursor": null
}
```

`limit` is the maximum requested number of rows. The encoded response-size bound,
including envelope overhead, may produce a smaller page. A short page does not
signal completion; callers use `next_cursor`. Following the returned cursors for
the same retained result and named output visits every stored row exactly once,
including distinct rows with equal values, provided every individual row fits.
No relative row order across separate reads is promised.

Schema and provider scope remain available through `results.describe(result_ref)`.
An oversized row uses the error and Arrow fallback described below.

### Oversized-row errors

Expose `RowTooLargeError` from `lyra.contracts.errors`, sharing the existing
`ResultAccessError` base. Both Python clients raise it when the next row cannot
fit in an otherwise empty JSON page. It exposes `result_ref`, `output_name`, and
`download_url`, so callers can obtain the complete table without parsing an error
message.

HTTP and MCP use the stable error code `"row_too_large"`, the same three fields,
and a short message. The shared error payload is:

```json
{
  "code": "row_too_large",
  "message": "The next row exceeds the JSON page size limit.",
  "result_ref": "result_example",
  "output_name": "inspection_points",
  "download_url": "https://lyra.example.com/results/result_example/tables/inspection_points/download"
}
```

The reference and deployment host are illustrative. The payload remains bounded
and contains no row contents. Transport-specific envelopes may surround it. The
absolute download link uses ordinary authentication and retrieves the entire
named table.

If rows before the oversized row fit in the current page, return those rows and
set `next_cursor` to resume at the oversized row. Requesting that continuation
raises the error. A first row that is oversized fails the initial page request.
Neither case skips the row or advances the cursor past it. The retained result
remains valid and completely retrievable through Arrow.

Here, `result_ref` identifies a successful `block_findings` result declaring
`inspection_points`. With an open client and `cursor` set to a returned
continuation for that output or `None` for the first page, a caller can catch the
error and download the table:

```python
from lyra.contracts.errors import RowTooLargeError

try:
    page = client.results.rows(
        result_ref, "inspection_points", limit=100, cursor=cursor
    )
except RowTooLargeError as error:
    client.results.download_table(
        error.result_ref, error.output_name, "inspection_points.arrow"
    )
```

The download includes all rows in the named output, including any already read
through pagination. Asynchronous callers use the same exception and fields with
`await` on the row request and download.

### JSON cell encoding

Use one fixed JSON encoding per logical table type across Python row access,
HTTP, and MCP. The retained table schema identifies each column's logical type:

| Logical type | JSON representation |
| --- | --- |
| String, including IDs and row keys | String preserving its exact spelling. |
| Signed 64-bit integer | Decimal string, including small integers such as `"12"` or `"-3"`. |
| Finite 64-bit float | JSON number with enough precision to reconstruct the same finite 64-bit value. |
| Boolean | `true` or `false`. |
| Geometry | WKT string in the declared CRS, using full coordinate precision. |
| Missing value | `null`. |

Integer columns use decimal strings for every non-null value, preserving the full
signed 64-bit range across JSON clients. Their logical schema type remains integer.
String IDs such as `"0012"` retain their spelling and remain strings independently
of whether their contents resemble numbers.

Every row includes every declared column, including columns whose value is `null`.
Geometry coordinates remain in the output's declared CRS, retained in its schema;
JSON row access does not reproject them. WKT serialization uses full coordinate
precision without rounding. Missing geometry is `null`, while empty geometry keeps
its WKT representation, such as `"POINT EMPTY"`.

For an illustrative spatial output with declared CRS EPSG:32614, a page could be:

```json
{
  "rows": [
    {
      "location_id": "0012",
      "row_key": "main",
      "observation_count": "9007199254740993",
      "area_m2": 125.5,
      "reviewed": true,
      "note": null,
      "geometry": "POINT (500000 2150000)"
    }
  ],
  "next_cursor": null
}
```

`results.rows()` returns these JSON representations directly, including integer
and geometry strings. `results.table()` reconstructs native nullable integer
columns and GeoPandas geometry through Arrow, using the retained schema and CRS.
These cell encodings apply to table row pages; report fields retain the report
contract from milestone 10.

### Asynchronous access

`AsyncLyraClient` exposes the same methods, arguments, and return values with
`await`, including job execution, paginated rows, inspection, and both download
methods:

```python
from lyra.api import AsyncLyraClient

async def read_area(result_ref: str, execution_id: str):
    async with AsyncLyraClient(
        "lyra.example.com", agent_api_key="agent-key"
    ) as client:
        description = await client.results.describe(result_ref)
        area = await client.results.table(result_ref, "area")
        report = await client.reports.get(execution_id)
    return description, area, report
```

## Completion boundary

Both Python client interfaces, HTTP, and MCP can retrieve final named metric
outputs and inspect reports after job records expire. Local callers can reopen
the store and obtain complete reports. Small reports are complete inline; large
reports are explicit previews with usable full downloads.

Verify that successful job status and terminal result responses expose matching
durable identifiers. Both Python clients support the sketched description, native
table, paginated rows, complete report, bounded inspection, and file-download
methods after job expiry. Description and report reads do not load tables; native
table retrieval uses Arrow IPC streams, and named-table downloads preserve those
stream bytes independently of the filename. Verify the
`application/vnd.apache.arrow.stream` content type and ordinary PyArrow/GeoPandas
readers for saved downloads, including empty schemas and standard GeoArrow WKB
and CRS metadata.

For both file-download methods and client modes, verify successful replacement
of an existing destination, preservation of that destination on failure or
cancellation, and removal of temporary files. Cover a previously absent
destination and a missing parent directory, preserving ordinary filesystem
errors. Use temporary files with mocked or in-process HTTP responses.

Verify the five HTTP endpoints, four read-only MCP tools, and shared Python
endpoint mappings. Download links from tools, report previews, and oversized-row
errors must be absolute, preserve the deployment's public API base URL, and work
with ordinary authentication after job expiry.

Verify that both Python clients expose the shared lookup exceptions and their
attributes, and that HTTP and MCP preserve the same domain error codes. Verify
the specified HTTP status mappings, including `200` for complete and preview
report inspections. Check `InvalidPaginationError` and its `parameter` field for
malformed cursors, cursors used with the wrong result or table, and invalid page
limits. Distinguish ordinary MCP SDK argument-validation errors from domain
lookup failures.

Isolated and in-process checks cover authentication, advertised MCP schemas and
actual envelopes, bounded responses, all reference error categories, job expiry,
and result assembly. Verify SQL-to-Arrow-to-client type/geometry/CRS preservation,
empty and all-null schemas, pagination boundaries, and duplicate-valued
unpartitioned rows. Verify that result descriptions reuse the generated
`OutputMetadata`, retaining declared types, nullability, descriptions, units, and
geometry/CRS for empty outputs and reopened results without loading plugins.
Verify empty pages, completion cursors, and short pages with
more rows remaining. Row-page checks cover encoded response bounds including
envelope overhead, oversized individual rows, no silent truncation or cursor
advancement past an oversized row, and complete Arrow download fallback.
Cover an oversized first row and preceding rows returned before an oversized row,
with the continuation failing at that row. Verify `RowTooLargeError` and its
attributes in both Python clients, the shared HTTP/MCP error code and payload,
and authenticated Arrow fallback for the complete named table.
Check the fixed JSON cell encodings, including small integers and signed 64-bit
extremes as strings, finite float precision, exact ID spelling, and all declared
columns in rows with null values. Verify WKT coordinate precision and declared
CRS, and distinguish missing geometry from empty geometry. Python row access
preserves these JSON representations while Arrow table access reconstructs native
types.
Check native Python return types and isolation when a caller
changes IDs, values, geometry, CRS, or metadata, including repeated reads of the
same stored value. Verify that local `RunResult` tables and output metadata refer
to the same execution and retain their association across subsequent runs and
reads. Report checks include complete inline inspections, the same fixed preview
shape for oversized parameters and graphs, explicit whole-report omission
information, and complete downloads exceeding inspection limits.

Retrieve results from separate concrete block and tract metrics and from runs
across changed provider code or origin revisions, including identical bare IDs and
empty outputs.
Check that Python, Arrow, JSON, and report inspection preserve each concrete scope
through cached/fresh assembly, reopened local access, and simulated job expiry.

## Deferred or excluded work

Managed GIS files follow in [milestone 12](12-geoparquet-exports.md). This milestone
does not create a permanent Arrow or report artifact store.

MCP-only report pagination/chunk tools, public graph navigation, finding lookups,
row/group traces, empty-group inspection, intermediate membership APIs, and direct
retrieval of intermediate metric tables, provider records, or operation
correspondence tables remain deferred. Retrieval supports the current store
without migrations or legacy readers across incompatible schema/code changes,
and does not restore external inputs that are no longer available.

## Reference points

Current table conversion is split between [server results](../src/lyra/server/results.py)
and [client reconstruction](../src/lyra/api/client/_tables.py).
Existing schema generation uses [Pandera declaration compilation](../src/lyra/sdk/output_schema.py)
and [Pydantic output metadata](../src/lyra/contracts/outputs.py).
PyArrow documents the [IPC stream readers and writers](https://arrow.apache.org/docs/python/ipc.html).
GeoPandas provides the [Arrow reader](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.from_arrow.html)
used for spatial reconstruction.
