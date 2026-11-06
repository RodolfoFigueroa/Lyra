---
title: Understand how Lyra runs a job
description: Follow a contract from its plugin package through API discovery, worker execution, and result access.
contentType: Conceptual
contentPlan: concepts/architecture
sidebar: { label: "Understand Job Execution" }
---

Lyra separates publishing a metric’s contract from executing its Python code. The application programming interface (API) server reads the catalog from [plugin manifests](../model/#a-plugin-registers-nodes) and queues validated requests. Workers import trusted plugins, resolve locations, and perform the calculations.

## Follow the two paths

Publishing makes a metric’s contract discoverable, while execution runs its calculation. The two paths share installed plugin packages but use them differently:

```text
Plugin Python definitions
  → generated manifest
  → installed deployment package
      → API reads manifest → public metric catalog
      → worker imports factory → validated execution graph

Client → authenticated API → Redis / Redis Queue (RQ)
  → worker resolves locations and executes graph
  → retained result → client download
```

## Publish the catalog

The deployment image contains Lyra and plugins installed from a locked dependency set. Operators select distributions and routes in `lyra.toml`.

At startup, the API compiles installed manifests into one catalog for discovery and request validation. It also records which queue serves each concrete metric. Compilation validates:

- Family contracts
- Calculation dependencies
- Canonical routes and accepted source bindings

The catalog lists public families and lets you inspect a selected edition. Inspection describes its schemas, output meanings, and concrete sources.

The catalog’s public fingerprint identifies the contract available to clients. Discovery responses and [job provenance](../provenance/) use this fingerprint.

Administrative responses use a separate operational fingerprint to identify the installed plugins and metric queue assignments. Changing a plugin factory or queue assignment changes this fingerprint without changing the public one.

Workers independently validate installed distribution versions and manifests. Workers load the plugin factories for their assigned metrics and dependencies, then compare those definitions with the saved manifests. Workers can start before the API because both read the installed packages.

## Execute a job

You submit a metric and input through the authenticated API. Before queueing the job, the API validates your metric family, edition, parameters, and source selections. It saves the effective parameters, concrete identity, canonical routes, and pinned location revisions. A worker fails the job if it cannot execute the accepted plan.

Lyra stores the accepted plan once, in the queued job’s payload. The worker records execution observations separately. When you retrieve a result, the API combines these records into its provenance.

The selected worker resolves IDs, then runs the accepted routes to produce the metric’s native inputs. It constructs typed arguments and executes calculation dependencies sequentially. It passes each metric the arguments that metric declares:

- Dependency tables
- A runtime context for logging, progress, and scratch storage
- Plugin resources for file or service access

The worker validates the named output tables and saves them in the shared artifact directory. It returns the artifact ID to RQ and records its execution observations in job metadata. The API uses the ID to read the tables and serve results until the job expires.

## Know each component’s responsibility

Use this table to identify which component handles discovery, execution, or result access:

| Component | Responsibility |
| --- | --- |
| `lyra.server` | Web endpoints, authentication, job queues, worker processes, and result access |
| `lyra.sdk` | Tools for defining plugins and generating manifests |
| `lyra.contracts` | Shared models for manifests, requests, and results |
| `lyra.data` | Immutable locations, native tables, artifact readers, serialization, and geospatial exports |
| `lyra.runtime.planning` | Compiles manifest declarations into a catalog and records the chosen routes and effective inputs when Lyra accepts a request |
| `lyra.runtime` | Binds handlers, acquires resources, runs the accepted calculation locally or on workers, and publishes cached artifacts |
| `lyra.api` | Synchronous and asynchronous HTTP clients and the administrative command-line interface |
| `lyra.utils` | Date and geometry helpers, plus Earth Engine helpers with the `earth-engine` extra |
| RQ workers | Trusted imports, execution, result validation, and job supervision |
| Redis | Queues, registries, job metadata, and retained artifact references |
| PostGIS | Optional plugin-owned spatial data and queries |
| Model Context Protocol (MCP) adapter | Agent tools using the same submission and result services |

## Understand the boundaries

Public routes expose health, metric schemas, and collection metadata. Job routes and MCP tools require the agent key. Administrator routes require a separate key.

A valid API catalog proves that Lyra can read the manifest. It does not prove that a worker can import the package or reach its data sources. Check worker coverage and startup diagnostics as part of deployment verification.

Plugins execute trusted Python with worker permissions. Metric declarations describe inputs and outputs; they do not intercept filesystem or network access. Operators control installed packages, secrets, mounts, and network permissions.

Workers resolve ID selections and execute the graph in one [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials). They close provider resources after execution, including on failure.

Continue to [deployment](../../operate/deployment/) for setup or [graph execution](../graphs/) for table flow and reuse.
