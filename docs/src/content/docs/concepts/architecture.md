---
title: Understand how Lyra runs a job
description: Follow a contract from its plugin package through API discovery, worker execution, and result access.
contentType: Conceptual
contentPlan: concepts/architecture
sidebar: { label: "Understand Job Execution" }
---

The API, workers, and local runners build their catalogs from the same enabled [plugin definitions](../model/#a-plugin-registers-nodes). These catalogs describe available calculations and validate requests before execution. This page follows a request from plugin discovery to result retrieval.

## Load the catalog

Lyra loads only the installed distributions, or Python packages, that you enable. Each distribution registers a factory as the `plugin` entry point in `lyra.plugins`. The factory runs synchronously, takes no arguments, and returns `PluginDefinition`.

For deployment, select distributions in `lyra.toml`; for local execution, pass their names to `LocalRunner(plugins=[...])`. Both paths load live definitions before preparing requests. For server jobs, the API uses Redis Queue (RQ) to queue work in Valkey.

```text
Selected installed distributions
  → entry-point factories → live definitions
      → API catalog → discovery and request preparation
      → worker catalog → compatible request preparation
      → local catalog → local execution

Client → authenticated API → RQ job queue in Valkey
  → worker → executor validation → resources and handlers
  → retained result → client download
```

Both API and workers must be able to import every enabled plugin and its dependencies; an import failure stops startup. Keep loading and request preparation free of external input/output (I/O). Lyra opens requested resources during execution.

During catalog compilation, Lyra validates:

- Node names and dependencies
- Location contracts and accepted source bindings
- [Canonical routes](../selection/) that convert selections into native inputs

Each public metric has its own request and output contracts. Private metrics can supply dependencies, and you can execute them through `run_development()`.

The [discovery fingerprint](../identity/#distinguish-discovery-from-computation-identity) identifies public catalog metadata. If you have administrator access, run `lyra-admin catalog show` to inspect the catalog and its plugins.

## Execute a job

A job begins when you submit a registered metric with `inputs` and `parameters` mappings. The API validates your selections and applies each node’s Pydantic model to prepare [effective parameter values](../../reference/parameters/#defaults-and-validators). The API saves these details with the queued job.

- The normalized request and effective parameters for each use of a node
- The [computation identity](../identity/#distinguish-discovery-from-computation-identity), including the selected routes
- Provider scopes and their data revisions
- Requested [interventions](../../use/spatial-inputs/#prepare-changes-for-a-catalog) and their targets

The worker prepares the same request using its own catalog and checks it against the plan the API accepted. If the plans disagree, the worker fails the job with `bound_plan` before opening resources or running handlers.

The worker resolves IDs and canonical routes, then executes dependencies sequentially. It passes each handler its declared arguments:

- Resolved locations and dependency tables
- Parameter models
- Runtime contexts and requested resources

Lyra gives handlers independent parameter models and returns independent values through inspection. Changes to these values, including nested containers and models, cannot alter retained execution state.

The worker saves validated output values in SQLite. It commits the result description and execution report together under the job ID you received at submission. Each submission gets a fresh ID, even when every node reuses cached values.

Once SQLite commits, you can read the description, report, and named tables by job ID. These reads work even if the worker stops before RQ records success. SQLite also serves terminal job status and result downloads.

## Know each component’s responsibility

Use this table to identify which component handles discovery, execution, or result access:

| Component | Responsibility |
| --- | --- |
| `lyra.server` | Web endpoints, authentication, the job queue, worker processes, and result access |
| `lyra.sdk` | Live plugin definitions, registration, and inspection |
| `lyra.contracts` | Shared declaration, request, discovery, and result models |
| `lyra.data` | Native tables, relational conversions, serialization, and geospatial exports |
| `lyra.runtime.planning` | Compiles live declarations and prepares routes, effective values, and pinned inputs |
| `lyra.runtime` | Runs prepared calculations, manages resources, and saves or reopens immutable results |
| `lyra.api` | Synchronous and asynchronous HTTP clients and the administrative command-line interface |
| `lyra.utils` | Date and geometry helpers, plus Earth Engine helpers with the `earth-engine` extra |
| RQ workers | Trusted imports, execution, result validation, and job supervision |
| Valkey | Dispatch, live job and worker state, and temporary failure diagnostics |
| SQLite | Shared immutable values, terminal job history, schemas, reports, and final-result descriptions |
| PostGIS | Optional plugin-owned spatial data and queries |
| Model Context Protocol (MCP) adapter | Agent tools using the same submission and result services |

## Understand the boundaries

Public routes expose health, metric schemas, and collection metadata. Job routes and MCP tools require the agent key. Administrator routes require a separate key.

Lyra validates plugin definitions at startup. Workers connect to data sources when they run a job. To verify a deployment, inspect the worker startup logs and [check for a worker heartbeat](../../operate/runbook/#inspect-workers-and-the-queue).

Plugin imports and factories execute trusted Python in API and worker processes. Handlers run with their process’s permissions. Metric declarations describe inputs and outputs; they do not intercept filesystem or network access. Operators control installed packages, secrets, mounts, and network permissions.

Workers resolve ID selections and execute the graph in one [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials). They close provider resources after execution, including on failure.

Continue to [deployment](../../operate/deployment/) for setup or [graph execution](../graphs/) for table flow and reuse.
