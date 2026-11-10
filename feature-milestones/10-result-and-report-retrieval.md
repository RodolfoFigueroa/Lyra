# 10. Result and report retrieval

Expose retained values and execution reports consistently through Python, HTTP,
and MCP without depending on transient jobs. This milestone completes decisions
13 and 14 and the access obligations of decisions 16, 17, and 22 and R1/R3/R7/R10
in [the ledger](../DECISIONS.md).

## Accepted scope

### Shared read-only access

Use shared reference definitions and read-only lookup logic for local Python,
remote Python clients, HTTP, and MCP. Run/result interfaces provide the durable
execution ID alongside ordinary outputs. The stored result description identifies
the values and selected groups to retrieve, and the associated execution report
remains addressable after RQ/job expiry.

Use the native synchronous/asynchronous client responsibilities established in
[milestone 2](02-native-httpx-clients.md) and typed FastMCP registration from
[milestone 3](03-mcp-and-shared-references.md). Preserve authentication, tool
annotations, SDK validation, response limits, and common domain operations.

Malformed references are validation errors; well-formed references without a
target are not found; broken required retained relationships are integrity errors.
Lookups do not rerun handlers, acquire external data, reconstruct historical
producers, fabricate records, or run recursive integrity sweeps. Intentionally
unstored intermediates are outside report retrieval and do not make a report
incomplete. Concrete reference encoding, envelopes, cursors, and size limits remain
implementation choices within these contracts.

### Bounded reports and full downloads

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

Preserve the same supported logical types and applicable standard columns across
Python, Arrow, and JSON: metric `location_id`/`row_key`, operation
`source_location_id`/`location_id`, provider `location_id`, and spatial `geometry`.
Retain dataset/revision qualifications, named-output and result/table scope,
nullability, descriptions, units, and declared CRS in the applicable schema and
metadata. Use standard WKB/spatial conversions, including native CRS through
SQL-to-Arrow-to-client reconstruction. Empty and all-null outputs retain their
declared schema. Internal storage fields do not become author-facing columns.

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
