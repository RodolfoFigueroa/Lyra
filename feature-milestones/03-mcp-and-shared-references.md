# 3. MCP integration and shared references

Let the MCP SDK own tool mechanics and give active result references one shared
contract. Existing tools and retrieval remain usable while validation, dispatch,
and reference interpretation move to their designated owners.

## Accepted scope

Register typed handlers through FastMCP bundled with the MCP SDK. Function
signatures and response models define advertised schemas; the SDK validates
arguments and dispatches calls. Remove the separate tool-contract registry,
duplicate schema adapters, name-based dispatcher, and low-level registration
machinery.

Preserve authentication, tool annotations, bounded responses, and shared domain
operations. MCP remains part of the existing API deployment and needs no separate
service. Use ordinary SDK validation errors instead of custom argument-correction
payloads. Straightforward SDK-compatible response shapes are acceptable, including
a `result` wrapper where a union response requires it.

Centralize active result-reference formatting and parsing in the shared contracts
layer so contracts, MCP, and Python clients do not maintain competing definitions.
Retain raw-job-ID convenience at the Python client boundary, normalizing it before
shared reference handling. Delete the unused `ResultReference` and `Bounds` types
and the empty `server/loaders` package without removing active retrieval behavior.

Public reference inputs use shared strict string validation without coercion or
string/repr fallbacks. Malformed references are validation errors; valid references
without a target are not found; broken required stored relationships are integrity
errors. Exact reference syntax and transport error envelopes remain implementation
choices within those shared semantics.

Reference parsing and domain lookup are distinct boundaries: a syntactically valid
identifier can still have no retained target. Lookups are read-only and do not
rerun handlers, acquire external data, fabricate missing records, or recursively
sweep the store. Required relationship damage is an integrity failure, rather
than an ordinary missing target. Reuse validated domain results without adding
another producer-validation layer in the MCP adapter.

## Completion boundary

Existing MCP workflows remain available with SDK-derived schemas and actual
response envelopes that agree. Python, HTTP, and MCP use the same active reference
semantics, with the client's raw-job-ID convenience preserved.

In-process integration checks verify advertised schemas, successful and invalid
calls, authentication, tool annotations, response bounds, and reference lookup
outcomes. Tests exercise supported behavior rather than the removed registration
structure, and contact no external services.

## Deferred or excluded work

Durable execution IDs are introduced with
[execution reports](09-execution-reports-and-durable-success.md), following the
same shared-contract ownership. New MCP report inspection and paginated row tools
arrive in [milestone 10](10-result-and-report-retrieval.md).

Per-row and output-group trace-reference types are deferred. Do not introduce
placeholder trace APIs, MCP-only report pagination, or a replacement custom tool
framework.

## Reference points

The current MCP implementation contains [tool contracts](../src/lyra/server/mcp/models.py),
[dispatch and reference handling](../src/lyra/server/mcp/tools.py), and
[server registration](../src/lyra/server/mcp/server.py).
Shared-reference work also touches [result contracts](../src/lyra/contracts/jobs.py),
[geometry contracts](../src/lyra/contracts/geometry.py), and the
[Python client parser](../src/lyra/api/client/base.py).
The SDK's [FastMCP implementation](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/fastmcp/server.py)
provides the registration and validation machinery.
