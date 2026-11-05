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

Workers independently validate installed distribution versions and manifests. They import the roots they serve and their transitive dependencies, then compare live definitions with the saved contracts. They can start before the API; both obtain their catalog inputs from installed packages.

## Execute a job

You submit a metric and input through the authenticated API. Before queueing the job, the API validates your metric family, edition, parameters, and source selections. It saves the effective parameters, concrete identity, canonical routes, and pinned location revisions. Workers honor those bindings and fail if they cannot, rather than silently replanning.

The selected worker resolves IDs, then runs the accepted routes to produce the metric’s native inputs. It constructs typed arguments and executes calculation dependencies sequentially. It passes each metric the arguments that metric declares:

- Dependency tables
- A runtime context for logging, progress, and scratch storage
- Plugin resources for file or service access

The worker checks contract identity, validates native named table returns, and returns the result to RQ. RQ retains status and results. Clients observe the job and download its data before retention ends.

## Know each component’s responsibility

Use this table to identify which component handles discovery, execution, or result access:

| Component | Responsibility |
| --- | --- |
| FastAPI application | Discovery, request validation, submission, result access, and administration |
| `lyra-sdk` | Plugin definitions, input/output contracts, graph execution, and local running |
| `lyra-api` | Synchronous/asynchronous HTTP clients and administrative CLI |
| `lyra-utils` | Geometry and date helpers; Earth Engine helpers through the `earth-engine` extra |
| RQ workers | Trusted imports, execution, result validation, and job supervision |
| Redis | Queues, registries, metadata, and retained results |
| PostGIS | Optional plugin-owned spatial data and queries |
| Model Context Protocol (MCP) adapter | Agent tools using the same submission and result services |

## Understand the boundaries

Public routes expose health, metric schemas, and collection metadata. Job routes and MCP tools require the agent key. Administrator routes require a separate key.

A valid API catalog proves that Lyra can read the manifest. It does not prove that a worker can import the package or reach its data sources. Check worker coverage and startup diagnostics as part of deployment verification.

Plugins execute trusted Python with worker permissions. Metric declarations describe inputs and outputs; they do not intercept filesystem or network access. Operators control installed packages, secrets, mounts, and network permissions.

Workers resolve ID selections and execute the graph in one [resource scope](../../plugins/runtime/#manage-resource-clients-and-credentials). They close provider resources after execution, including on failure.

Continue to [deployment](../../operate/deployment/) for setup or [graph execution](../graphs/) for table flow and reuse.
