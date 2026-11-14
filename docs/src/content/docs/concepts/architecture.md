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

For deployment, select distributions in `lyra.toml`; for local execution, pass their names to `LocalRunner(plugins=[...])`. Both paths load live definitions before preparing requests:

```text
Selected installed distributions
  → entry-point factories → live definitions
      → API catalog → discovery and request preparation
      → worker catalog → compatible request preparation
      → local catalog → local execution

Client → authenticated API → Redis / Redis Queue (RQ)
  → worker → executor validation → resources and handlers
  → retained result → client download
```

Both API and workers must be able to import every enabled plugin and its dependencies; an import failure stops startup. Keep loading and request preparation free of external input/output (I/O). Lyra opens requested resources during execution.

During catalog compilation, Lyra validates:

- Node names and dependencies
- Location contracts and accepted source bindings
- [Canonical routes](../selection/) that convert selections into native inputs

Each public metric has its own request and output contracts. Private metrics can supply dependencies, and you can execute them through `run_development()`.

A discovery fingerprint identifies public catalog metadata. Administrative discovery also describes installed plugins and queue assignments. API and workers use a separate [`build_revision`](../identity/#distinguish-discovery-from-computation-identity) to check deployment compatibility.

## Execute a job

A job begins when you submit a registered metric with `inputs` and `parameters` mappings and an optional `cache_revision`. The API validates selections and uses each selected node’s Pydantic model to prepare [effective parameter values](../../reference/parameters/#defaults-and-validators). The queued payload records:

- The normalized request and complete effective values for each node use
- The deployment build revision
- Location scopes and pinned data revisions

If the build revisions differ, the worker fails the job with `deployment_changed` before acquiring resources or calling handlers. You can resubmit against the current deployment.

A compatible worker revalidates the queued values, including explicit defaults, and prepares the request against its live catalog. The executor validates the complete plan before acquiring resources.

The worker resolves IDs and canonical routes, then executes dependencies sequentially. It passes each handler its declared arguments:

- Resolved locations and dependency tables
- Parameter models
- Runtime contexts and requested resources

Lyra gives handlers independent parameter models and returns independent values through inspection. Changes to these values, including nested containers and models, cannot alter retained execution state.

The worker validates output tables, then saves required final values and a result description in the shared SQLite store. Each execution gets a distinct reference to its result description. RQ retains that reference, and the worker records execution observations.

The API serves tables and provenance by job ID until the job expires. SQLite retains the saved values and descriptions indefinitely.

## Know each component’s responsibility

Use this table to identify which component handles discovery, execution, or result access:

| Component | Responsibility |
| --- | --- |
| `lyra.server` | Web endpoints, authentication, job queues, worker processes, and result access |
| `lyra.sdk` | Live plugin definitions, registration, and inspection |
| `lyra.contracts` | Shared declaration, request, discovery, and result models |
| `lyra.data` | Native tables, relational conversions, serialization, and geospatial exports |
| `lyra.runtime.planning` | Compiles live declarations and prepares routes, effective values, and pinned inputs |
| `lyra.runtime` | Runs prepared calculations, manages resources, and saves or reopens immutable results |
| `lyra.api` | Synchronous and asynchronous HTTP clients and the administrative command-line interface |
| `lyra.utils` | Date and geometry helpers, plus Earth Engine helpers with the `earth-engine` extra |
| RQ workers | Trusted imports, execution, result validation, and job supervision |
| Redis | Queues, registries, job metadata, and retained result references |
| SQLite | Shared immutable values, completion, schemas, and final-result descriptions |
| PostGIS | Optional plugin-owned spatial data and queries |
| Model Context Protocol (MCP) adapter | Agent tools using the same submission and result services |

## Understand the boundaries

Public routes expose health, metric schemas, and collection metadata. Job routes and MCP tools require the agent key. Administrator routes require a separate key.

Catalog compilation validates live definitions; execution establishes access to data sources. Check worker coverage and startup diagnostics as part of deployment verification.

Plugin imports and factories execute trusted Python in API and worker processes. Handlers run with their process’s permissions. Metric declarations describe inputs and outputs; they do not intercept filesystem or network access. Operators control installed packages, secrets, mounts, and network permissions.

Workers resolve ID selections and execute the graph in one [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials). They close provider resources after execution, including on failure.

Continue to [deployment](../../operate/deployment/) for setup or [graph execution](../graphs/) for table flow and reuse.
