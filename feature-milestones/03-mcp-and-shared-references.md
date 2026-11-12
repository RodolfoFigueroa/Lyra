# 3. MCP integration and shared job identifiers

Let the MCP SDK own tool mechanics and use plain job IDs for active job and result
lookup. Existing tools and retrieval remain usable while validation and dispatch
move to their designated owners.

## Accepted scope

Register typed handlers through FastMCP bundled with the MCP SDK. Function
signatures and response models define advertised schemas; the SDK validates
arguments and dispatches calls. Remove the separate tool-contract registry,
duplicate schema adapters, name-based dispatcher, and low-level registration
machinery.

Expose [milestone 4's shared submission contract](04-plugin-discovery-and-normalized-jobs.md#shared-submission-contract)
through `lyra_run_metric`: its arguments object contains `metric`, `inputs`, and
`parameters`, matching the HTTP request body within MCP's protocol wrapper.
Generate the advertised `inputSchema` from the shared models and typed handlers;
metric-specific selections and parameters remain subject to shared validation.
Milestone 14 extends this object with optional `interventions` through the same
domain operation.

Preserve authentication, tool annotations, bounded responses, and shared domain
operations. MCP remains part of the existing API deployment and needs no separate
service. Use ordinary SDK validation errors instead of custom argument-correction
payloads. Straightforward SDK-compatible response shapes are acceptable, including
a `result` wrapper where a union response requires it.

Use plain job IDs consistently across Python, HTTP, and MCP. MCP result tools
accept `job_id`; submission and inspection responses identify the job with
`job_id`, without a duplicate `result_ref`. Python result helpers accept the same
job ID directly. Centralize job-ID validation in the shared contracts layer.
Remove the `lyra://results/{job_id}` URI contract, its formatting and parsing
helpers, and client-side reference normalization, without compatibility aliases.
Delete the unused `ResultReference` and `Bounds` types and the empty
`server/loaders` package without removing active retrieval behavior.

Public job-ID inputs use shared strict nonempty string validation without coercion
or string/repr fallbacks. Invalid identifiers are validation errors; valid
identifiers without a target are not found; broken required stored relationships
are integrity errors. Transport error envelopes remain implementation choices
within those shared semantics.

Identifier validation and domain lookup are distinct boundaries: a syntactically
valid identifier can still have no retained target. Lookups are read-only and do not
rerun handlers, acquire external data, fabricate missing records, or recursively
sweep the store. Required relationship damage is an integrity failure, rather
than an ordinary missing target. Reuse validated domain results without adding
another producer-validation layer in the MCP adapter.

## Completion boundary

Existing MCP workflows remain available with SDK-derived schemas and actual
response envelopes that agree, including any SDK-generated `result` wrapper.
Python, HTTP, and MCP use the same job-ID validation and lookup semantics. Client
and agent-facing guidance describes the job-ID inputs and responses directly.

In-process integration checks verify advertised schemas, successful and invalid
calls, authentication, tool annotations, response bounds, and job-ID lookup
outcomes. Tests exercise supported behavior rather than the removed registration
structure, and contact no external services.

Verify that HTTP and MCP submissions normalize equivalent request contents and
apply the same omission, null, unknown-field, and metric-specific validation rules
from milestone 4, while retaining their framework-owned protocol and error wrappers.

## Deferred or excluded work

Durable execution IDs are introduced with
[execution reports](10-execution-reports-and-durable-success.md), following the
same shared-contract ownership. New MCP report inspection and paginated row tools
arrive in [milestone 11](11-result-and-report-retrieval.md).

Per-row and output-group trace-reference types are deferred. Do not introduce
placeholder trace APIs, MCP-only report pagination, or a replacement custom tool
framework.

## Reference points

The current MCP implementation contains [tool contracts](../src/lyra/server/mcp/models.py),
[dispatch and reference handling](../src/lyra/server/mcp/tools.py), and
[server registration](../src/lyra/server/mcp/server.py).
Job-ID validation and reference cleanup also touch
[result contracts](../src/lyra/contracts/jobs.py),
[geometry contracts](../src/lyra/contracts/geometry.py), and the
[Python client parser](../src/lyra/api/client/base.py).
The SDK's [FastMCP implementation](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/fastmcp/server.py)
provides the registration and validation machinery.
