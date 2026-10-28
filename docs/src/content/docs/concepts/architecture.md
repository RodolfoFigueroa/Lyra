---
title: Architecture and trust boundaries
description: Follow a contract from its plugin package through API discovery, worker execution, and result access.
contentType: Conceptual
contentPlan: concepts/architecture
sidebar: { label: "Architecture And Trust Boundaries" }
---

Lyra separates publishing a metric’s contract from executing its Python code. The API can read the catalog without importing plugins. Workers import trusted plugins and perform the calculations.

## Follow the two paths

Publishing makes a metric’s contract discoverable, while execution runs its calculation. The two paths share installed plugin packages but use them differently:

Publishing makes a metric’s contract discoverable, while execution runs its calculation. The two paths share installed plugin packages but use them differently:

```text
Plugin Python definitions
  → generated manifest
  → installed deployment package
      → API reads manifest → public metric catalog
      → worker imports factory → validated execution graph

Client → authenticated API → spatial resolution → Redis / Redis Queue (RQ)
  → worker executes graph → retained result → client download
```

## Publish the catalog

The deployment image contains Lyra and plugins installed from a locked dependency set. Operators select distributions and routes in `lyra.toml`.

At startup, the API reads installed manifests and validates their request schemas and spatial metadata. It publishes metric names, descriptions, schemas, outputs, spatial mappings, and a contract fingerprint.

The public fingerprint identifies the client-facing contract. Distribution names, factories, routing, and job state are operational details outside it.

Workers independently validate installed distribution versions and manifests. They import the roots they serve and their transitive dependencies, then compare live definitions with the saved contracts. They can start before the API; both obtain their catalog inputs from installed packages.

## Execute a job

An authenticated caller submits a metric and input. The API validates the schema, resolves spatial selectors, captures provenance and contract identity in Redis Queue (RQ) metadata, and queues resolved JSON inputs.

The selected warm worker constructs typed arguments and executes the graph sequentially. Dataset contexts provide acquisition services. Metrics receive their declared dependency tables and operational context.

The worker checks contract identity, validates native named table returns, and returns the result to RQ. RQ retains status and results. Clients observe the job and download its data before retention ends.

## Know each component’s responsibility

Each component owns a different part of discovery, execution, or result access:

| Component | Responsibility |
| --- | --- |
| FastAPI application | Discovery, request validation, spatial resolution, submission, result access, and administration |
| `lyra-sdk` | Plugin definitions, input/output contracts, graph execution, and local running |
| `lyra-api` | Synchronous/asynchronous HTTP clients and administrative CLI |
| `lyra-utils` | Geometry, date, and Earth Engine helpers |
| RQ workers | Trusted imports, execution, result validation, and job supervision |
| Redis | Queues, registries, metadata, and retained results |
| PostGIS | Database-backed spatial selection and dataset queries |
| MCP adapter | Agent tools using the same submission and result services |

## Understand the boundaries

Public routes expose health, metric schemas, and lookups. Job routes and MCP require the agent key. Administrator routes require a separate key.

A valid API catalog proves that a manifest can be read. It does not prove that a worker can import the package or reach its data sources. Check worker coverage and startup diagnostics as part of deployment verification.

Plugins execute trusted Python with worker permissions. Dataset declarations make acquisition explicit; they do not intercept filesystem or network access. Operators control installed packages, secrets, mounts, and network permissions.

The API owns its database runtime and shares it with HTTP and in-process MCP services. Database-backed selectors use that runtime. Explicit GeoJSON can follow the resolution path without opening a database connection for the input.

Continue to [deployment](../../operate/deployment/) for setup or [graph execution](../graphs/) for table flow and reuse.
