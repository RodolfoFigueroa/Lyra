# 2. Native HTTPX clients

Keep synchronous and native asynchronous consumer and administrator clients while
simplifying their transport and lifecycle. This milestone covers decision 16 in
[the ledger](../DECISIONS.md).

## Accepted scope

Back synchronous clients directly with HTTPX `Client` and asynchronous clients
with `AsyncClient`. Remove the background event-loop thread and the bridge that
runs synchronous calls through asynchronous machinery.

Share endpoint definitions, request construction, response validation, error
interpretation, and table conversion across both interfaces. Preserve the
currently supported consumer and administrator operations. Later result and
report endpoints must use these same shared responsibilities.

Replace generator-based polling with two ordinary loops sharing retry,
progress-comparison, and deadline helpers. Small amounts of duplication around
requests, sleeping, streaming, and context management are acceptable; eliminating
that duplication is not a reason to introduce another orchestration framework.

Use standard HTTPX connection, read, write, and pool timeouts. Polling deadlines
are checked between operations. An in-flight request or cleanup may exceed the
overall polling deadline, so the interface does not promise a strict total
wall-clock bound.

Asynchronous requests run in the caller's task and follow ordinary cancellation
and cleanup behavior. Callers finish outstanding operations, or cancel and await
them, before closing the client. Remove custom task tracking and background
cleanup ownership. Immediate return independent of cleanup completion is not a
guarantee, and local cancellation does not cancel a remote job.

## Completion boundary

Both client interfaces remain usable for existing consumer and administrator
workflows, with equivalent request, response, error, and table semantics. Their
documented lifecycle matches native HTTPX behavior.

Isolated checks cover both interfaces, polling success and failure, retries,
deadline handling, streaming and closure, and asynchronous cancellation. Tests
must not depend on a real HTTP service or require the removed background-thread
organization.

## Deferred or excluded work

New Arrow, paginated-row, and execution-report access arrives in
[milestone 10](10-result-and-report-retrieval.md). This milestone provides their
transport foundation without adding those endpoints early. Shared result-reference
ownership belongs to [milestone 3](03-mcp-and-shared-references.md).

Strict total-duration guarantees, custom cancellation ownership, and automatic
remote-job cancellation are outside the accepted client contract.
