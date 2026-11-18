# 08 · Make publication atomic and retries predictable

Publication has two distinct commitments. A computation key retains its first successfully written data. A completed run publishes its report and result description together. Both use the production SQLite writers, with failures visible to the caller.

## Computation retries

Check the key inside the write transaction, including when the caller previously observed a miss. If a value already exists, apply the following rules.

| Writer | Behavior on retry |
| --- | --- |
| Provider record | Keep the original field values. |
| Metric output | Verify the stored computation header and output registrations, matching input bindings, and stored partitions and row counts against the first completion record. Keep the original rows even when incoming values differ. Conflicting bindings raise an integrity error. |
| Operation correspondence | Keep the original source-to-target mapping when the source partition set agrees, even if incoming targets differ. A different source partition set raises an integrity error. |

Use SQL selection checks in the transaction. Eliminate reconstruction of stored DataFrames and per-cell comparisons with incoming output. A failed write or commit leaves previously committed data intact and exposes no partial value.

## Run publication and reads

Build the execution report once. Before committing it with the result description, check that the two agree, every declared output exists under its expected metric and scope, and selected partitions and row counts match their completion records. A missing partition or count mismatch fails publication. After commit, `LocalRunner` returns a caller-owned copy of the report. A commit error fails the run and propagates to the caller.

Lyra assumes it is the only writer of retained tables. Remove per-cell scalar checks from bounded DataFrame, JSON, and Arrow reads. SQLite enforces `STRICT` scalar types, nullability, Boolean values of 0 or 1, and finite-float bounds. Preserve signed 64-bit integers, Booleans, and nulls in typed frames and Arrow. SQLite can store arbitrary geometry bytes, so validate WKB when reading geometry, including when emitting binary Arrow values. Keep partition and row-count integrity checks. Damaged WKB can surface on inspection or download, and an Arrow integrity error must precede HTTP success headers.

## Verification

Replace `SQLiteStore.publication()` and its test-only callers with exercises of the production writers. Verify an identical retry leaves exactly one committed value per key. Read back the first committed value after each kind of retry, cover both conflict cases, and inject write and commit failures to verify rollback. Remove corruption cases that bypass SQLite's scalar constraints. Exercise missing rows and malformed geometry through supported reads. In the 1,000-row SQLite foundation benchmark, use `MetricTables.publish()` and retain the repeat-publication and reopen checks.
